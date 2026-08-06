#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ekubo_flow.py — Ekubo pools liquidity shift collector

Тянет /overview/pairs с prod-api.ekubo.org, фильтрует STRK-пары
(11 пар на mainnet на 06.08.2026), считает нетто-изменение TVL за 24ч
в каждом пуле и в сумме.

Источник:  https://prod-api.ekubo.org (публичный, без ключа)
Chain ID:  0x534e5f4d41494e = SN_MAIN (Starknet Mainnet)
STRK L2:   0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d

Что писать в cache:
  data/cache/ekubo_flow.json
  {
    "as_of": "2026-08-06T...",
    "pool_count": 11,
    "pools": [
      {
        "counter_symbol": "USDC",
        "counter_address": "0x33068f...",
        "tvl_strk_now": 9422858.36,
        "tvl_strk_delta_24h": -2244.87,
        "tvl_counter_now": 240623.84,
        "tvl_counter_delta_24h": -1493.71,
        "tvl_usd_now": 481247.68,
        "tvl_usd_delta_24h": -1436.5,
        "vol_24h_usd": 12345,
        "direction": "DRAINING"
      },
      ...
    ],
    "aggregate": {
      "total_strk_in_pools": 15234567,
      "net_strk_delta_24h": -845000,
      "net_usd_delta_24h": -21800,
      "signal": "LP_REMOVING"  // LP_ADDING / LP_REMOVING / STABLE
    }
  }

Классификация:
  |net_usd_delta_24h| < 3% от total_usd_in_pools → STABLE
  net_usd_delta_24h > 0                          → LP_ADDING
  net_usd_delta_24h < 0                          → LP_REMOVING
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
OUTPUT_FILE = CACHE_DIR / 'ekubo_flow.json'

EKUBO_API = 'https://prod-api.ekubo.org'
STARKNET_MAINNET = '0x534e5f4d41494e'
STRK_L2 = '0x4718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('ekubo_flow')


