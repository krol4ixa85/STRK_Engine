#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
funding_history.py — Funding rate signal from OKX

Собирает funding history STRK-USDT-SWAP с OKX и вычисляет:
  - current funding annualized
  - 7d/14d average
  - trend (растёт/падает)
  - extreme flag (>15% ann = long crowded, <-15% ann = short crowded)

Логика:
  · Funding = плата между long и short каждые 8 часов
  · Положительный funding = long платит short (long-crowded)
  · Отрицательный funding = short платит long (short-crowded)
  · Устойчивый extreme funding = contrarian setup (потенциальный squeeze)

Output: data/cache/funding_signal.json
"""

import os
import sys
import json
import time
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('funding')


def fetch_bitget_funding_history():
    """Fetch Bitget STRKUSDT funding history (fallback/confirmation source)."""
    all_items = []
    try:
        # Bitget: pageSize max 100
        url = 'https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=STRKUSDT&productType=USDT-FUTURES&pageSize=100'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        if data.get('code') == '00000':
            for item in data.get('data', []):
                all_items.append({
                    'fundingTime': item['fundingTime'],
                    'fundingRate': item['fundingRate'],
                })
    except Exception as e:
        logger.warning(f"Bitget fetch failed: {e}")
    return all_items


def compare_sources(okx_funding, bitget_items):
    """Cross-validate OKX vs Bitget funding."""
    if not bitget_items or not okx_funding:
        return {'agreement': 'no_second_source'}
    
    # Bitget current annualized
    try:
        latest_bg = float(bitget_items[0]['fundingRate']) * 3 * 365 * 100
    except (KeyError, ValueError, IndexError):
        return {'agreement': 'parse_error'}
    
    okx_current = okx_funding.get('current_annualized_pct', 0)
    diff = abs(okx_current - latest_bg)
    
    return {
        'okx_current_pct': okx_current,
        'bitget_current_pct': round(latest_bg, 3),
        'diff_pct': round(diff, 3),
        'agreement': 'agree' if diff < 5 else 'disagree',
    }


def fetch_okx_funding_history(days_back=14):
    """Fetch OKX STRK-USDT-SWAP funding history."""
    all_items = []
    after_ts = None
    
    # Each page = 100 entries = ~33 days of 8h funding
    for page in range(3):  # up to ~100 days
        url = f'https://www.okx.com/api/v5/public/funding-rate-history?instId=STRK-USDT-SWAP&limit=100'
        if after_ts:
            url += f'&after={after_ts}'
        
        try:
            r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(r, timeout=15).read())
            items = data.get('data', [])
            if not items:
                break
            all_items.extend(items)
            after_ts = int(items[-1]['fundingTime']) - 1
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"OKX fetch failed: {e}")
            break
    
    return all_items


def analyze_funding(items, days_back=14):
    """Compute funding metrics from raw items."""
    if not items:
        return None
    
    # Sort by time desc (newest first)
    items.sort(key=lambda x: -int(x['fundingTime']))
    
    now = datetime.now(timezone.utc)
    cutoff_3d = int((now - timedelta(days=3)).timestamp() * 1000)
    cutoff_7d = int((now - timedelta(days=7)).timestamp() * 1000)
    cutoff_14d = int((now - timedelta(days=14)).timestamp() * 1000)
    
    rates_all = []
    rates_3d = []
    rates_7d = []
    rates_14d = []
    
    for item in items:
        try:
            ts = int(item['fundingTime'])
            rate = float(item.get('realizedRate') or item['fundingRate'])
            annualized = rate * 3 * 365 * 100
            
            rates_all.append((ts, annualized))
            if ts >= cutoff_14d:
                rates_14d.append(annualized)
            if ts >= cutoff_7d:
                rates_7d.append(annualized)
            if ts >= cutoff_3d:
                rates_3d.append(annualized)
        except (KeyError, ValueError, TypeError):
            continue
    
    if not rates_all:
        return None
    
    current = rates_all[0][1]
    avg_3d = sum(rates_3d) / len(rates_3d) if rates_3d else current
    avg_7d = sum(rates_7d) / len(rates_7d) if rates_7d else current
    avg_14d = sum(rates_14d) / len(rates_14d) if rates_14d else current
    
    # === KEY NEW METRICS: cumulative pressure ===
    # % of negative fundings in each window
    neg_3d = [r for r in rates_3d if r < 0]
    neg_7d = [r for r in rates_7d if r < 0]
    pct_negative_3d = len(neg_3d) / max(len(rates_3d), 1) * 100
    pct_negative_7d = len(neg_7d) / max(len(rates_7d), 1) * 100
    
    # Extremes
    min_ann_7d = min(rates_7d) if rates_7d else 0
    max_ann_7d = max(rates_7d) if rates_7d else 0
    
    # === SHORT SQUEEZE detection ===
    # Multiple ways to detect short-crowded book:
    #   - >40% of fundings negative in last 3 days
    #   - OR min funding <-10% ann in last 7 days
    #   - OR sustained negative avg (avg_3d < -3%)
    is_short_crowded_3d = pct_negative_3d >= 40
    is_short_crowded_extreme = min_ann_7d < -10
    is_short_crowded_avg = avg_3d < -3
    
    short_crowded = is_short_crowded_3d or is_short_crowded_extreme or is_short_crowded_avg
    
    # === LONG SQUEEZE / one-sided long book ===
    pct_positive_3d = 100 - pct_negative_3d
    max_ann_3d = max(rates_3d) if rates_3d else 0
    long_crowded = (pct_positive_3d >= 80 and avg_3d > 8) or max_ann_7d > 30
    
    # Extreme classification
    if current > 25 or long_crowded:
        extreme = 'long_extreme'
    elif current < -25 or is_short_crowded_extreme:
        extreme = 'short_extreme'
    elif short_crowded:
        extreme = 'short_crowded'
    elif long_crowded:
        extreme = 'long_crowded'
    else:
        extreme = 'normal'
    
    return {
        'as_of': now.isoformat(),
        'source': 'OKX STRK-USDT-SWAP',
        'current_annualized_pct': round(current, 3),
        'avg_3d_pct': round(avg_3d, 3),
        'avg_7d_pct': round(avg_7d, 3),
        'avg_14d_pct': round(avg_14d, 3),
        'pct_negative_3d': round(pct_negative_3d, 1),
        'pct_negative_7d': round(pct_negative_7d, 1),
        'min_ann_7d': round(min_ann_7d, 2),
        'max_ann_7d': round(max_ann_7d, 2),
        'extreme': extreme,
        'short_crowded': short_crowded,
        'long_crowded': long_crowded,
        'samples_3d': len(rates_3d),
        'samples_7d': len(rates_7d),
        'samples_14d': len(rates_14d),
    }


def classify_funding_signal(funding):
    """Convert funding metrics to trading signal."""
    if not funding:
        return {'signal': 'UNKNOWN', 'reason': 'no funding data'}
    
    reasons = []
    reasons.append(f"current {funding['current_annualized_pct']:+.1f}% ann")
    reasons.append(f"3d neg fundings: {funding['pct_negative_3d']:.0f}%")
    reasons.append(f"7d min: {funding['min_ann_7d']:+.1f}%")
    
    # SHORT SQUEEZE setup - contrarian BULLISH
    if funding['short_crowded']:
        if funding['extreme'] == 'short_extreme' or funding['min_ann_7d'] < -12:
            signal = 'BULLISH_CONTRARIAN'  # strong squeeze setup
            reasons.append(f"⚡ SHORT SQUEEZE setup (extreme negative funding)")
        else:
            signal = 'BULLISH_WEAK'
            reasons.append(f"short-crowded book — squeeze possible")
    
    # LONG CROWDED - contrarian BEARISH
    elif funding['long_crowded']:
        if funding['extreme'] == 'long_extreme':
            signal = 'BEARISH_CONTRARIAN'
            reasons.append(f"⚡ LONG SQUEEZE setup (extreme positive funding)")
        else:
            signal = 'BEARISH_WEAK'
            reasons.append(f"long-crowded book — dump possible")
    
    else:
        signal = 'NEUTRAL'
        reasons.append(f"funding in normal range")
    
    return {
        'signal': signal,
        'reason': '; '.join(reasons),
        'funding_metrics': funding,
    }


def main():
    logger.info("=" * 60)
    logger.info("FUNDING SIGNAL · OKX STRK-USDT-SWAP + Bitget cross-check")
    logger.info("=" * 60)
    
    items = fetch_okx_funding_history()
    logger.info(f"Fetched {len(items)} funding entries from OKX")
    
    funding = analyze_funding(items)
    if not funding:
        logger.error("Failed to analyze funding")
        return 1
    
    # Cross-check with Bitget
    logger.info("Cross-checking with Bitget...")
    bitget_items = fetch_bitget_funding_history()
    logger.info(f"  Bitget returned {len(bitget_items)} entries")
    cross = compare_sources(funding, bitget_items)
    
    logger.info(f"\nOKX Current: {funding['current_annualized_pct']:+.2f}% annualized")
    logger.info(f"3d avg:  {funding['avg_3d_pct']:+.2f}% · % negative: {funding['pct_negative_3d']:.0f}%")
    logger.info(f"7d avg:  {funding['avg_7d_pct']:+.2f}% · min: {funding['min_ann_7d']:+.1f}%")
    logger.info(f"14d avg: {funding['avg_14d_pct']:+.2f}%")
    logger.info(f"Extreme: {funding['extreme']}")
    logger.info(f"Short crowded: {funding['short_crowded']}")
    logger.info(f"Long crowded: {funding['long_crowded']}")
    
    if cross.get('agreement') == 'agree':
        logger.info(f"✓ Bitget confirms OKX (diff {cross['diff_pct']}%)")
    elif cross.get('agreement') == 'disagree':
        logger.warning(f"⚠ OKX/Bitget disagree by {cross['diff_pct']}% - possible one-side glitch")
    
    classification = classify_funding_signal(funding)
    classification['cross_source_check'] = cross
    logger.info(f"\nSignal: {classification['signal']}")
    logger.info(f"Reason: {classification['reason']}")
    
    output_file = CACHE_DIR / 'funding_signal.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(classification, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {output_file}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
