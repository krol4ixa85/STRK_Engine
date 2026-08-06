#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyckoff_phase.py — Классификация фазы рынка по Wyckoff

Wyckoff Cycle:
  ACCUMULATION → MARKUP → DISTRIBUTION → MARKDOWN → повторение

Как определяем фазу on-chain + technical:

ACCUMULATION (умные деньги набирают, розница продаёт):
  · LARGE receivers 14d МАЛЕНЬКОЕ (< 30)
  · Distribution ratio ВЫСОКИЙ и растёт (> 0.30)
  · Custody wallets RECEIVING (не отправляют)
  · Funding negative или flat (шорты закрываются, лонги не активны)
  · Volume ПАДАЕТ (тихое накопление)
  · Price boxed in range (низкая волатильность)

MARKUP (рост после накопления):
  · Distribution ratio ВЫСОКИЙ и стабильный
  · Watchlist balance растёт
  · Funding нормальный (0-10% ann)
  · Volume expansion up
  · Higher highs, higher lows
  · BTC UP или NEUTRAL

DISTRIBUTION (умные сбрасывают, розница покупает):
  · LARGE receivers 14d ОЧЕНЬ БОЛЬШОЕ (> 50)
  · Distribution ratio ПАДАЕТ (< 0.10)
  · Custody → CEX flows растут
  · Funding positive и rising (лонги crowded)
  · Volume expansion на плоской цене (churn)
  · Топ формируется

MARKDOWN (падение после распределения):
  · Watchlist balance ПАДАЕТ
  · CEX inflows большие
  · Auto-rejected > auto-accepted
  · Funding neutral или slowly negative
  · Volume decline с price decline
  · Lower highs, lower lows

Дополнительно ловим:
  RE_ACCUMULATION — короткий откат внутри markup
  RE_DISTRIBUTION — короткий отскок внутри markdown
  SPRING — false breakdown в конце accumulation (bullish)
  UPTHRUST — false breakout в конце distribution (bearish)

Output:
  · current_phase
  · sub_phase (A/B/C/D по классическому Wyckoff schematic)
  · confidence (HIGH/MEDIUM/LOW)
  · reversal_triggers (что должно случиться для смены фазы)
  · layman_explanation (перевод на простой язык)
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'wyckoff_phase.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('wyckoff')


def load_json(path):
    if Path(path).exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_strk_technical():
    """Get STRK price levels, volume, momentum from OKX."""
    try:
        # 4h candles for medium-term structure
        url = 'https://www.okx.com/api/v5/market/candles?instId=STRK-USDT&bar=4H&limit=120'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        candles = list(reversed(data['data']))
        
        if len(candles) < 30:
            return None
        
        opens = [float(c[1]) for c in candles]
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]
        vols = [float(c[6]) for c in candles]
        
        price_now = closes[-1]
        
        # Ranges
        high_7d = max(highs[-42:])  # 42 * 4h = 7d
        low_7d = min(lows[-42:])
        high_14d = max(highs)  # 120 * 4h = 20d, close to 14d
        low_14d = min(lows)
        
        # Volume analysis
        avg_vol_30 = sum(vols[-30:]) / 30
        last_vol = vols[-1]
        vol_ratio = last_vol / avg_vol_30 if avg_vol_30 > 0 else 1
        recent_vol_trend = sum(vols[-10:]) / 10 / avg_vol_30 if avg_vol_30 > 0 else 1
        
        # Trend structure - simple higher highs / lower lows
        highs_5d = highs[-30:]
        lows_5d = lows[-30:]
        recent_high = max(highs_5d[-15:])
        prior_high = max(highs_5d[:15])
        recent_low = min(lows_5d[-15:])
        prior_low = min(lows_5d[:15])
        
        hh = recent_high > prior_high
        hl = recent_low > prior_low
        lh = recent_high < prior_high
        ll = recent_low < prior_low
        
        if hh and hl: structure = 'UPTREND'
        elif lh and ll: structure = 'DOWNTREND'
        elif hh and ll: structure = 'VOLATILE'
        elif lh and hl: structure = 'CONSOLIDATION'
        else: structure = 'SIDEWAYS'
        
        # Range compression
        range_now = (highs[-1] - lows[-1]) / closes[-1] * 100
        range_avg = sum((highs[i] - lows[i]) / closes[i] for i in range(-30, 0)) / 30 * 100
        compression = range_now / range_avg if range_avg > 0 else 1
        
        # Distance from range extremes
        pct_from_high_14d = (price_now / high_14d - 1) * 100
        pct_from_low_14d = (price_now / low_14d - 1) * 100
        
        # Clustered support/resistance
        sorted_highs = sorted(highs[-42:], reverse=True)
        sorted_lows = sorted(lows[-42:])
        
        return {
            'price_now': round(price_now, 4),
            'high_7d': round(high_7d, 4),
            'low_7d': round(low_7d, 4),
            'high_14d': round(high_14d, 4),
            'low_14d': round(low_14d, 4),
            'resistance_zones': [round(sorted_highs[0], 4), round(sorted_highs[3], 4), round(sorted_highs[7], 4)],
            'support_zones': [round(sorted_lows[0], 4), round(sorted_lows[3], 4), round(sorted_lows[7], 4)],
            'avg_vol_30': round(avg_vol_30, 2),
            'last_vol': round(last_vol, 2),
            'vol_ratio_last': round(vol_ratio, 2),
            'vol_trend_10': round(recent_vol_trend, 2),
            'structure': structure,
            'compression': round(compression, 2),
            'pct_from_high_14d': round(pct_from_high_14d, 2),
            'pct_from_low_14d': round(pct_from_low_14d, 2),
        }
    except Exception as e:
        logger.error(f"Technical error: {e}")
        return None