def _fetch(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error(f"fetch {url[:60]}... failed: {e}")
        return None


def get_token_info(address):
    """Get symbol + decimals + usd_price for a Starknet token via Ekubo."""
    d = _fetch(f'{EKUBO_API}/tokens/{STARKNET_MAINNET}/{address}')
    if not d:
        return None
    return {
        'symbol': d.get('symbol', '?'),
        'decimals': int(d.get('decimals', 18)),
        'usd_price': d.get('usd_price'),  # may be None for illiquid
    }


def get_strk_pairs():
    """Get all STRK-quoted pairs on Ekubo Starknet mainnet."""
    d = _fetch(f'{EKUBO_API}/overview/pairs?chainId={STARKNET_MAINNET}')
    if not d or 'topPairs' not in d:
        return []
    strk_norm = STRK_L2.lower().lstrip('0x')
    pairs = []
    for p in d['topPairs']:
        t0 = (p.get('token0') or '').lower().lstrip('0x')
        t1 = (p.get('token1') or '').lower().lstrip('0x')
        if strk_norm in t0 or strk_norm in t1:
            # normalize: strk_side is always token0 or token1
            if strk_norm in t0:
                p['_strk_side'] = 0
                p['_counter_address'] = p.get('token1')
            else:
                p['_strk_side'] = 1
                p['_counter_address'] = p.get('token0')
            pairs.append(p)
    return pairs


def compute_pool_shift(pair, strk_price, counter_info):
    """Compute per-pool 24h shift in STRK, counter, and USD."""
    counter_dec = counter_info['decimals'] if counter_info else 18
    counter_usd = counter_info['usd_price'] if counter_info else None
    strk_side = pair['_strk_side']

    def _int(k):
        v = pair.get(k, '0')
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0

    if strk_side == 0:
        strk_now = _int('tvl0_total') / 1e18
        strk_delta = _int('tvl0_delta_24h') / 1e18
        counter_now = _int('tvl1_total') / (10 ** counter_dec)
        counter_delta = _int('tvl1_delta_24h') / (10 ** counter_dec)
        vol_strk = _int('volume0_24h') / 1e18
        vol_counter = _int('volume1_24h') / (10 ** counter_dec)
    else:
        strk_now = _int('tvl1_total') / 1e18
        strk_delta = _int('tvl1_delta_24h') / 1e18
        counter_now = _int('tvl0_total') / (10 ** counter_dec)
        counter_delta = _int('tvl0_delta_24h') / (10 ** counter_dec)
        vol_strk = _int('volume1_24h') / 1e18
        vol_counter = _int('volume0_24h') / (10 ** counter_dec)

    tvl_usd_now = strk_now * strk_price
    tvl_usd_delta = strk_delta * strk_price
    if counter_usd is not None:
        tvl_usd_now += counter_now * counter_usd
        tvl_usd_delta += counter_delta * counter_usd

    vol_usd_24h = vol_strk * strk_price
    if counter_usd is not None:
        vol_usd_24h += vol_counter * counter_usd

    # direction per-pool (only meaningful when significant vs TVL)
    if tvl_usd_now < 1000:
        direction = 'DUST'
    elif abs(tvl_usd_delta) / max(tvl_usd_now, 1) < 0.02:
        direction = 'STABLE'
    elif tvl_usd_delta > 0:
        direction = 'ADDING'
    else:
        direction = 'DRAINING'

    return {
        'counter_symbol': counter_info['symbol'] if counter_info else '?',
        'counter_address': pair.get('_counter_address', ''),
        'tvl_strk_now': round(strk_now, 2),
        'tvl_strk_delta_24h': round(strk_delta, 2),
        'tvl_counter_now': round(counter_now, 4),
        'tvl_counter_delta_24h': round(counter_delta, 4),
        'tvl_usd_now': round(tvl_usd_now, 0),
        'tvl_usd_delta_24h': round(tvl_usd_delta, 0),
        'vol_24h_usd': round(vol_usd_24h, 0),
        'direction': direction,
        # useful for MUST #19 depth flag:
        'min_depth_percent': pair.get('min_depth_percent', 0),
    }


def classify_aggregate(pools):
    total_usd = sum(p['tvl_usd_now'] for p in pools)
    net_usd = sum(p['tvl_usd_delta_24h'] for p in pools)
    net_strk = sum(p['tvl_strk_delta_24h'] for p in pools)
    if total_usd <= 0:
        return 'UNKNOWN', total_usd, net_usd, net_strk
    pct = abs(net_usd) / total_usd
    if pct < 0.03:
        sig = 'STABLE'
    elif net_usd > 0:
        sig = 'LP_ADDING'
    else:
        sig = 'LP_REMOVING'
    return sig, total_usd, net_usd, net_strk


def main():
    logger.info('=' * 60)
    logger.info('EKUBO LIQUIDITY FLOW · STRK pairs')
    logger.info('=' * 60)

    strk_info = get_token_info(STRK_L2)
    if not strk_info or strk_info['usd_price'] is None:
        logger.error('STRK price unavailable — cannot compute USD deltas')
        return 1
    strk_price = strk_info['usd_price']
    logger.info(f'STRK price = ${strk_price:.6f}')

    pairs = get_strk_pairs()
    logger.info(f'Found {len(pairs)} STRK pairs on Ekubo mainnet')

    pools = []
    # de-dupe token lookups
    token_cache = {}
    for p in pairs:
        counter = p['_counter_address']
        if counter not in token_cache:
            token_cache[counter] = get_token_info(counter)
        info = token_cache[counter]
        row = compute_pool_shift(p, strk_price, info)
        pools.append(row)

    # sort by TVL usd desc, drop dust
    pools.sort(key=lambda x: -x['tvl_usd_now'])
    pools_significant = [p for p in pools if p['tvl_usd_now'] > 1000]

    signal, total_usd, net_usd, net_strk = classify_aggregate(pools_significant)

    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'strk_price_usd': strk_price,
        'pool_count': len(pools_significant),
        'pools': pools,
        'aggregate': {
            'total_usd_in_pools': round(total_usd, 0),
            'net_usd_delta_24h': round(net_usd, 0),
            'net_strk_delta_24h': round(net_strk, 0),
            'signal': signal,
            'net_pct_of_tvl': round(net_usd / total_usd * 100, 3) if total_usd > 0 else 0,
        },
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # log summary
    logger.info(f'\nTotal STRK TVL in Ekubo pools: {result["aggregate"]["total_usd_in_pools"]:,.0f} USD')
    logger.info(f'Net 24h delta: {net_usd:+,.0f} USD ({net_strk:+,.0f} STRK)')
    logger.info(f'Signal: {signal}')
    logger.info(f'\nTop 5 pools by TVL:')
    for p in pools_significant[:5]:
        delta = p["tvl_usd_delta_24h"]
        delta_str = f'{delta:+,.0f}'.rjust(10)
        logger.info(f'  STRK/{p["counter_symbol"]:<10} TVL=${p["tvl_usd_now"]:>10,.0f} '
                    f'Δ24h={delta_str}  {p["direction"]}')
    logger.info(f'\nSaved: {OUTPUT_FILE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
