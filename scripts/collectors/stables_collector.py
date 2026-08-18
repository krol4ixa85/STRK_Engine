#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stables_collector.py — общий marketcap стейблкоинов + trend.

Free API: CoinGecko (30 calls/min).

Что собирает:
  1. Total stablecoin marketcap (USDT + USDC + DAI + BUSD + FRAX и др.)
  2. 7d change stables marketcap
  3. Domination stables от общего crypto marketcap
  4. Individual top 5 stables с 7d change

Value для phase detection:
  - Стейблы marketcap растёт быстро → деньги ждут (bullish setup для рынка)
  - Стейблы marketcap падает → деньги уже в риске (late-cycle сигнал)
  - Stables dominance > 8% total = bear market cash-out
  - Stables dominance < 5% total = late alt-season (opportunity зона)

Cache: 4 часа.
Cost: $0.
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

OUTPUT_FILE = CACHE_DIR / 'stables_signal.json'
CACHE_TTL_HOURS = 4

CG_BASE = 'https://api.coingecko.com/api/v3'

# Top stablecoins IDs на CoinGecko
STABLE_IDS = [
    'tether', 'usd-coin', 'dai', 'first-digital-usd',
    'ethena-usde', 'frax', 'true-usd', 'paypal-usd',
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def http_get_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f'GET {url}: {e}')
        return None


def load_cached():
    if not OUTPUT_FILE.exists():
        return None
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        gen = datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00'))
        age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        if age_h < CACHE_TTL_HOURS:
            logger.info(f'Cache fresh ({age_h:.1f}h)')
            return data
    except Exception:
        pass
    return None


def fetch_stables_data():
    """GET markets endpoint для stablecoins."""
    ids = ','.join(STABLE_IDS)
    url = f'{CG_BASE}/coins/markets?vs_currency=usd&ids={ids}&order=market_cap_desc&per_page=20&sparkline=false&price_change_percentage=7d'
    data = http_get_json(url)
    if not data or not isinstance(data, list):
        return []
    return [{
        'symbol': (c.get('symbol') or '').upper(),
        'name': c.get('name'),
        'marketcap_usd': c.get('market_cap'),
        'price_usd': c.get('current_price'),
        'change_7d_pct': c.get('price_change_percentage_7d_in_currency'),
        'change_24h_pct': c.get('price_change_percentage_24h'),
    } for c in data]


def fetch_total_marketcap():
    """GET /global — total crypto marketcap."""
    data = http_get_json(f'{CG_BASE}/global')
    if not data or 'data' not in data:
        return None
    return data['data'].get('total_market_cap', {}).get('usd')


def compute_trend_signal(total_stables, dominance_pct):
    """Классифицирует stables signal.
    Returns: {signal, reasoning}
    """
    reasoning = []

    # High dominance = много cash ждут
    if dominance_pct > 10:
        signal = 'HIGH_DRY_POWDER'
        reasoning.append(f'Stables доминирование {dominance_pct:.1f}% (>10%) = много кэша ждёт')
        reasoning.append('Bullish setup: капитал готов вернуться')
    elif dominance_pct > 7:
        signal = 'ELEVATED_DRY_POWDER'
        reasoning.append(f'Stables {dominance_pct:.1f}% — умеренно много кэша')
        reasoning.append('Можно ожидать buy pressure')
    elif dominance_pct > 5:
        signal = 'BALANCED'
        reasoning.append(f'Stables {dominance_pct:.1f}% — сбалансировано')
        reasoning.append('Нейтральный сетап')
    elif dominance_pct > 3:
        signal = 'LOW_DRY_POWDER'
        reasoning.append(f'Stables только {dominance_pct:.1f}% — деньги в risk assets')
        reasoning.append('Late-cycle сигнал: осторожно')
    else:
        signal = 'CRITICALLY_LOW'
        reasoning.append(f'Stables {dominance_pct:.1f}% — критически мало')
        reasoning.append('Топ формируется: пора фиксировать')

    return {'signal': signal, 'reasoning': reasoning}


def main():
    logger.info('=' * 60)
    logger.info('STABLES COLLECTOR · dry powder tracking')
    logger.info('=' * 60)

    cached = load_cached()
    if cached:
        return 0

    logger.info('Fetching stables data from CoinGecko...')

    # 1. Individual stables
    stables = fetch_stables_data()
    if not stables:
        logger.error('Failed to fetch stables')
        return 1

    total_stables_mcap = sum(s.get('marketcap_usd', 0) or 0 for s in stables)
    logger.info(f'  Total stables mcap: ${total_stables_mcap/1e9:.1f}B')

    # 2. Total crypto marketcap for dominance
    total_mcap = fetch_total_marketcap()
    if not total_mcap:
        logger.error('Failed to fetch total marketcap')
        return 1

    dominance_pct = (total_stables_mcap / total_mcap) * 100
    logger.info(f'  Total crypto mcap: ${total_mcap/1e12:.2f}T')
    logger.info(f'  Stables dominance: {dominance_pct:.2f}%')

    # 3. Analyze signal
    signal_info = compute_trend_signal(total_stables_mcap, dominance_pct)
    logger.info(f'\n📊 Signal: {signal_info["signal"]}')
    for r in signal_info['reasoning']:
        logger.info(f'  · {r}')

    # 4. Top individual movers
    stables_sorted = sorted(stables, key=lambda x: x.get('marketcap_usd', 0) or 0, reverse=True)
    logger.info('\nTop stables:')
    for s in stables_sorted[:5]:
        mcap_b = (s.get('marketcap_usd') or 0) / 1e9
        change = s.get('change_7d_pct') or 0
        logger.info(f'  {s["symbol"]}: ${mcap_b:.1f}B · {change:+.2f}% 7d')

    # 5. Save
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'coingecko_free_api',
        'total_stables_marketcap_usd': total_stables_mcap,
        'total_crypto_marketcap_usd': total_mcap,
        'stables_dominance_pct': round(dominance_pct, 3),
        'signal': signal_info['signal'],
        'reasoning': signal_info['reasoning'],
        'top_stables': stables_sorted[:8],
        'cache_ttl_hours': CACHE_TTL_HOURS,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f'\nSaved to {OUTPUT_FILE.name}')
    logger.info('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())