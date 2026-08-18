#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
defillama_collector.py — TVL по chains через DeFiLlama free API.

Free API (no auth needed): https://api.llama.fi
Rate limit: 300 calls/min · более чем достаточно для наших нужд.

Что собирает:
  1. Total TVL по 6 major chains (Ethereum, Solana, Base, Arbitrum, Optimism, Starknet)
  2. 7d change TVL для каждого chain
  3. Топ 10 protocols по TVL globally
  4. Топ protocols на Starknet
  5. Cross-chain flow direction (какой chain растёт быстрее)

Value:
  - Фундаментальный signal — куда реально деньги идут (TVL = locked capital)
  - Ecosystem health для STRK (Starknet TVL trend)
  - Confluence с Alt-Cycle Compass (TVL growth = confirmation альтсезона)

Cache: 6 часов (обновляем 4×/сутки).
Cost: $0.
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
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = CACHE_DIR / 'defillama_tvl.json'
CACHE_TTL_HOURS = 6

BASE_URL = 'https://api.llama.fi'

# Chains для tracking (name на DeFiLlama)
CHAINS = ['Ethereum', 'Solana', 'Base', 'Arbitrum', 'Optimism', 'Starknet']

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def http_get_json(url, timeout=20):
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
        ts = data.get('generated_at')
        if not ts:
            return None
        gen = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        if age_h < CACHE_TTL_HOURS:
            logger.info(f'Cache fresh ({age_h:.1f}h) — skip')
            return data
    except Exception:
        pass
    return None


def fetch_chains_tvl():
    """GET /v2/chains — TVL для всех chains."""
    data = http_get_json(f'{BASE_URL}/v2/chains')
    if not data:
        return {}
    # Filter to our chains of interest
    result = {}
    for c in data:
        name = c.get('name')
        if name in CHAINS:
            result[name] = {
                'tvl_usd': c.get('tvl'),
                'chain_id': c.get('chainId'),
                'gecko_id': c.get('gecko_id'),
                'token_symbol': c.get('tokenSymbol'),
            }
    return result


def fetch_chain_history(chain_name, days=7):
    """GET /v2/historicalChainTvl/{chain} — историческая TVL для chain."""
    data = http_get_json(f'{BASE_URL}/v2/historicalChainTvl/{chain_name}')
    if not data or not isinstance(data, list):
        return None
    if len(data) < days:
        return None
    # Последние days точек
    recent = data[-days:]
    tvl_now = recent[-1].get('tvl') if recent else None
    tvl_days_ago = recent[0].get('tvl') if recent else None
    change_pct = None
    if tvl_now and tvl_days_ago:
        change_pct = ((tvl_now / tvl_days_ago) - 1) * 100
    return {
        'tvl_now': tvl_now,
        'tvl_days_ago': tvl_days_ago,
        f'change_{days}d_pct': change_pct,
    }


def fetch_top_protocols(limit=10):
    """GET /protocols — топ protocols по TVL."""
    data = http_get_json(f'{BASE_URL}/protocols')
    if not data:
        return []
    # Sort by TVL desc
    sorted_data = sorted(data, key=lambda x: x.get('tvl') or 0, reverse=True)[:limit]
    return [{
        'name': p.get('name'),
        'symbol': p.get('symbol'),
        'tvl_usd': p.get('tvl'),
        'chain': p.get('chain'),
        'category': p.get('category'),
        'change_7d_pct': p.get('change_7d'),
    } for p in sorted_data]


def fetch_starknet_protocols(limit=10):
    """Топ protocols specifically на Starknet."""
    data = http_get_json(f'{BASE_URL}/protocols')
    if not data:
        return []
    sn = [p for p in data if 'Starknet' in (p.get('chains') or [])]
    sn.sort(key=lambda x: x.get('tvl') or 0, reverse=True)
    return [{
        'name': p.get('name'),
        'tvl_usd': p.get('tvl'),
        'category': p.get('category'),
        'change_7d_pct': p.get('change_7d'),
    } for p in sn[:limit]]


def compute_flow_leader(chain_changes):
    """Определяет chain-leader по темпу роста TVL за 7d.
    Returns: (leader_name, leader_change, laggard_name, laggard_change)
    """
    changes = [(name, data.get('change_7d_pct', 0) or 0) for name, data in chain_changes.items() if data]
    if not changes:
        return None, None, None, None
    changes.sort(key=lambda x: x[1], reverse=True)
    leader, leader_ch = changes[0]
    laggard, laggard_ch = changes[-1]
    return leader, leader_ch, laggard, laggard_ch


def main():
    logger.info('=' * 60)
    logger.info('DEFILLAMA COLLECTOR · TVL by chains')
    logger.info('=' * 60)

    cached = load_cached()
    if cached:
        return 0

    logger.info('Fetching from DeFiLlama free API...')

    # 1. Chains TVL (current)
    chains_current = fetch_chains_tvl()
    if not chains_current:
        logger.error('Failed to fetch chains TVL')
        return 1

    for name, d in chains_current.items():
        logger.info(f'  {name}: ${d["tvl_usd"]/1e9:.2f}B')

    # 2. Historical for each chain (7d)
    logger.info('\nFetching 7d history for each chain...')
    chains_history = {}
    for name in CHAINS:
        hist = fetch_chain_history(name, days=7)
        if hist:
            chains_history[name] = hist
            change = hist.get('change_7d_pct')
            if change is not None:
                emoji = '📈' if change > 0 else '📉'
                logger.info(f'  {name}: {emoji} {change:+.1f}% 7d')

    # 3. Cross-chain flow leader
    leader, leader_ch, laggard, laggard_ch = compute_flow_leader(chains_history)
    if leader:
        logger.info(f'\n📊 Cross-chain flow leader: {leader} ({leader_ch:+.1f}%) vs laggard {laggard} ({laggard_ch:+.1f}%)')

    # 4. Top protocols global
    top_global = fetch_top_protocols(limit=10)
    logger.info(f'\nTop 10 protocols globally: {len(top_global)} fetched')

    # 5. Top on Starknet
    top_starknet = fetch_starknet_protocols(limit=8)
    logger.info(f'Top Starknet protocols: {len(top_starknet)} fetched')
    for p in top_starknet[:3]:
        logger.info(f'  {p["name"]} · ${p["tvl_usd"]/1e6:.1f}M · {p.get("change_7d_pct", 0):+.1f}% 7d')

    # 6. Save
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'defillama_free_api',
        'chains_current': chains_current,
        'chains_history_7d': chains_history,
        'cross_chain_flow': {
            'leader': leader,
            'leader_change_7d_pct': leader_ch,
            'laggard': laggard,
            'laggard_change_7d_pct': laggard_ch,
        },
        'top_protocols_global': top_global,
        'top_protocols_starknet': top_starknet,
        'cache_ttl_hours': CACHE_TTL_HOURS,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f'\nSaved to {OUTPUT_FILE.name}')
    logger.info('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())