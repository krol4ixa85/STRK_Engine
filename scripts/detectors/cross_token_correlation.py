#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_token_correlation.py — STRK relative performance vs L2 sector

Показывает: STRK опережает или отстаёт от L2 конкурентов?

Peer tokens:
  · ARB (Arbitrum)
  · OP (Optimism)
  · ZK (ZKsync)
  · MATIC (Polygon)
  · MANTA (Manta Network)
  · POLYGON (POL) - если доступен

Метрики:
  · 24h change
  · 7d change
  · 30d change
  · L2 sector average
  · STRK vs sector delta
  · Correlation with each peer

Сигнал:
  · STRK_OUTPERFORMING: STRK бьёт средний L2 на >5% (7d) → sector rotation в STRK
  · STRK_UNDERPERFORMING: STRK хуже среднего L2 на >5% → sector negative sentiment
  · SECTOR_MOMENTUM: весь L2 сектор растёт → beta play
  · SECTOR_WEAKNESS: весь L2 сектор падает → macro headwind
"""

import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'cross_token_correlation.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('corr')

# L2 peer tokens (OKX symbols)
PEER_TOKENS = [
    {'symbol': 'STRK', 'inst_id': 'STRK-USDT', 'name': 'Starknet'},
    {'symbol': 'ARB', 'inst_id': 'ARB-USDT', 'name': 'Arbitrum'},
    {'symbol': 'OP', 'inst_id': 'OP-USDT', 'name': 'Optimism'},
    {'symbol': 'ZK', 'inst_id': 'ZK-USDT', 'name': 'ZKsync'},
    {'symbol': 'MATIC', 'inst_id': 'MATIC-USDT', 'name': 'Polygon'},
    {'symbol': 'MANTA', 'inst_id': 'MANTA-USDT', 'name': 'Manta'},
    {'symbol': 'METIS', 'inst_id': 'METIS-USDT', 'name': 'Metis'},
]


def fetch_candles(inst_id, bar='1D', limit=35):
    """Fetch daily candles."""
    try:
        url = f'https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return list(reversed(data.get('data', [])))
    except Exception as e:
        logger.error(f"Fetch error {inst_id}: {e}")
        return []


def compute_token_performance(candles):
    """Compute performance metrics for a token."""
    if len(candles) < 30:
        return None
    
    closes = [float(c[4]) for c in candles]
    
    price_now = closes[-1]
    price_1d = closes[-2] if len(closes) >= 2 else closes[-1]
    price_7d = closes[-8] if len(closes) >= 8 else closes[0]
    price_30d = closes[-31] if len(closes) >= 31 else closes[0]
    
    return {
        'price_now': round(price_now, 6),
        'change_1d_pct': round((price_now / price_1d - 1) * 100, 2),
        'change_7d_pct': round((price_now / price_7d - 1) * 100, 2),
        'change_30d_pct': round((price_now / price_30d - 1) * 100, 2),
    }


def compute_correlation(series1, series2, n=30):
    """Simple Pearson correlation for last n periods."""
    if len(series1) < n or len(series2) < n:
        return 0
    
    s1 = series1[-n:]
    s2 = series2[-n:]
    
    # Returns
    r1 = [(s1[i] - s1[i-1]) / s1[i-1] for i in range(1, len(s1)) if s1[i-1] > 0]
    r2 = [(s2[i] - s2[i-1]) / s2[i-1] for i in range(1, len(s2)) if s2[i-1] > 0]
    
    if len(r1) < 5 or len(r2) < 5:
        return 0
    
    n = min(len(r1), len(r2))
    r1 = r1[:n]
    r2 = r2[:n]
    
    mean1 = sum(r1) / n
    mean2 = sum(r2) / n
    
    numerator = sum((r1[i] - mean1) * (r2[i] - mean2) for i in range(n))
    denom1 = sum((r - mean1) ** 2 for r in r1) ** 0.5
    denom2 = sum((r - mean2) ** 2 for r in r2) ** 0.5
    
    if denom1 * denom2 == 0:
        return 0
    return numerator / (denom1 * denom2)


def analyze_sector():
    """Compare STRK vs L2 peers."""
    logger.info("Fetching prices for L2 sector...")
    
    performances = {}
    closes_by_token = {}
    
    for token in PEER_TOKENS:
        logger.info(f"  {token['symbol']} ({token['inst_id']})...")
        candles = fetch_candles(token['inst_id'], bar='1D', limit=35)
        
        if not candles or len(candles) < 30:
            logger.warning(f"    Insufficient data for {token['symbol']}")
            continue
        
        perf = compute_token_performance(candles)
        if perf:
            performances[token['symbol']] = perf
            closes_by_token[token['symbol']] = [float(c[4]) for c in candles]
            logger.info(f"    24h: {perf['change_1d_pct']:+.2f}% · 7d: {perf['change_7d_pct']:+.2f}% · 30d: {perf['change_30d_pct']:+.2f}%")
    
    if 'STRK' not in performances:
        logger.error("No STRK data")
        return None
    
    # === Sector aggregates (excluding STRK) ===
    peer_symbols = [s for s in performances.keys() if s != 'STRK']
    
    if not peer_symbols:
        return {'error': 'No peer data'}
    
    sector_1d = sum(performances[s]['change_1d_pct'] for s in peer_symbols) / len(peer_symbols)
    sector_7d = sum(performances[s]['change_7d_pct'] for s in peer_symbols) / len(peer_symbols)
    sector_30d = sum(performances[s]['change_30d_pct'] for s in peer_symbols) / len(peer_symbols)
    
    strk_1d = performances['STRK']['change_1d_pct']
    strk_7d = performances['STRK']['change_7d_pct']
    strk_30d = performances['STRK']['change_30d_pct']
    
    # Alpha (excess return)
    alpha_1d = strk_1d - sector_1d
    alpha_7d = strk_7d - sector_7d
    alpha_30d = strk_30d - sector_30d
    
    # === Correlations ===
    correlations = {}
    strk_closes = closes_by_token.get('STRK', [])
    if strk_closes:
        for symbol, closes in closes_by_token.items():
            if symbol == 'STRK':
                continue
            corr = compute_correlation(strk_closes, closes, n=30)
            correlations[symbol] = round(corr, 3)
    
    # Best/worst performer in sector
    if performances:
        best_7d = max(performances.items(), key=lambda x: x[1]['change_7d_pct'])
        worst_7d = min(performances.items(), key=lambda x: x[1]['change_7d_pct'])
    else:
        best_7d = worst_7d = None
    
    # === CLASSIFICATION ===
    signal = 'NEUTRAL'
    interpretation = ''
    
    if alpha_7d > 5:
        signal = 'STRK_OUTPERFORMING'
        interpretation = f'STRK {strk_7d:+.1f}% vs sector {sector_7d:+.1f}% (alpha +{alpha_7d:.1f}%) — sector rotation'
    elif alpha_7d < -5:
        signal = 'STRK_UNDERPERFORMING'
        interpretation = f'STRK {strk_7d:+.1f}% vs sector {sector_7d:+.1f}% (alpha {alpha_7d:.1f}%) — sector negative on STRK'
    elif sector_7d > 8:
        signal = 'SECTOR_MOMENTUM'
        interpretation = f'Whole L2 sector rallying {sector_7d:+.1f}% (7d) — beta opportunity'
    elif sector_7d < -8:
        signal = 'SECTOR_WEAKNESS'
        interpretation = f'Whole L2 sector down {sector_7d:+.1f}% (7d) — macro headwind'
    elif abs(alpha_7d) < 2 and abs(sector_7d) < 5:
        signal = 'IN_LINE'
        interpretation = f'STRK moves with sector (alpha {alpha_7d:+.1f}%)'
    else:
        signal = 'DIVERGING'
        interpretation = f'STRK diverging (alpha {alpha_7d:+.1f}%)'
    
    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'signal': signal,
        'interpretation': interpretation,
        'performances': performances,
        'sector_averages': {
            'sector_1d_pct': round(sector_1d, 2),
            'sector_7d_pct': round(sector_7d, 2),
            'sector_30d_pct': round(sector_30d, 2),
        },
        'strk_alpha': {
            'alpha_1d_pct': round(alpha_1d, 2),
            'alpha_7d_pct': round(alpha_7d, 2),
            'alpha_30d_pct': round(alpha_30d, 2),
        },
        'correlations_30d': correlations,
        'best_7d': {'symbol': best_7d[0], 'change_pct': best_7d[1]['change_7d_pct']} if best_7d else None,
        'worst_7d': {'symbol': worst_7d[0], 'change_pct': worst_7d[1]['change_7d_pct']} if worst_7d else None,
    }
    
    return result


def main():
    logger.info("=" * 60)
    logger.info("CROSS-TOKEN CORRELATION · STRK vs L2 sector")
    logger.info("=" * 60)
    
    result = analyze_sector()
    if not result:
        return 1
    
    logger.info(f"\n=== SECTOR ANALYSIS ===")
    logger.info(f"Signal: {result['signal']}")
    logger.info(f"Interpretation: {result['interpretation']}")
    
    logger.info(f"\nSTRK vs sector alpha:")
    logger.info(f"  1d: {result['strk_alpha']['alpha_1d_pct']:+.2f}%")
    logger.info(f"  7d: {result['strk_alpha']['alpha_7d_pct']:+.2f}%")
    logger.info(f"  30d: {result['strk_alpha']['alpha_30d_pct']:+.2f}%")
    
    logger.info(f"\nCorrelations 30d:")
    for symbol, corr in result['correlations_30d'].items():
        logger.info(f"  STRK ↔ {symbol}: {corr:+.3f}")
    
    if result.get('best_7d'):
        logger.info(f"\nBest 7d: {result['best_7d']['symbol']} {result['best_7d']['change_pct']:+.2f}%")
    if result.get('worst_7d'):
        logger.info(f"Worst 7d: {result['worst_7d']['symbol']} {result['worst_7d']['change_pct']:+.2f}%")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
