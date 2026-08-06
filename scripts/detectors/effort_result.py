#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
effort_result.py — Wyckoff Effort/Result metric

Формула: Eff = |ΔPrice| / Volume

Интерпретация:
  · Volume огромный + Eff → 0 (цена не двигается): ABSORPTION / DISTRIBUTION
    (крупные игроки поглощают/сдерживают своими лимитными ордерами)
  · Volume средний + Eff высокий (цена летит): ACCUMULATION_COMPLETE / MARKUP
    (сопротивления нет, стакан пустой)
  · Volume маленький + Eff низкий: спокойная фаза (ACCUMULATION или sideways)
  · Volume + Price rising together: healthy trend (MARKUP)

Считаю на 3 таймфреймах: 1h, 4h, 12h — ищу дивергенции.
"""

import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'effort_result.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('eff')


def fetch_candles(inst_id, bar, limit=100):
    try:
        url = f'https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return list(reversed(data.get('data', [])))
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return []


def compute_effort_result(candles, label):
    """Compute Eff for a given timeframe."""
    if len(candles) < 20:
        return None
    
    # Recent window (last 20 candles) vs prior window (20 before)
    recent = candles[-20:]
    prior = candles[-40:-20] if len(candles) >= 40 else recent
    
    def stats(window):
        opens = [float(c[1]) for c in window]
        closes = [float(c[4]) for c in window]
        vols = [float(c[6]) for c in window]  # in base currency
        highs = [float(c[2]) for c in window]
        lows = [float(c[3]) for c in window]
        
        # Absolute price movement
        price_range = max(highs) - min(lows)
        net_move = closes[-1] - opens[0]
        
        # Volume total
        vol_total = sum(vols)
        
        # Efficiency: absolute price change per unit volume
        # Normalized to make comparable
        if vol_total > 0 and closes[-1] > 0:
            eff_abs = abs(net_move) / closes[-1] / (vol_total / len(window)) * 1e6
            eff_range = price_range / closes[-1] / (vol_total / len(window)) * 1e6
        else:
            eff_abs = eff_range = 0
        
        return {
            'net_move_pct': (net_move / opens[0] * 100) if opens[0] else 0,
            'range_pct': (price_range / opens[0] * 100) if opens[0] else 0,
            'vol_total': vol_total,
            'vol_avg': vol_total / len(window),
            'eff_abs': eff_abs,
            'eff_range': eff_range,
        }
    
    recent_stats = stats(recent)
    prior_stats = stats(prior)
    
    # Direction of price
    direction = 'UP' if recent_stats['net_move_pct'] > 0.5 else ('DOWN' if recent_stats['net_move_pct'] < -0.5 else 'FLAT')
    
    # Volume trend
    vol_ratio = recent_stats['vol_avg'] / prior_stats['vol_avg'] if prior_stats['vol_avg'] > 0 else 1
    
    # Effort ratio: is efficiency dropping while volume rising?
    eff_ratio = recent_stats['eff_abs'] / prior_stats['eff_abs'] if prior_stats['eff_abs'] > 0 else 1
    
    # === CLASSIFICATION ===
    signal = 'NEUTRAL'
    interpretation = ''
    
    if vol_ratio > 1.5 and eff_ratio < 0.5:
        # Volume up but efficiency down — classic distribution
        signal = 'ABSORPTION_DISTRIBUTION'
        interpretation = f'Vol +{(vol_ratio-1)*100:.0f}% but Eff -{(1-eff_ratio)*100:.0f}% — market absorbing (distribution)'
    elif vol_ratio > 1.3 and abs(recent_stats['net_move_pct']) < 1.0:
        # Volume up but price flat — churn
        signal = 'CHURN'
        interpretation = f'Vol +{(vol_ratio-1)*100:.0f}% but price flat — churn (potential distribution)'
    elif vol_ratio < 0.8 and abs(recent_stats['net_move_pct']) > 3:
        # Volume down but price moving — thin market
        signal = 'THIN_MARKET_MOVE'
        interpretation = f'Vol -{(1-vol_ratio)*100:.0f}% but +{recent_stats["net_move_pct"]:.1f}% move — thin order book'
    elif vol_ratio > 1.2 and recent_stats['net_move_pct'] > 3 and direction == 'UP':
        # Volume + Price both rising — healthy trend
        signal = 'HEALTHY_MARKUP'
        interpretation = f'Vol +{(vol_ratio-1)*100:.0f}% AND +{recent_stats["net_move_pct"]:.1f}% — healthy trend up'
    elif vol_ratio > 1.2 and recent_stats['net_move_pct'] < -3 and direction == 'DOWN':
        # Volume up + price down — capitulation or markdown
        signal = 'MARKDOWN_CAPITULATION'
        interpretation = f'Vol +{(vol_ratio-1)*100:.0f}% AND {recent_stats["net_move_pct"]:.1f}% — capitulation'
    elif vol_ratio < 0.7 and abs(recent_stats['net_move_pct']) < 2:
        # Volume dry + price flat — quiet accumulation phase
        signal = 'QUIET_ACCUMULATION'
        interpretation = f'Vol -{(1-vol_ratio)*100:.0f}% + price flat — quiet phase (accumulation)'
    
    return {
        'timeframe': label,
        'direction': direction,
        'net_move_pct': round(recent_stats['net_move_pct'], 2),
        'vol_ratio_recent_vs_prior': round(vol_ratio, 2),
        'eff_recent': round(recent_stats['eff_abs'], 4),
        'eff_prior': round(prior_stats['eff_abs'], 4),
        'eff_ratio': round(eff_ratio, 2),
        'signal': signal,
        'interpretation': interpretation,
    }


def main():
    logger.info("=" * 60)
    logger.info("EFFORT/RESULT · Wyckoff efficiency analysis")
    logger.info("=" * 60)
    
    results = {}
    
    for tf_label, bar, limit in [('1h', '1H', 100), ('4h', '4H', 60), ('12h', '12H', 40)]:
        logger.info(f"\nFetching {tf_label} candles...")
        candles = fetch_candles('STRK-USDT', bar, limit)
        if not candles:
            continue
        
        r = compute_effort_result(candles, tf_label)
        if r:
            results[tf_label] = r
            logger.info(f"  {tf_label}: {r['signal']} · vol_ratio {r['vol_ratio_recent_vs_prior']} · eff_ratio {r['eff_ratio']}")
            if r['interpretation']:
                logger.info(f"      {r['interpretation']}")
    
    # === Multi-TF consensus ===
    signals = [r['signal'] for r in results.values() if r]
    
    dist_signals = ['ABSORPTION_DISTRIBUTION', 'CHURN']
    acc_signals = ['QUIET_ACCUMULATION']
    markup_signals = ['HEALTHY_MARKUP']
    markdown_signals = ['MARKDOWN_CAPITULATION']
    
    dist_count = sum(1 for s in signals if s in dist_signals)
    acc_count = sum(1 for s in signals if s in acc_signals)
    markup_count = sum(1 for s in signals if s in markup_signals)
    markdown_count = sum(1 for s in signals if s in markdown_signals)
    
    consensus = 'MIXED'
    if dist_count >= 2:
        consensus = 'DISTRIBUTION'
    elif acc_count >= 2:
        consensus = 'ACCUMULATION'
    elif markup_count >= 2:
        consensus = 'MARKUP'
    elif markdown_count >= 2:
        consensus = 'MARKDOWN'
    
    logger.info(f"\nMulti-TF consensus: {consensus}")
    
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'timeframes': results,
        'consensus': consensus,
        'distribution_signals': dist_count,
        'accumulation_signals': acc_count,
        'markup_signals': markup_count,
        'markdown_signals': markdown_count,
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