def classify_phase(technical, funding, distribution, btc, growth, patterns, concentration=None, effort=None, cvd=None, cex_flow=None):
    """Determine Wyckoff phase.
    
    IMPORTANT: v3 with HHI-primary failed backtest (33.3% vs v2 66.7%).
    v4 hybrid also failed (28.6%). Cause: HHI on STRK is ~0.02-0.11 always,
    doesn't distinguish phases. STRK fundamentally distributed on L1.
    
    Reverted to v2 baseline (66.7% proven accuracy). HHI/Eff/CVD used only
    as WEAK CONFIRMATORY signals, not primary voters.
    """
    if not technical:
        return None
    
    # Extract key metrics
    struct = technical['structure']
    vol_trend = technical['vol_trend_10']
    compression = technical['compression']
    pct_from_high = technical['pct_from_high_14d']
    pct_from_low = technical['pct_from_low_14d']
    
    large_14d = 0
    ratio_14d = 0
    if distribution:
        large_14d = (distribution.get('counts') or {}).get('LARGE', 0)
        ratio_14d = distribution.get('ratio_smallamt_over_largeamt', 0)
    
    # Secondary signals (weak weights)
    hhi = concentration.get('hhi', 0) if concentration else 0
    entropy = concentration.get('entropy_bits', 0) if concentration else 0
    eff_consensus = effort.get('consensus', 'MIXED') if effort else 'MIXED'
    cvd_consensus = cvd.get('consensus', 'MIXED') if cvd else 'MIXED'
    
    accepted_7d = growth.get('accepted_7d', 0) if growth else 0
    rejected_7d = growth.get('rejected_7d', 0) if growth else 0
    
    funding_metrics = funding.get('funding_metrics', {}) if funding else {}
    short_crowded = funding_metrics.get('short_crowded', False)
    long_crowded = funding_metrics.get('long_crowded', False)
    funding_avg_7d = funding_metrics.get('avg_7d_pct', 0)
    funding_min_7d = funding_metrics.get('min_ann_7d', 0)
    
    btc_cycle = btc.get('cycle', 'NEUTRAL') if btc else 'NEUTRAL'
    btc_dist200 = btc.get('dist200_pct', 0) if btc else 0
    btc_available = btc is not None and btc.get('cycle') != 'UNKNOWN'
    
    # === PHASE VOTING (v2 baseline, proven 66.7%) ===
    scores = {'ACCUMULATION': 0, 'MARKUP': 0, 'DISTRIBUTION': 0, 'MARKDOWN': 0}
    reasons = {'ACCUMULATION': [], 'MARKUP': [], 'DISTRIBUTION': [], 'MARKDOWN': []}
    
    # ---- CAPITULATION detection ----
    is_capitulation = (pct_from_high < -25 and pct_from_low > 5 and short_crowded)
    if is_capitulation:
        scores['ACCUMULATION'] += 3
        reasons['ACCUMULATION'].append(f'CAPITULATION/SPRING: -{abs(pct_from_high):.0f}% from high, +{pct_from_low:.0f}% off low')
    
    # ---- ACCUMULATION signals (v2 baseline) ----
    if large_14d < 40:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'few LARGE receivers ({large_14d})')
    if ratio_14d > 0.25:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'small holders active (ratio {ratio_14d:.3f})')
    if short_crowded and funding_min_7d < -10:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'extreme shorts (min {funding_min_7d:+.1f}%)')
    if struct in ('SIDEWAYS', 'CONSOLIDATION'):
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'price {struct.lower()}')
    if compression < 0.7 and vol_trend < 0.8:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append('range compression + volume dry')
    if accepted_7d >= 2 and rejected_7d < 2:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'{accepted_7d} new holders 7d')
    
    # ---- MARKUP signals ----
    if struct == 'UPTREND':
        scores['MARKUP'] += 2
        reasons['MARKUP'].append('uptrend structure (HH/HL)')
    if vol_trend > 1.2 and struct == 'UPTREND':
        scores['MARKUP'] += 2
        reasons['MARKUP'].append('volume expanding with trend')
    if btc_cycle in ('UP', 'DOWN_REVERSING'):
        scores['MARKUP'] += 1
        reasons['MARKUP'].append(f'BTC {btc_cycle}')
    if pct_from_low > 20 and pct_from_high > -10:
        scores['MARKUP'] += 1
        reasons['MARKUP'].append(f'close to highs')
    if 0.20 < ratio_14d < 0.50 and 20 < large_14d < 60:
        scores['MARKUP'] += 1
        reasons['MARKUP'].append('healthy distribution during trend')
    
    # ---- DISTRIBUTION signals (v2 baseline) ----
    if large_14d > 100:
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append(f'many LARGE receivers ({large_14d})')
    elif large_14d > 60:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'elevated LARGE ({large_14d})')
    if ratio_14d < 0.02:
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append(f'no retail activity (ratio {ratio_14d:.4f})')
    if long_crowded or funding_avg_7d > 8:
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append(f'long-crowded ({funding_avg_7d:+.1f}%)')
    if pct_from_high > -5 and vol_trend > 1.3:
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append('high volume near range high')
    if struct == 'VOLATILE' and pct_from_high > -10:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append('choppy near highs')
    
    # ---- MARKDOWN signals ----
    if struct == 'DOWNTREND':
        if pct_from_low > 10:
            scores['MARKDOWN'] += 1
            reasons['MARKDOWN'].append(f'downtrend but +{pct_from_low:.0f}% off low')
        else:
            scores['MARKDOWN'] += 2
            reasons['MARKDOWN'].append('downtrend structure')
    if pct_from_high < -20 and pct_from_low < 3:
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append(f'{pct_from_high:.0f}% from high, no bounce')
    if vol_trend > 1.5 and struct == 'DOWNTREND':
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append('capitulation volume')
    if btc_cycle == 'DOWN' and btc_dist200 < -12:
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append('BTC deep down-cycle')
    if rejected_7d > accepted_7d + 2:
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append('more rejects than accepts')
    
    # ============================================================
    # v3 SECONDARY SIGNALS (weak confirmatory, +1 each max)
    # HHI/Eff/CVD proven to fail backtest as primary voters.
    # Kept only for weak confluence check.
    # ============================================================
    if cvd_consensus == 'DISTRIBUTION_DIVERGENCE':
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append('CVD divergence (weak confirmatory)')
    elif cvd_consensus == 'ACCUMULATION_DIVERGENCE':
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append('CVD divergence (weak confirmatory)')
    
    if eff_consensus == 'DISTRIBUTION':
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append('Effort/Result absorption (weak)')
    elif eff_consensus == 'MARKUP':
        scores['MARKUP'] += 1
        reasons['MARKUP'].append('Effort/Result markup pattern')
    elif eff_consensus == 'MARKDOWN':
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append('Effort/Result markdown pattern')
    
    # ============================================================
    # NEW: CEX FLOW (weak +1, ortogonal signal)
    # Backtest showed 44.4% alone, so weak weight. But orthogonal
    # to distribution shape - useful confluence.
    # ============================================================
    if cex_flow:
        cex_cls = cex_flow.get('classification', {})
        cex_signal = cex_cls.get('signal', 'NEUTRAL')
        cex_conf = cex_cls.get('confidence', 'LOW')
        
        if cex_signal == 'STRONG_DISTRIBUTION' and cex_conf == 'HIGH':
            scores['DISTRIBUTION'] += 2
            reasons['DISTRIBUTION'].append(f'CEX inflows 3+ days consecutive (confluence)')
        elif cex_signal == 'MILD_DISTRIBUTION':
            scores['DISTRIBUTION'] += 1
            reasons['DISTRIBUTION'].append(f'CEX net inflow (weak confluence)')
        elif cex_signal == 'STRONG_ACCUMULATION' and cex_conf == 'HIGH':
            scores['ACCUMULATION'] += 2
            reasons['ACCUMULATION'].append(f'CEX outflows 3+ days consecutive (whales pulling)')
        elif cex_signal == 'MILD_ACCUMULATION':
            scores['ACCUMULATION'] += 1
            reasons['ACCUMULATION'].append(f'CEX net outflow (weak confluence)')
    
    # === Determine winner ===
    winner = max(scores.items(), key=lambda x: x[1])
    phase = winner[0]
    score = winner[1]
    
    if score >= 5:
        confidence = 'HIGH'
    elif score >= 3:
        confidence = 'MEDIUM'
    else:
        confidence = 'LOW'
    
    if not btc_available and confidence == 'HIGH':
        confidence = 'MEDIUM'
        reasons[phase].append('confidence reduced: BTC context unknown')
    
    # Sub-phase
    sub_phase = None
    if phase == 'DISTRIBUTION':
        if pct_from_high > -3:
            sub_phase = 'Phase A · initial supply testing highs'
        elif -8 < pct_from_high <= -3:
            sub_phase = 'Phase B · building the cause'
        elif -15 < pct_from_high <= -8:
            sub_phase = 'Phase C · Sign of Weakness'
        else:
            sub_phase = 'Phase D · markdown starting'
    elif phase == 'ACCUMULATION':
        if is_capitulation:
            sub_phase = 'Phase C · Spring / capitulation'
        elif pct_from_low < 5:
            sub_phase = 'Phase A · initial demand'
        elif 5 <= pct_from_low < 12:
            sub_phase = 'Phase B · building base'
        elif 12 <= pct_from_low < 20:
            sub_phase = 'Phase C · Spring test'
        else:
            sub_phase = 'Phase D · Sign of Strength'
    elif phase == 'MARKUP':
        sub_phase = 'Trend continuation up' if struct == 'UPTREND' else 'Early breakout'
    elif phase == 'MARKDOWN':
        sub_phase = 'Trend continuation down' if struct == 'DOWNTREND' else 'Failed bounce'
    
    return {
        'phase': phase,
        'sub_phase': sub_phase,
        'confidence': confidence,
        'score': score,
        'all_scores': scores,
        'reasons': reasons[phase],
        'all_reasons': reasons,
        'metrics_used': {
            'hhi': hhi,
            'entropy': entropy,
            'effort_consensus': eff_consensus,
            'cvd_consensus': cvd_consensus,
        },
        'calibration_note': 'v2 baseline (66.7% proven). HHI/Eff/CVD backtest-failed as primary, used only weak confirmatory.',
    }
    if not technical:
        return None
    
    # Extract key metrics
    struct = technical['structure']
    vol_trend = technical['vol_trend_10']
    compression = technical['compression']
    pct_from_high = technical['pct_from_high_14d']
    pct_from_low = technical['pct_from_low_14d']
    
    large_14d = 0
    ratio_14d = 0
    if distribution:
        large_14d = (distribution.get('counts') or {}).get('LARGE', 0)
        ratio_14d = distribution.get('ratio_smallamt_over_largeamt', 0)
    
    # === NEW: HHI concentration ===
    hhi = concentration.get('hhi', 0) if concentration else 0
    entropy = concentration.get('entropy_bits', 0) if concentration else 0
    conc_signal = concentration.get('concentration_signal', 'NEUTRAL') if concentration else 'NEUTRAL'
    conc_large = concentration.get('large_count', 0) if concentration else 0
    top_5_share = concentration.get('top_5_share_pct', 0) if concentration else 0
    
    # === NEW: Effort/Result ===
    eff_consensus = effort.get('consensus', 'MIXED') if effort else 'MIXED'
    eff_dist = effort.get('distribution_signals', 0) if effort else 0
    eff_acc = effort.get('accumulation_signals', 0) if effort else 0
    eff_markup = effort.get('markup_signals', 0) if effort else 0
    eff_markdown = effort.get('markdown_signals', 0) if effort else 0
    
    # === NEW: CVD ===
    cvd_consensus = cvd.get('consensus', 'MIXED') if cvd else 'MIXED'
    
    accepted_7d = growth.get('accepted_7d', 0) if growth else 0
    rejected_7d = growth.get('rejected_7d', 0) if growth else 0
    
    funding_metrics = funding.get('funding_metrics', {}) if funding else {}
    short_crowded = funding_metrics.get('short_crowded', False)
    long_crowded = funding_metrics.get('long_crowded', False)
    funding_avg_7d = funding_metrics.get('avg_7d_pct', 0)
    funding_min_7d = funding_metrics.get('min_ann_7d', 0)
    
    btc_cycle = btc.get('cycle', 'NEUTRAL') if btc else 'NEUTRAL'
    btc_dist200 = btc.get('dist200_pct', 0) if btc else 0
    btc_available = btc is not None and btc.get('cycle') != 'UNKNOWN'
    
    # === PHASE VOTING (v3 with new metrics) ===
    scores = {'ACCUMULATION': 0, 'MARKUP': 0, 'DISTRIBUTION': 0, 'MARKDOWN': 0}
    reasons = {'ACCUMULATION': [], 'MARKUP': [], 'DISTRIBUTION': [], 'MARKDOWN': []}
    
    # ============================================================
    # PRIMARY: HHI-based concentration signal (weight 3-4)
    # ============================================================
    if hhi >= 0.25 and conc_large >= 20:
        scores['ACCUMULATION'] += 3
        reasons['ACCUMULATION'].append(f'HHI {hhi:.3f} concentrated ({conc_large} large receivers)')
    elif hhi >= 0.20 and conc_large >= 15:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'HHI {hhi:.3f} moderately concentrated')
    
    if hhi < 0.08 and conc_large >= 50:
        scores['DISTRIBUTION'] += 4
        reasons['DISTRIBUTION'].append(f'HHI {hhi:.3f} very diluted ({conc_large} receivers) — capital fragmenting')
    elif hhi < 0.12 and conc_large >= 40:
        scores['DISTRIBUTION'] += 3
        reasons['DISTRIBUTION'].append(f'HHI {hhi:.3f} diluted + many receivers')
    
    # Top-5 concentration
    if top_5_share > 60 and conc_large >= 15:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'Top 5 hold {top_5_share:.0f}% (very concentrated)')
    elif top_5_share < 25 and conc_large >= 40:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'Top 5 only {top_5_share:.0f}% (diluted)')
    
    # Entropy cross-check
    if entropy < 3.0 and conc_large >= 20:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'Low entropy {entropy:.2f} (concentrated)')
    elif entropy > 5.0 and conc_large >= 40:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'High entropy {entropy:.2f} (diluted)')
    
    # ============================================================
    # PRIMARY: Effort/Result (weight 2-3)
    # ============================================================
    if eff_consensus == 'DISTRIBUTION' or eff_dist >= 2:
        scores['DISTRIBUTION'] += 3
        reasons['DISTRIBUTION'].append('Effort/Result multi-TF: absorption pattern (vol up, price flat)')
    elif eff_consensus == 'ACCUMULATION' or eff_acc >= 2:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append('Effort/Result: quiet phase (vol low, price flat)')
    elif eff_consensus == 'MARKUP' or eff_markup >= 2:
        scores['MARKUP'] += 3
        reasons['MARKUP'].append('Effort/Result: healthy trend up (vol + price both rising)')
    elif eff_consensus == 'MARKDOWN' or eff_markdown >= 2:
        scores['MARKDOWN'] += 2
        reasons['MARKDOWN'].append('Effort/Result: capitulation (vol up, price down)')
    
    # ============================================================
    # PRIMARY: CVD divergence (weight 2)
    # ============================================================
    if 'DISTRIBUTION' in cvd_consensus:
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append('CVD bearish divergence (price up, delta down = retail buying, smart selling)')
    elif 'ACCUMULATION' in cvd_consensus:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append('CVD bullish divergence (price down, delta up = retail selling, smart buying)')
    elif cvd_consensus == 'BEARISH_LEAN':
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append('CVD leaning bearish')
    elif cvd_consensus == 'BULLISH_LEAN':
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append('CVD leaning bullish')
    
    # ============================================================
    # CAPITULATION / SPRING (bullish reversal)
    # ============================================================
    is_capitulation = (pct_from_high < -25 and pct_from_low > 5 and short_crowded)
    if is_capitulation:
        scores['ACCUMULATION'] += 3
        reasons['ACCUMULATION'].append(f'CAPITULATION/SPRING: -{abs(pct_from_high):.0f}% from high, +{pct_from_low:.0f}% off low')
    
    # ============================================================
    # SECONDARY: Distribution shape (original metrics, reduced weight now)
    # ============================================================
    if large_14d < 40 and ratio_14d > 0.20:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'few LARGE, retail active')
    
    if large_14d > 100 and ratio_14d < 0.02:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'many LARGE + no retail')
    
    # Short-crowded
    if short_crowded and funding_min_7d < -10:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'extreme shorts ({funding_min_7d:+.1f}%)')
    if long_crowded or funding_avg_7d > 8:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'long-crowded ({funding_avg_7d:+.1f}%)')
    
    # ============================================================
    # STRUCTURE signals
    # ============================================================
    if struct == 'UPTREND':
        scores['MARKUP'] += 2
        reasons['MARKUP'].append('uptrend structure')
    elif struct == 'DOWNTREND':
        if pct_from_low > 10:
            scores['MARKDOWN'] += 1  # weakened if bouncing
            reasons['MARKDOWN'].append(f'downtrend but +{pct_from_low:.0f}% off low')
        else:
            scores['MARKDOWN'] += 2
            reasons['MARKDOWN'].append('downtrend structure')
    elif struct == 'VOLATILE' and pct_from_high > -10:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append('choppy near highs')
    elif struct in ('SIDEWAYS', 'CONSOLIDATION'):
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'price {struct.lower()}')
    
    # BTC context
    if btc_cycle in ('UP', 'DOWN_REVERSING'):
        scores['MARKUP'] += 1
        reasons['MARKUP'].append(f'BTC {btc_cycle}')
    if btc_cycle == 'DOWN' and btc_dist200 < -12:
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append('BTC deep down-cycle')
    
    # Discovery signals
    if accepted_7d >= 2 and rejected_7d < 2:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'{accepted_7d} new holders 7d')
    if rejected_7d > accepted_7d + 2:
        scores['MARKDOWN'] += 1
        reasons['MARKDOWN'].append('more rejects than accepts')
    
    # === Determine winner ===
    winner = max(scores.items(), key=lambda x: x[1])
    phase = winner[0]
    score = winner[1]
    
    # Confidence based on new higher weights
    if score >= 8:
        confidence = 'HIGH'
    elif score >= 5:
        confidence = 'MEDIUM'
    else:
        confidence = 'LOW'
    
    # Auto-degrade if key metrics missing
    if not concentration and confidence == 'HIGH':
        confidence = 'MEDIUM'
        reasons[phase].append('confidence reduced: HHI data missing')
    if not btc_available and confidence != 'LOW':
        reasons[phase].append('note: BTC context uncertain')
    
    # Sub-phase
    sub_phase = None
    if phase == 'DISTRIBUTION':
        if pct_from_high > -3:
            sub_phase = 'Phase A · initial supply testing highs'
        elif -8 < pct_from_high <= -3:
            sub_phase = 'Phase B · building the cause (churn)'
        elif -15 < pct_from_high <= -8:
            sub_phase = 'Phase C · Sign of Weakness (SOW)'
        else:
            sub_phase = 'Phase D · markdown starting'
    elif phase == 'ACCUMULATION':
        if is_capitulation:
            sub_phase = 'Phase C · Spring / capitulation'
        elif pct_from_low < 5:
            sub_phase = 'Phase A · initial demand at lows'
        elif 5 <= pct_from_low < 12:
            sub_phase = 'Phase B · building base'
        elif 12 <= pct_from_low < 20:
            sub_phase = 'Phase C · Spring test'
        else:
            sub_phase = 'Phase D · Sign of Strength (SOS)'
    elif phase == 'MARKUP':
        sub_phase = 'Trend continuation up' if struct == 'UPTREND' else 'Early breakout'
    elif phase == 'MARKDOWN':
        sub_phase = 'Trend continuation down' if struct == 'DOWNTREND' else 'Failed bounce'
    
    return {
        'phase': phase,
        'sub_phase': sub_phase,
        'confidence': confidence,
        'score': score,
        'all_scores': scores,
        'reasons': reasons[phase],
        'all_reasons': reasons,
        'metrics_used': {
            'hhi': hhi,
            'entropy': entropy,
            'concentration_signal': conc_signal,
            'effort_consensus': eff_consensus,
            'cvd_consensus': cvd_consensus,
        },
        'calibration_note': 'v3 with HHI + Effort/Result + CVD divergence',
    }


