#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
endur_lst_flow.py — Endur (xSTRK) liquid staking flow collector

Endur — крупнейший LST на Starknet. xSTRK — ERC-4626 vault, где exchange
rate xSTRK/STRK растёт каждый блок (yield через appreciation, не rebase).

Источники:
  1. https://app.endur.fi/api/stats  → живой tvlStrk, APY
  2. https://api.llama.fi/protocol/endur  → историческая TVL_USD (дневная гранулярность, 619 точек)

Что вычисляем:
  · lst_tvl_strk_now       — сколько STRK сейчас через Endur LST
  · lst_tvl_usd_now        — USD-эквивалент
  · lst_tvl_usd_delta_24h  — из DefiLlama daily series
  · lst_tvl_usd_delta_7d   — из DefiLlama daily series
  · lst_apy                — из Endur API
  · direction              — LST_MINTING / LST_REDEEMING / STABLE

Пороги direction (по 24h delta):
  |Δ24h| < 3% от TVL          → STABLE
  Δ24h > 0                     → LST_MINTING (люди стейкают через LST)
  Δ24h < 0                     → LST_REDEEMING (выводят через withdraw queue)

Output: data/cache/endur_lst_flow.json
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
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = CACHE_DIR / 'endur_lst_flow.json'

XSTRK_ADDRESS = '0x28d709c875c0ceac3dce7065bec5328186dc89fe254527084d1689910954b0a'
ENDUR_STATS_URL = 'https://app.endur.fi/api/stats'
DEFILLAMA_URL = 'https://api.llama.fi/protocol/endur'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('endur_lst')


