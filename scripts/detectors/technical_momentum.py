#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
technical_momentum.py — Price/Volume based signals (independent from on-chain)

Why this layer:
  · On-chain distribution shape for STRK gives ceiling of 66.7%
  · Rally_2, Rally_3 showed DISTRIBUTION on-chain, but STILL rallied
  · On-chain doesn't predict rallies — need other data class
  
Technical momentum uses ONLY price/volume:
  · 3-day price slope
  · 7-day volume vs 30-day volume (acceleration)
  · Higher-highs/lower-lows structure
  · RSI-like divergence
  · Distance from recent support/resistance
  · Volatility compression (Bollinger-like)

These are INDEPENDENT signals from distribution shape.
Combined with v2 on-chain, expected to reach 75%+ precision.

Backtest on 9 events + live signal computation.
"""

import os
import sys
import json
import math
import time
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
VALIDATION_DIR = SCRIPT_DIR / 'data' / 'validation'
OUTPUT_FILE = CACHE_DIR / 'technical_momentum.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('tech')

EVENTS = [
    {'name': 'Rally_1', 'date': '2024-11-05', 'type': 'RALLY', 'move': +135},
    {'name': 'Crash_1', 'date': '2024-12-07', 'type': 'CRASH', 'move': -86},
    {'name': 'Rally_2', 'date': '2025-11-03', 'type': 'RALLY', 'move': +175},
    {'name': 'Crash_2', 'date': '2025-11-20', 'type': 'CRASH', 'move': -88},
    {'name': 'Rally_3', 'date': '2026-04-14', 'type': 'RALLY', 'move': +99},
    {'name': 'Crash_3', 'date': '2026-05-09', 'type': 'CRASH', 'move': -56},
    {'name': 'Quiet_A', 'date': '2025-06-15', 'type': 'QUIET', 'move': 0},
    {'name': 'Quiet_B', 'date': '2026-01-20', 'type': 'QUIET', 'move': 0},
    {'name': 'Quiet_C', 'date': '2026-07-10', 'type': 'QUIET', 'move': 0},
]


def fetch_candles_at(inst_id, bar, end_ts_ms, days_needed):
    """Fetch historical candles ending at end_ts."""
    try:
        url = f'https://www.okx.com/api/v5/market/history-candles?instId={inst_id}&bar={bar}&before={end_ts_ms}&limit=300'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        candles = data.get('data', [])
        return list(reversed(candles))
    except Exception as e:
        logger.error(f"Candles error: {e}")
        return []


def fetch_current_candles(bar='4H', limit=200):
    try:
        url = f'https://www.okx.com/api/v5/market/candles?instId=STRK-USDT&bar={bar}&limit={limit}'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return list(reversed(data.get('data', [])))
    except Exception as e:
        return []


def compute_technical_features(candles):
    """Compute all technical features from candles.
    
    Assumes candles sorted oldest→newest, each = [ts, o, h, l, c, vol, ...]
    """
    if len(candles) < 30:
        return None
    
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    vols = [float(c[6]) for c in candles]
    
    # ---- Price slopes (on 4H candles: 3d = 18 candles, 7d = 42) ----
    n_3d = min(18, len(closes))
    n_7d = min(42, len(closes))
    n_14d = min(84, len(closes))
    
    slope_3d = (closes[-1] / closes[-n_3d] - 1) * 100 if closes[-n_3d] > 0 else 0
    slope_7d = (closes[-1] / closes[-n_7d] - 1) * 100 if closes[-n_7d] > 0 else 0
    slope_14d = (closes[-1] / closes[-n_14d] - 1) * 100 if closes[-n_14d] > 0 else 0
    
    # ---- Slope acceleration ----
    #
    # ФИКС 21.08.2026. Было: slope_3d - (slope_7d - slope_3d), то есть
    # 2*slope_3d - slope_7d. Это НЕ производная наклона, а линейная
    # комбинация двух перекрывающихся окон, и она систематически врёт:
    #
    #   ровный тренд  (+3 / +7)   → -1   «замедление» там, где его нет
    #   обвал        (-10 / -20)  →  0   «не отскакивает» на любом падении
    #
    # Второй случай кормил проверку not_bouncing в confluence_gate.
    # Стало: честное сравнение двух ПОСЛЕДОВАТЕЛЬНЫХ окон одной длины —
    # последние 3 дня против предыдущих 3 дней.
    if len(closes) >= 2 * n_3d and closes[-2 * n_3d] > 0:
        slope_prev_3d = (closes[-n_3d] / closes[-2 * n_3d] - 1) * 100
        slope_accel = slope_3d - slope_prev_3d      # positive = accelerating
        slope_accel_ok = True
    else:
        # Истории не хватает на два непересекающихся окна.
        # Ноль здесь был бы утверждением «ускорения нет» — а это неизвестно.
        slope_prev_3d = None
        slope_accel = None
        slope_accel_ok = False
    
    # ---- Volume analysis ----
    vol_3d = sum(vols[-n_3d:]) / n_3d
    vol_7d = sum(vols[-n_7d:]) / n_7d
    vol_30d = sum(vols[-min(180, len(vols)):]) / min(180, len(vols))
    
    vol_ratio_3d = vol_3d / max(vol_30d, 1)
    vol_ratio_7d = vol_7d / max(vol_30d, 1)
    vol_accel = vol_3d / max(vol_7d, 1)
    
    # ---- Range structure ----
    high_14d = max(highs[-n_14d:])
    low_14d = min(lows[-n_14d:])
    range_pct = (high_14d - low_14d) / low_14d * 100 if low_14d > 0 else 0
    pct_from_high = (closes[-1] / high_14d - 1) * 100 if high_14d > 0 else 0
    pct_from_low = (closes[-1] / low_14d - 1) * 100 if low_14d > 0 else 0
    
    # ---- Higher highs / lower lows ----
    recent_highs = highs[-n_7d:]
    prior_highs = highs[-n_14d:-n_7d] if len(highs) >= n_14d else recent_highs
    recent_max = max(recent_highs)
    prior_max = max(prior_highs)
    hh = recent_max > prior_max
    
    recent_lows = lows[-n_7d:]
    prior_lows = lows[-n_14d:-n_7d] if len(lows) >= n_14d else recent_lows
    recent_min = min(recent_lows)
    prior_min = min(prior_lows)
    hl = recent_min > prior_min
    
    ll = recent_min < prior_min
    lh = recent_max < prior_max
    
    if hh and hl: structure = 'UPTREND'
    elif ll and lh: structure = 'DOWNTREND'
    elif hh and ll: structure = 'VOLATILE'
    else: structure = 'RANGING'
    
    # ---- Compression (BB-like) ----
    mean_close_20 = sum(closes[-20:]) / 20
    variance = sum((c - mean_close_20) ** 2 for c in closes[-20:]) / 20
    std = math.sqrt(variance)
    bb_width_pct = (std * 2) / mean_close_20 * 100 if mean_close_20 > 0 else 0
    
    # ---- RSI-like momentum (14-period) ----
    n_rsi = min(14, len(closes) - 1)
    gains = [max(closes[i] - closes[i-1], 0) for i in range(-n_rsi, 0)]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(-n_rsi, 0)]
    avg_gain = sum(gains) / n_rsi if n_rsi > 0 else 0
    avg_loss = sum(losses) / n_rsi if n_rsi > 0 else 1
    rsi = 100 - (100 / (1 + avg_gain/max(avg_loss, 0.0001)))
    
    return {
        'price_now': closes[-1],
        'slope_3d_pct': round(slope_3d, 2),
        'slope_7d_pct': round(slope_7d, 2),
        'slope_14d_pct': round(slope_14d, 2),
        'slope_accel_pct': round(slope_accel, 2) if slope_accel_ok else None,
        'slope_accel_available': slope_accel_ok,
        'slope_prev_3d_pct': round(slope_prev_3d, 2) if slope_accel_ok else None,
        'vol_ratio_3d_vs_30d': round(vol_ratio_3d, 2),
        'vol_ratio_7d_vs_30d': round(vol_ratio_7d, 2),
        'vol_accel': round(vol_accel, 2),
        'high_14d': high_14d,
        'low_14d': low_14d,
        'range_pct': round(range_pct, 2),
        'pct_from_high': round(pct_from_high, 2),
        'pct_from_low': round(pct_from_low, 2),
        'structure': structure,
        'bb_width_pct': round(bb_width_pct, 2),
        'rsi': round(rsi, 2),
    }


def classify_technical(features):
    """Classify based on technical features."""
    if not features:
        return None
    
    votes = {'BULLISH': 0, 'BEARISH': 0}
    reasons = {'BULLISH': [], 'BEARISH': []}
    
    # Structure
    if features['structure'] == 'UPTREND':
        votes['BULLISH'] += 2
        reasons['BULLISH'].append('UPTREND (HH/HL)')
    elif features['structure'] == 'DOWNTREND':
        votes['BEARISH'] += 2
        reasons['BEARISH'].append('DOWNTREND (LH/LL)')
    
    # Slope
    if features['slope_3d_pct'] > 5:
        votes['BULLISH'] += 1
        reasons['BULLISH'].append(f'slope_3d {features["slope_3d_pct"]:+.1f}%')
    elif features['slope_3d_pct'] < -5:
        votes['BEARISH'] += 1
        reasons['BEARISH'].append(f'slope_3d {features["slope_3d_pct"]:+.1f}%')
    
    # Slope acceleration (very important for rally prediction!)
    # None означает «истории не хватило» — это не ноль и не голос.
    _accel = features.get('slope_accel_pct')
    if _accel is None:
        reasons.setdefault('NEUTRAL', []).append('slope accel: недостаточно истории')
    elif _accel > 3:
        votes['BULLISH'] += 2
        reasons['BULLISH'].append(f'slope accelerating +{_accel:.1f}%')
    elif _accel < -3:
        votes['BEARISH'] += 1
        reasons['BEARISH'].append('slope decelerating')
    
    # Volume
    if features['vol_ratio_3d_vs_30d'] > 1.5 and features['slope_3d_pct'] > 3:
        votes['BULLISH'] += 2
        reasons['BULLISH'].append(f'vol +{(features["vol_ratio_3d_vs_30d"]-1)*100:.0f}% with price up')
    elif features['vol_ratio_3d_vs_30d'] > 1.5 and features['slope_3d_pct'] < -3:
        votes['BEARISH'] += 2
        reasons['BEARISH'].append(f'vol +{(features["vol_ratio_3d_vs_30d"]-1)*100:.0f}% with price down')
    
    # Bollinger compression = potential breakout setup (either direction)
    # Won't vote here
    
    # RSI
    if features['rsi'] < 30:
        votes['BULLISH'] += 1
        reasons['BULLISH'].append(f'RSI {features["rsi"]:.0f} oversold')
    elif features['rsi'] > 70:
        votes['BEARISH'] += 1
        reasons['BEARISH'].append(f'RSI {features["rsi"]:.0f} overbought')
    
    # Off high / off low
    if features['pct_from_high'] < -20 and features['pct_from_low'] > 10:
        # Deep drop + partial bounce = post-capitulation
        votes['BULLISH'] += 2
        reasons['BULLISH'].append(f'post-capitulation: -{abs(features["pct_from_high"]):.0f}% from high, +{features["pct_from_low"]:.0f}% off low')
    
    if features['pct_from_high'] > -5 and features['vol_accel'] > 1.3:
        # Near high with rising volume = distribution
        votes['BEARISH'] += 1
        reasons['BEARISH'].append(f'near high with volume spike')
    
    if votes['BULLISH'] > votes['BEARISH']:
        signal = 'BULLISH'
        score = votes['BULLISH']
        winning_reasons = reasons['BULLISH']
    elif votes['BEARISH'] > votes['BULLISH']:
        signal = 'BEARISH'
        score = votes['BEARISH']
        winning_reasons = reasons['BEARISH']
    else:
        signal = 'NEUTRAL'
        score = 0
        winning_reasons = []
    
    return {
        'signal': signal,
        'score': score,
        'votes': votes,
        'reasons': winning_reasons,
    }


def backtest_technical():
    """Backtest technical momentum on 9 events."""
    logger.info("=" * 70)
    logger.info("TECHNICAL MOMENTUM BACKTEST")
    logger.info("=" * 70)
    
    hits = 0
    misses = 0
    results = []
    
    for event in EVENTS:
        logger.info(f"\n{'='*60}")
        logger.info(f"{event['name']} · {event['type']} · move {event['move']:+d}%")
        
        event_dt = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        end_ts_ms = int(event_dt.timestamp() * 1000)
        
        logger.info("  Fetching historical candles...")
        candles = fetch_candles_at('STRK-USDT', '4H', end_ts_ms, days_needed=30)
        time.sleep(0.5)
        
        if len(candles) < 30:
            logger.warning(f"  Only {len(candles)} candles, need 30+")
            continue
        
        features = compute_technical_features(candles)
        if not features:
            continue
        
        logger.info(f"  Price: ${features['price_now']:.4f}")
        logger.info(f"  Slope 3d: {features['slope_3d_pct']:+.2f}% · 7d: {features['slope_7d_pct']:+.2f}%")
        _a = features.get('slope_accel_pct')
        logger.info(f"  Slope accel: {_a:+.2f}%" if _a is not None
                    else "  Slope accel: нет данных (мало истории)")
        logger.info(f"  Vol ratio 3d: {features['vol_ratio_3d_vs_30d']}")
        logger.info(f"  Structure: {features['structure']} · RSI: {features['rsi']}")
        logger.info(f"  From high: {features['pct_from_high']:.1f}% · From low: {features['pct_from_low']:+.1f}%")
        
        classification = classify_technical(features)
        
        expected = None
        if event['type'] == 'RALLY': expected = 'BULLISH'
        elif event['type'] == 'CRASH': expected = 'BEARISH'
        else: expected = 'NEUTRAL'
        
        detected = classification['signal']
        
        if event['type'] == 'QUIET' and detected == 'NEUTRAL':
            outcome = 'HIT'
            hits += 1
        elif detected == expected:
            outcome = 'HIT'
            hits += 1
        else:
            outcome = 'MISS'
            misses += 1
        
        marker = "✅" if outcome == 'HIT' else "❌"
        logger.info(f"  DETECTED: {detected} · votes B:{classification['votes']['BULLISH']} vs B:{classification['votes']['BEARISH']}")
        for r in classification['reasons'][:3]:
            logger.info(f"    · {r}")
        logger.info(f"  {marker} {outcome}")
        
        results.append({
            'event': event['name'],
            'type': event['type'],
            'move': event['move'],
            'features': features,
            'detected': detected,
            'votes': classification['votes'],
            'reasons': classification['reasons'],
            'outcome': outcome,
        })
    
    total = len(results)
    accuracy = hits / total * 100 if total else 0
    
    logger.info(f"\n{'='*70}")
    logger.info(f"TECHNICAL MOMENTUM SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"Total: {hits}/{total} = {accuracy:.1f}%")
    logger.info(f"\nComparison:")
    logger.info(f"  v2 on-chain baseline:  66.7%")
    logger.info(f"  v3-v5 on-chain tries:  28-55%")
    logger.info(f"  Technical momentum:    {accuracy:.1f}%")
    
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'accuracy_pct': round(accuracy, 1),
        'hits': hits,
        'total': total,
        'events': results,
    }
    with open(VALIDATION_DIR / 'technical_momentum_backtest.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    return output


def main():
    stats = backtest_technical()
    
    # Live signal
    logger.info(f"\n{'='*70}")
    logger.info("LIVE TECHNICAL MOMENTUM")
    logger.info(f"{'='*70}")
    candles = fetch_current_candles()
    if candles and len(candles) >= 30:
        features = compute_technical_features(candles)
        classification = classify_technical(features)
        logger.info(f"Signal: {classification['signal']}")
        for r in classification['reasons']:
            logger.info(f"  · {r}")
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'as_of': datetime.now(timezone.utc).isoformat(),
                'features': features,
                'classification': classification,
                'backtest_stats': stats,
            }, f, indent=2, ensure_ascii=False)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