def build_reversal_triggers(phase_data, technical, distribution, funding, btc):
    """What must happen for phase to change."""
    if not phase_data:
        return {}
    
    phase = phase_data['phase']
    triggers = {'to_next_phase': [], 'exit_signals': []}
    
    price = technical['price_now'] if technical else 0
    high_7d = technical['high_7d'] if technical else 0
    low_7d = technical['low_7d'] if technical else 0
    high_14d = technical['high_14d'] if technical else 0
    low_14d = technical['low_14d'] if technical else 0
    
    large_14d = (distribution.get('counts') or {}).get('LARGE', 0) if distribution else 0
    ratio_14d = distribution.get('ratio_smallamt_over_largeamt', 0) if distribution else 0
    funding_metrics = funding.get('funding_metrics', {}) if funding else {}
    
    if phase == 'DISTRIBUTION':
        triggers['next_phase'] = 'MARKDOWN'
        triggers['to_next_phase'] = [
            (f'Break BELOW ${low_14d:.4f} on high volume', price < low_14d * 1.02),
            (f'LARGE receivers 14d drop under 30 (now {large_14d})', large_14d < 30),
            (f'Funding turn extreme positive >15% ann', funding_metrics.get('current_annualized_pct', 0) > 15),
            (f'Custody→CEX flows 3+ days in a row', False),
        ]
        triggers['reversal_to_accumulation'] = [
            f'LARGE receivers 14d drop under 30 (now {large_14d})',
            f'Distribution ratio above 0.30 (now {ratio_14d:.3f})',
            f'Price stays above ${low_14d:.4f} for 7 days',
            'Funding turns extreme negative -10%+',
        ]
        triggers['exit_signals'] = [
            f'If long: close above ${high_7d:.4f} (+{(high_7d/price-1)*100:.1f}%) or below ${low_7d*0.98:.4f} (stop)',
            f'If flat: STAY FLAT, distribution active',
            f'If want short: wait for break below ${low_7d:.4f} with volume',
        ]
    
    elif phase == 'MARKDOWN':
        triggers['next_phase'] = 'ACCUMULATION'
        triggers['to_next_phase'] = [
            (f'Price stops making new lows for 7+ days', True),
            (f'Funding extreme negative 5+ fundings row', False),
            (f'Auto-rejected count drops', False),
            (f'Watchlist balance stops decreasing', True),
        ]
        triggers['exit_signals'] = [
            f'If short: cover below ${low_14d*0.9:.4f} (capitulation) or above ${low_14d:.4f} (invalidation)',
            f'If flat: DO NOT LONG YET, wait for accumulation confirmation',
        ]
    
    elif phase == 'ACCUMULATION':
        triggers['next_phase'] = 'MARKUP'
        triggers['to_next_phase'] = [
            (f'Break ABOVE ${high_7d:.4f} on volume', price > high_7d * 0.98),
            (f'Distribution ratio holds above 0.30', ratio_14d > 0.30),
            (f'BTC breaks up-cycle', False),
            (f'Volume expansion up 2+ candles', False),
        ]
        triggers['exit_signals'] = [
            f'If flat: consider LONG on break above ${high_7d:.4f} with stop at ${low_7d:.4f}',
            f'Position sizing: small (accumulation not confirmed until markup)',
        ]
    
    elif phase == 'MARKUP':
        triggers['next_phase'] = 'DISTRIBUTION'
        triggers['to_next_phase'] = [
            (f'LARGE receivers 14d spike above 50 (now {large_14d})', large_14d > 50),
            (f'Funding turn extreme positive >20% ann', False),
            (f'Volume expansion but price flat', False),
            (f'Custody sending to CEX', False),
        ]
        triggers['exit_signals'] = [
            f'If long: trail stop below prior HL, take profit at ${high_14d*1.15:.4f}',
            f'Watch for exhaustion on high volume',
        ]
    
    return triggers