def _fetch(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error(f"fetch {url[:60]}... failed: {e}")
        return None


def fetch_endur_stats():
    """Live tvlStrk + APY from Endur API."""
    d = _fetch(ENDUR_STATS_URL)
    if not d:
        return None
    return {
        'tvl_strk_now': d.get('tvlStrk'),
        'tvl_usd_now_endur_reported': d.get('tvl'),
        'apy_pct': (d.get('apy', 0) or 0) * 100,
    }


def fetch_defillama_history():
    """
    Historical daily series from DefiLlama.
    Возвращает STRK-count (не USD!), чтобы отсечь влияние изменения цены STRK.
    Плюс USD для справки.
    """
    d = _fetch(DEFILLAMA_URL)
    if not d:
        return None

    # tokens series — количество каждого токена по дням
    tok_series = d.get('tokens', [])
    usd_series = d.get('tvl', [])
    if not tok_series:
        return None

    def _strk(point):
        return (point or {}).get('tokens', {}).get('STRK')

    def _usd(point):
        return (point or {}).get('totalLiquidityUSD')

    tok_latest = tok_series[-1]
    tok_24 = tok_series[-2] if len(tok_series) > 1 else None
    tok_7 = tok_series[-8] if len(tok_series) > 7 else None
    tok_30 = tok_series[-31] if len(tok_series) > 30 else None

    usd_latest = usd_series[-1] if usd_series else None
    usd_24 = usd_series[-2] if len(usd_series) > 1 else None
    usd_7 = usd_series[-8] if len(usd_series) > 7 else None
    usd_30 = usd_series[-31] if len(usd_series) > 30 else None

    return {
        'strk_latest': _strk(tok_latest),
        'strk_24h_ago': _strk(tok_24),
        'strk_7d_ago': _strk(tok_7),
        'strk_30d_ago': _strk(tok_30),
        'usd_latest': _usd(usd_latest),
        'usd_24h_ago': _usd(usd_24),
        'usd_7d_ago': _usd(usd_7),
        'usd_30d_ago': _usd(usd_30),
        'latest_ts': tok_latest.get('date'),
    }


def classify(strk_now, strk_ref):
    """Классификация по STRK-count (чистая, без искажения ценой)."""
    if not strk_now or not strk_ref:
        return 'UNKNOWN', 0, 0
    delta = strk_now - strk_ref
    pct = delta / strk_ref * 100 if strk_ref > 0 else 0
    # STRK-flow пороги: 1% за 24ч — заметное движение для LST такого масштаба
    if abs(pct) < 1:
        return 'STABLE', delta, pct
    elif delta > 0:
        return 'LST_MINTING', delta, pct
    else:
        return 'LST_REDEEMING', delta, pct


def main():
    logger.info('=' * 60)
    logger.info('ENDUR LST FLOW · xSTRK')
    logger.info('=' * 60)

    live = fetch_endur_stats()
    hist = fetch_defillama_history()

    if not live and not hist:
        logger.error('Both Endur API and DefiLlama unavailable')
        result = {
            'as_of': datetime.now(timezone.utc).isoformat(),
            'status': 'NOT_CHECKED',
            'reason': 'both Endur API and DefiLlama unavailable',
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        return 1

    tvl_strk_now = live['tvl_strk_now'] if live else None
    apy_pct = live['apy_pct'] if live else None

    # Основной сигнал — по STRK-count из DefiLlama
    if hist and hist.get('strk_latest'):
        strk_now_hist = hist['strk_latest']
        strk_24 = hist['strk_24h_ago']
        strk_7 = hist['strk_7d_ago']
        strk_30 = hist['strk_30d_ago']
        signal, delta_24h_strk, pct_24h = classify(strk_now_hist, strk_24)
        _, delta_7d_strk, pct_7d = classify(strk_now_hist, strk_7)
        _, delta_30d_strk, pct_30d = classify(strk_now_hist, strk_30) if strk_30 else ('UNKNOWN', 0, 0)
        # Для справки — USD
        usd_now = hist.get('usd_latest')
    else:
        strk_now_hist = tvl_strk_now
        strk_24 = strk_7 = strk_30 = None
        signal = 'UNKNOWN'
        delta_24h_strk = delta_7d_strk = delta_30d_strk = 0
        pct_24h = pct_7d = pct_30d = 0
        usd_now = live['tvl_usd_now_endur_reported'] if live else None

    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'contract': XSTRK_ADDRESS,
        'name': 'Endur xSTRK (Liquid Staked STRK)',
        'tvl_strk_now': round(tvl_strk_now or strk_now_hist or 0, 0),
        'tvl_usd_now': round(usd_now, 0) if usd_now else None,
        'apy_pct': round(apy_pct, 2) if apy_pct else None,
        'history_strk': {
            'strk_24h_ago': round(strk_24, 0) if strk_24 else None,
            'strk_7d_ago': round(strk_7, 0) if strk_7 else None,
            'strk_30d_ago': round(strk_30, 0) if strk_30 else None,
        },
        'deltas': {
            'strk_delta_24h': round(delta_24h_strk, 0),
            'strk_delta_7d': round(delta_7d_strk, 0),
            'strk_delta_30d': round(delta_30d_strk, 0),
            'pct_24h': round(pct_24h, 2),
            'pct_7d': round(pct_7d, 2),
            'pct_30d': round(pct_30d, 2),
        },
        'signal': signal,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    _tvl = tvl_strk_now or strk_now_hist or 0
    logger.info(f'\nxSTRK TVL: {_tvl:,.0f} STRK' + (f' (${usd_now:,.0f})' if usd_now else ''))
    logger.info(f'APY: {apy_pct:.2f}%' if apy_pct else 'APY: unknown')
    logger.info(f'Δ24h: {delta_24h_strk:+,.0f} STRK ({pct_24h:+.2f}%) → {signal}')
    logger.info(f'Δ7d:  {delta_7d_strk:+,.0f} STRK ({pct_7d:+.2f}%)')
    logger.info(f'Δ30d: {delta_30d_strk:+,.0f} STRK ({pct_30d:+.2f}%)')
    logger.info(f'\nSaved: {OUTPUT_FILE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
