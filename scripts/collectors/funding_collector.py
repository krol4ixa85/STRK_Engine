#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
funding_collector.py — funding rates + top gainers через CoinGecko free API.

Использует CoinGecko вместо Binance/Bybit т.к. они blocks некоторые регионы.
CoinGecko global availability, тот же provider что для alt_cycle.

Endpoints:
  /derivatives/exchanges/binance_futures — funding для BTC/ETH
  /coins/markets — top gainers/losers/volume

Value:
  - Funding > 30% APR → overleverage LONG → short squeeze risk
  - Funding < -20% APR → overleverage SHORT → long squeeze risk
  - Top gainers 24h = momentum candidates

Cache: 30 min.
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

OUTPUT_FILE = CACHE_DIR / 'funding_signals.json'
CACHE_TTL_MINUTES = 30

CG_BASE = 'https://api.coingecko.com/api/v3'

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
        age_min = (datetime.now(timezone.utc) - gen).total_seconds() / 60
        if age_min < CACHE_TTL_MINUTES:
            logger.info(f'Cache fresh ({age_min:.0f}min)')
            return data
    except Exception:
        pass
    return None


def fetch_top_movers(vs_currency='usd'):
    """GET /coins/markets — top 250 by marketcap, filter noise."""
    # Order by marketcap (не по volume — шум с новыми тoken'ами)
    url = f'{CG_BASE}/coins/markets?vs_currency={vs_currency}&order=market_cap_desc&per_page=250&page=1&sparkline=false&price_change_percentage=24h'
    data = http_get_json(url)
    if not data or not isinstance(data, list):
        return []
    result = []
    for c in data:
        try:
            change = c.get('price_change_percentage_24h_in_currency')
            vol = c.get('total_volume', 0) or 0
            mcap = c.get('market_cap', 0) or 0
            # Стрict filter — избегаем shitcoins в топе
            if change is None or vol < 50_000_000 or mcap < 100_000_000:
                continue
            result.append({
                'token': (c.get('symbol') or '').upper(),
                'name': c.get('name'),
                'price_change_24h_pct': round(float(change), 2),
                'volume_24h_usd': vol,
                'last_price': c.get('current_price'),
                'marketcap': mcap,
            })
        except (ValueError, TypeError):
            continue
    return result


# Whitelist для funding — только liquid major tokens что имеет смысл tracking
FUNDING_WHITELIST = {
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE',
    'LINK', 'AAVE', 'UNI', 'ARB', 'OP', 'MATIC',
    'AVAX', 'LDO', 'MORPHO', 'PENDLE', 'CRV',
    'PEPE', 'WIF', 'BONK', 'SHIB',
    'STRK', 'FET', 'TAO', 'VIRTUAL', 'ONDO', 'RENDER', 'RNDR',
    'IMX', 'AXS', 'SAND', 'FIL', 'HNT', 'AKT', 'GRT', 'PYTH',
    'EIGEN', 'ETHFI', 'RPL', 'AIXBT', 'ZK', 'MNT', 'CFG',
}

def fetch_derivatives_data():
    """GET /derivatives — только для major tokens в whitelist."""
    url = f'{CG_BASE}/derivatives'
    data = http_get_json(url)
    if not data or not isinstance(data, list):
        return {}
    result = {}
    for d in data:
        try:
            symbol = d.get('symbol', '')
            # Extract base ticker
            base = None
            for suffix in ['USDT', 'USD', '-PERP', 'PERP']:
                if symbol.endswith(suffix):
                    base = symbol[:-len(suffix)].rstrip('_-').upper()
                    break
            if not base or base not in FUNDING_WHITELIST:
                continue

            funding_rate = d.get('funding_rate')
            if funding_rate is None:
                continue

            fr = float(funding_rate) / 100
            apr_pct = fr * 3 * 365 * 100

            # Keep first occurrence (usually largest exchange)
            if base not in result:
                result[base] = {
                    'funding_rate_current': fr,
                    'funding_apr_pct': round(apr_pct, 2),
                    'exchange': d.get('market'),
                    'index_price': d.get('index'),
                    'open_interest_usd': d.get('open_interest'),
                }
        except (ValueError, TypeError, KeyError):
            continue
    return result


def analyze_funding_signal(apr_pct):
    if apr_pct is None:
        return 'UNKNOWN', 'Нет данных'
    if apr_pct > 50:
        return 'EXTREME_LONG', 'Экстремальный overleverage LONG — high probability of long squeeze'
    if apr_pct > 30:
        return 'HIGH_LONG', 'Много LONG позиций — риск коррекции'
    if apr_pct > 10:
        return 'MODERATE_LONG', 'Умеренный bullish sentiment'
    if apr_pct > -10:
        return 'NEUTRAL', 'Сбалансированные позиции'
    if apr_pct > -20:
        return 'MODERATE_SHORT', 'Умеренный bearish sentiment'
    if apr_pct > -50:
        return 'HIGH_SHORT', 'Много SHORT позиций — риск short squeeze'
    return 'EXTREME_SHORT', 'Экстремальный overleverage SHORT'


def main():
    logger.info('=' * 60)
    logger.info('FUNDING COLLECTOR · CoinGecko free API')
    logger.info('=' * 60)

    cached = load_cached()
    if cached:
        return 0

    # 1. Funding rates
    logger.info('Fetching derivatives / funding rates...')
    funding = fetch_derivatives_data()
    if funding:
        logger.info(f'  Got funding for {len(funding)} tokens')
        # Add signal to each
        for token, d in funding.items():
            signal, _ = analyze_funding_signal(d['funding_apr_pct'])
            d['signal'] = signal
    else:
        logger.warning('  Funding data unavailable')
        funding = {}

    extremes = [(t, d) for t, d in funding.items() if d.get('signal') in ('EXTREME_LONG', 'EXTREME_SHORT', 'HIGH_LONG', 'HIGH_SHORT')]
    if extremes:
        logger.info('\n⚠ Extreme funding:')
        for t, d in extremes[:5]:
            logger.info(f'  {t}: {d["funding_apr_pct"]:+.1f}% APR · {d["signal"]}')

    # 2. Top gainers/losers/volume
    logger.info('\nFetching top movers 24h...')
    all_movers = fetch_top_movers()
    if all_movers:
        # Sort separately
        top_gainers = sorted(all_movers, key=lambda x: x['price_change_24h_pct'], reverse=True)[:10]
        top_losers = sorted(all_movers, key=lambda x: x['price_change_24h_pct'])[:10]
        top_volume = sorted(all_movers, key=lambda x: x['volume_24h_usd'], reverse=True)[:10]

        logger.info(f'\nTop gainers 24h:')
        for g in top_gainers[:5]:
            logger.info(f'  {g["token"]}: +{g["price_change_24h_pct"]:.1f}% · ${g["volume_24h_usd"]/1e6:.0f}M vol')
    else:
        top_gainers, top_losers, top_volume = [], [], []

    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'coingecko_free_api',
        'funding_rates': funding,
        'extreme_funding': [{'token': t, **d} for t, d in extremes],
        'top_gainers_24h': top_gainers,
        'top_losers_24h': top_losers,
        'top_volume_24h': top_volume,
        'cache_ttl_minutes': CACHE_TTL_MINUTES,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f'\nSaved to {OUTPUT_FILE.name}')
    logger.info('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())