def build_layman_explanation(phase_data, technical, funding, distribution):
    """Layman-friendly explanation."""
    if not phase_data:
        return "Not enough data to determine phase."
    
    phase = phase_data['phase']
    
    explanations = {
        'ACCUMULATION': (
            "Фаза НАКОПЛЕНИЯ. Крупные игроки тихо покупают токены у розницы. "
            "Цена в боковике, объём падает. Это до-стадия роста. "
            "Обычно длится 2-8 недель. Ничего покупать пока не время — "
            "жди пробоя вверх с объёмом."
        ),
        'MARKUP': (
            "Фаза РОСТА. Умные деньги набрали, теперь рынок растёт. "
            "Retail покупает на пике эйфории. Тренд твой друг — "
            "держи long, трейл стоп по higher lows. Выходи когда объём растёт "
            "а цена стагнирует (это начало distribution)."
        ),
        'DISTRIBUTION': (
            "Фаза РАСПРЕДЕЛЕНИЯ. Крупные держатели активно СБРАСЫВАЮТ токены "
            "розничным покупателям. Розница видит рост и покупает — "
            "получает мешок от китов. Это последняя фаза перед падением. "
            "НЕ покупать. Если есть позиция — уменьшать."
        ),
        'MARKDOWN': (
            "Фаза ПАДЕНИЯ. Разгружаются lonely holders, паника. "
            "Цена делает lower lows. Не ловить нож. Ждать капитуляцию: "
            "экстремальные шорт-funding'и, объём выше среднего в 3-5×, "
            "цена стоит 7+ дней без новых минимумов."
        ),
    }
    
    base = explanations.get(phase, "Фаза неопределена.")
    
    # Add context from funding
    funding_metrics = funding.get('funding_metrics', {}) if funding else {}
    if funding_metrics.get('short_crowded'):
        base += "\n\nВАЖНО: сейчас в системе много ШОРТОВ (funding negative). Это топливо для короткого squeeze rally. Может быть +5-15% движение но НЕ разворот тренда."
    if funding_metrics.get('long_crowded'):
        base += "\n\nВАЖНО: сейчас в системе много ЛОНГОВ (funding extreme positive). Это топливо для быстрого крэша при первом шоке."
    
    return base


