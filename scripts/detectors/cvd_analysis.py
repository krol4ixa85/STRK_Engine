#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cvd_analysis.py — Cumulative Volume Delta divergence

CVD = Cumulative (Buy Volume - Sell Volume) через taker orders

Через OKX taker volume endpoint:
  · https://www.okx.com/api/v5/rubik/stat/taker-volume
  · Даёт taker buy vs sell на 5min/15min/1H/4H

Дивергенция:
  · Цена растёт + CVD падает = DISTRIBUTION divergence (розница покупает, киты продают лимитками)
  · Цена падает + CVD растёт = ACCUMULATION divergence (розница продаёт, киты покупают лимитками)
  · Цена и CVD в одну сторону = здоровый тренд
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
OUTPUT_FILE = CACHE_DIR / 'cvd_analysis.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('cvd')


def fetch_taker_volume(period='1H', limit=100):
    """OKX taker volume endpoint - buy/sell of contracts."""
    try:
        url = f'https://www.okx.com/api/v5/rubik/stat/taker-volume?ccy=STRK&instType=SPOT&period={period}'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return list(reversed(data.get('data', [])))
    except Exception as e:
        logger.error(f"Taker volume error: {e}")
        return []


def fetch_candles(inst_id, bar, limit=100):
    try:
        url = f'https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return list(reversed(data.get('data', [])))
    except Exception as e:
        logger.error(f"Candles error: {e}")
        return []


def compute_cvd(taker_data):
    """Cumulative Volume Delta from taker buy/sell records.
    Each record: [ts, buy_vol, sell_vol]"""
    if not taker_data:
        return []
    
    cvd_series = []
    cumulative = 0
    for record in taker_data:
        try:
            ts = int(record[0])
            buy = float(record[1])
            sell = float(record[2])
            delta = buy - sell
            cumulative += delta
            cvd_series.append({'ts': ts, 'delta': delta, 'cumulative': cumulative, 'buy': buy, 'sell': sell})
        except (ValueError, IndexError):
            continue
    return cvd_series


def detect_divergence(cvd_series, candles):
    """Compare CVD trend vs price trend for divergence."""
    if len(cvd_series) < 20 or len(candles) < 20:
        return None
    
    # Recent window
    recent_cvd = [c['cumulative'] for c in cvd_series[-20:]]
    recent_prices = [float(c[4]) for c in candles[-20:]]
    
    # Prior window
    prior_cvd = [c['cumulative'] for c in cvd_series[-40:-20]] if len(cvd_series) >= 40 else recent_cvd
    prior_prices = [float(c[4]) for c in candles[-40:-20]] if len(candles) >= 40 else recent_prices
    
    # Trends
    cvd_change = recent_cvd[-1] - recent_cvd[0]
    price_change = recent_prices[-1] - recent_prices[0]
    price_change_pct = (price_change / recent_prices[0]) * 100 if recent_prices[0] else 0
    
    # Ratios for scale
    cvd_dir = 'UP' if cvd_change > 0 else ('DOWN' if cvd_change < 0 else 'FLAT')
    price_dir = 'UP' if price_change_pct > 0.5 else ('DOWN' if price_change_pct < -0.5 else 'FLAT')
    
    # === DIVERGENCE DETECTION ===
    signal = 'NEUTRAL'
    interpretation = ''
    
    if price_dir == 'UP' and cvd_dir == 'DOWN':
        signal = 'BEARISH_DIVERGENCE'
        interpretation = 'Price UP + CVD DOWN — retail buying while smart money selling limits (DISTRIBUTION)'
    elif price_dir == 'DOWN' and cvd_dir == 'UP':
        signal = 'BULLISH_DIVERGENCE'
        interpretation = 'Price DOWN + CVD UP — retail selling while smart money accumulating limits (ACCUMULATION)'
    elif price_dir == 'UP' and cvd_dir == 'UP':
        signal = 'HEALTHY_UPTREND'
        interpretation = 'Price + CVD both up — buyers in control'
    elif price_dir == 'DOWN' and cvd_dir == 'DOWN':
        signal = 'HEALTHY_DOWNTREND'
        interpretation = 'Price + CVD both down — sellers in control'
    elif price_dir == 'FLAT' and cvd_dir == 'UP':
        signal = 'STEALTH_ACCUMULATION'
        interpretation = 'Price flat + CVD up — quiet accumulation under the surface'
    elif price_dir == 'FLAT' and cvd_dir == 'DOWN':
        signal = 'STEALTH_DISTRIBUTION'
        interpretation = 'Price flat + CVD down — quiet distribution under the surface'
    
    return {
        'price_change_pct': round(price_change_pct, 2),
        'price_direction': price_dir,
        'cvd_change': round(cvd_change, 2),
        'cvd_direction': cvd_dir,
        'total_buy': round(sum(c['buy'] for c in cvd_series[-20:]), 2),
        'total_sell': round(sum(c['sell'] for c in cvd_series[-20:]), 2),
        'buy_sell_ratio': round(sum(c['buy'] for c in cvd_series[-20:]) / max(sum(c['sell'] for c in cvd_series[-20:]), 1), 3),
        'signal': signal,
        'interpretation': interpretation,
    }


def main():
    logger.info("=" * 60)
    logger.info("CVD ANALYSIS · Cumulative Volume Delta divergence")
    logger.info("=" * 60)
    
    results = {}
    
    for period, bar in [('1H', '1H'), ('4H', '4H')]:
        logger.info(f"\nFetching {period} taker volume...")
        taker_data = fetch_taker_volume(period=period)
        time.sleep(0.3)
        
        if not taker_data:
            logger.warning(f"  No taker data for {period}")
            continue
        
        logger.info(f"  Got {len(taker_data)} periods")
        
        logger.info(f"Fetching {bar} candles...")
        candles = fetch_candles('STRK-USDT', bar, limit=len(taker_data))
        time.sleep(0.3)
        
        if not candles:
            continue
        
        cvd_series = compute_cvd(taker_data)
        divergence = detect_divergence(cvd_series, candles)
        
        if divergence:
            results[period] = divergence
            logger.info(f"  {period}: {divergence['signal']}")
            logger.info(f"      Price {divergence['price_change_pct']:+.2f}% · CVD change {divergence['cvd_change']:+.2f}")
            logger.info(f"      Buy/Sell ratio: {divergence['buy_sell_ratio']}")
            if divergence['interpretation']:
                logger.info(f"      {divergence['interpretation']}")
    
    # Consensus
    signals = [r['signal'] for r in results.values() if r]
    bearish_div = sum(1 for s in signals if 'BEARISH' in s or 'DISTRIBUTION' in s)
    bullish_div = sum(1 for s in signals if 'BULLISH' in s or 'ACCUMULATION' in s)
    
    consensus = 'MIXED'
    if bearish_div >= 2:
        consensus = 'DISTRIBUTION_DIVERGENCE'
    elif bullish_div >= 2:
        consensus = 'ACCUMULATION_DIVERGENCE'
    elif bearish_div > bullish_div:
        consensus = 'BEARISH_LEAN'
    elif bullish_div > bearish_div:
        consensus = 'BULLISH_LEAN'
    
    logger.info(f"\nConsensus: {consensus}")
    
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'timeframes': results,
        'consensus': consensus,
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