def run_analysis():
    """Load all inputs and produce phase classification."""
    logger.info("Loading inputs...")
    
    technical = get_strk_technical()
    if technical:
        logger.info(f"  STRK ${technical['price_now']} · {technical['structure']} · vol_ratio {technical['vol_ratio_last']}")
    
    funding = load_json(CACHE_DIR / 'funding_signal.json')
    if funding:
        fm = funding.get('funding_metrics', {})
        logger.info(f"  Funding: extreme={fm.get('extreme')}, short_crowded={fm.get('short_crowded')}")
    
    composite = load_json(CACHE_DIR / 'composite_signal_v2.json') or {}
    inputs = composite.get('inputs') or {}
    distribution = inputs.get('distribution')
    btc = inputs.get('btc_context')
    if distribution:
        logger.info(f"  Distribution: {(distribution.get('counts') or {}).get('LARGE', 0)} LARGE, ratio {distribution.get('ratio_smallamt_over_largeamt')}")
    
    cross_window = load_json(CACHE_DIR / 'cross_window_pattern.json') or {}
    growth = cross_window.get('watchlist_growth') or {}
    patterns = cross_window.get('patterns_detected') or []
    
    concentration = load_json(CACHE_DIR / 'concentration_metrics.json')
    if concentration:
        logger.info(f"  HHI: {concentration.get('hhi')}, Signal: {concentration.get('concentration_signal')}")
    
    effort = load_json(CACHE_DIR / 'effort_result.json')
    if effort:
        logger.info(f"  Effort/Result: {effort.get('consensus')}")
    
    cvd = load_json(CACHE_DIR / 'cvd_analysis.json')
    if cvd:
        logger.info(f"  CVD: {cvd.get('consensus')}")
    
    # NEW: Load CEX flow
    cex_flow = load_json(CACHE_DIR / 'cex_flow.json')
    if cex_flow:
        cls = cex_flow.get('classification', {})
        logger.info(f"  CEX flow: {cls.get('signal')} · {cls.get('confidence')}")
    
    logger.info("\nClassifying phase (v2 baseline + weak confirmatory)...")
    phase_data = classify_phase(technical, funding, distribution, btc, growth, patterns,
                                concentration=concentration, effort=effort, cvd=cvd, cex_flow=cex_flow)
    
    if not phase_data:
        logger.error("Could not classify")
        return None
    
    logger.info(f"  Phase: {phase_data['phase']} · {phase_data['confidence']}")
    logger.info(f"  Score: {phase_data['score']}, all scores: {phase_data['all_scores']}")
    
    triggers = build_reversal_triggers(phase_data, technical, distribution, funding, btc)
    layman = build_layman_explanation(phase_data, technical, funding, distribution)
    
    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'phase': phase_data['phase'],
        'sub_phase': phase_data['sub_phase'],
        'confidence': phase_data['confidence'],
        'score': phase_data['score'],
        'all_scores': phase_data['all_scores'],
        'reasons': phase_data['reasons'],
        'technical': technical,
        'triggers': triggers,
        'layman_explanation': layman,
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    
    return result


def main():
    logger.info("=" * 70)
    logger.info("WYCKOFF PHASE DETECTOR")
    logger.info("=" * 70)
    
    result = run_analysis()
    if not result:
        return 1
    
    logger.info("\n" + "=" * 70)
    logger.info(f"PHASE: {result['phase']} · {result['confidence']}")
    if result.get('sub_phase'):
        logger.info(f"Sub-phase: {result['sub_phase']}")
    logger.info("=" * 70)
    logger.info("\nReasons:")
    for r in result['reasons']:
        logger.info(f"  · {r}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
