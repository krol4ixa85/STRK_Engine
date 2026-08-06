#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_eth.py — Ethereum L1 STRK Flow Collector
================================================

Собирает STRK transfers на Ethereum L1 по SEED-адресам из flow_seeds.json.
Записывает edges в CSV и summary в JSON для дальнейшей классификации.

ЧТО ДЕЛАЕТ:
1. Читает seeds из data/seeds/flow_seeds.json (только L1 адреса)
2. Для каждого seed → Etherscan tokentx query
3. Фильтрует по STRK contract (config/tokens.json)
4. Строит edges: from, to, amount_strk, tx, ts
5. Считает vol_7d, vol_30d по каждому seed
6. Записывает:
   - data/cache/flow_eth_edges.csv
   - data/cache/flow_eth_summary.json (для orchestrator)

ЧТО НЕ ДЕЛАЕТ:
- Не классифицирует flow (это делает classify_flow.py)
- Не принимает торговых решений
- Не делает BFS depth ≥ 2 (это deep flag, отдельный скрипт)

Usage:
    python3 flow_eth.py                # daily 1-hop collection
    python3 flow_eth.py --seed <addr>  # single seed only
    python3 flow_eth.py --deep         # BFS depth 2 (REVIEW mode)
"""

import os
import sys
import json
import time
import argparse
import logging
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List
import urllib.request
import urllib.parse

# ============================================================
# PATHS & CONFIG
# ============================================================

SCRIPT_DIR = Path(__file__).parent.parent.parent  # .../STRK_Engine
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
TOKENS_FILE = SCRIPT_DIR / 'config' / 'tokens.json'
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
LOG_FILE = SCRIPT_DIR / 'logs' / 'flow_eth.log'

CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Etherscan API V2 (V1 deprecated as of 2026)
# Docs: https://docs.etherscan.io/v2-migration
ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_CHAIN_ID = 1  # Ethereum mainnet
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')

# Behavior
FLOW_LOOKBACK_DAYS = int(os.environ.get('FLOW_LOOKBACK_DAYS', 7))
FLOW_LOOKBACK_DEEP_DAYS = int(os.environ.get('FLOW_LOOKBACK_DEEP_DAYS', 30))
STRICT_NO_TRADING = os.environ.get('STRICT_NO_TRADING', 'true').lower() == 'true'

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger('flow_eth')


# ============================================================
# SAFETY CHECK
# ============================================================

if not STRICT_NO_TRADING:
    logger.error("STRICT_NO_TRADING=false detected. This script is analysis-only. Aborting.")
    sys.exit(1)


# ============================================================
# LOAD REGISTRIES
# ============================================================

def load_seeds() -> Dict:
    """Загружает SEED-реестр (кошельки для мониторинга)."""
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        seeds = json.load(f)
    return seeds


def load_token_contract() -> str:
    """Загружает STRK L1 контракт (фильтр для transfers, НЕ seed)."""
    with open(TOKENS_FILE, 'r', encoding='utf-8') as f:
        tokens = json.load(f)
    addr = tokens['ethereum_l1']['strk_erc20']['address']
    # Etherscan expects lowercase
    return addr.lower()


def extract_l1_seed_addresses(seeds: Dict) -> List[Dict]:
    """
    Извлекает L1 адреса из reгистра.
    Обрабатывает ВСЕ top-level категории кроме служебных (_meta, _phantoms) и l2_native.
    
    Возвращает список dict с полями: name, address, category, role, importance.
    """
    result = []
    
    # Категории для skip: служебные и L2-only
    SKIP_CATEGORIES = {'_meta', '_phantoms', 'l2_native'}
    
    for category_name, category_data in seeds.items():
        if category_name in SKIP_CATEGORIES:
            continue
        if not isinstance(category_data, dict):
            continue
        
        for entry_name, entry in category_data.items():
            if entry_name.startswith('_'):
                continue
            if not isinstance(entry, dict):
                continue
            
            addr = entry.get('address', '')
            if not addr or addr.startswith('TBD'):
                logger.debug(f"Skipping {category_name}.{entry_name}: no valid address")
                continue
            
            # Ethereum L1 addresses are 42 chars (0x + 40 hex)
            if not (addr.startswith('0x') and len(addr) == 42):
                logger.debug(f"Skipping {category_name}.{entry_name}: not L1 format (address {addr})")
                continue
            
            result.append({
                'name': entry_name,
                'address': addr.lower(),
                'category': category_name,
                'role': entry.get('role', ''),
                'importance': entry.get('importance', 'medium'),
            })
    
    return result


# ============================================================
# ETHERSCAN API
# ============================================================

def fetch_tokentx(address: str, token_contract: str, lookback_days: int) -> List[Dict]:
    """
    Получает ERC-20 transfers для адреса (in + out) с Etherscan.
    Фильтрует по STRK контракту.
    
    Etherscan API:
        module=account&action=tokentx
        &contractaddress=<STRK L1>
        &address=<SEED>
        &startblock=0&endblock=99999999
        &page=1&offset=1000&sort=desc
    
    Возвращает список transfers в диапазоне последних lookback_days.
    """
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set. Skipping fetch.")
        return []
    
    params = {
        'chainid': ETHERSCAN_CHAIN_ID,   # V2 required
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': token_contract,
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'page': 1,
        'offset': 1000,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    }
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.error(f"Etherscan fetch failed for {address}: {e}")
        return []
    
    if data.get('status') != '1':
        msg = data.get('message', 'unknown')
        if msg == 'No transactions found':
            return []
        logger.warning(f"Etherscan API returned status {data.get('status')} for {address}: {msg}")
        return []
    
    transfers = data.get('result', [])
    
    # Filter by lookback
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    filtered = []
    for tx in transfers:
        try:
            ts = int(tx['timeStamp'])
            if ts < cutoff_ts:
                continue
            filtered.append({
                'tx_hash': tx['hash'],
                'block_number': int(tx['blockNumber']),
                'timestamp': ts,
                'timestamp_iso': datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                'from_address': tx['from'].lower(),
                'to_address': tx['to'].lower(),
                'value_raw': tx['value'],
                'amount_strk': int(tx['value']) / (10 ** int(tx.get('tokenDecimal', 18))),
                'gas_used': int(tx.get('gasUsed', 0)),
            })
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Skipping malformed tx: {e}")
            continue
    
    logger.info(f"  {address[:10]}...: {len(filtered)} transfers in last {lookback_days}d")
    return filtered


# ============================================================
# EDGE BUILDING
# ============================================================

def build_edges(seed_info: Dict, transfers: List[Dict]) -> List[Dict]:
    """
    Строит edges из transfers для одного seed.
    Каждый transfer = одна edge from → to с amount.
    """
    edges = []
    seed_addr = seed_info['address']
    
    for t in transfers:
        direction = 'in' if t['to_address'] == seed_addr else 'out'
        counterparty = t['from_address'] if direction == 'in' else t['to_address']
        
        edges.append({
            'seed_name': seed_info['name'],
            'seed_category': seed_info['category'],
            'chain': 'ethereum',
            'direction': direction,
            'seed_address': seed_addr,
            'counterparty': counterparty,
            'from_address': t['from_address'],
            'to_address': t['to_address'],
            'amount_strk': t['amount_strk'],
            'tx_hash': t['tx_hash'],
            'block_number': t['block_number'],
            'timestamp': t['timestamp'],
            'timestamp_iso': t['timestamp_iso'],
        })
    
    return edges


def build_seed_summary(seed_info: Dict, edges: List[Dict]) -> Dict:
    """Агрегированная статистика по одному seed."""
    if not edges:
        return {
            'seed_name': seed_info['name'],
            'seed_address': seed_info['address'],
            'category': seed_info['category'],
            'edges_count': 0,
            'vol_in_strk': 0,
            'vol_out_strk': 0,
            'net_flow_strk': 0,
            'unique_counterparties_in': 0,
            'unique_counterparties_out': 0,
        }
    
    vol_in = sum(e['amount_strk'] for e in edges if e['direction'] == 'in')
    vol_out = sum(e['amount_strk'] for e in edges if e['direction'] == 'out')
    cp_in = len(set(e['counterparty'] for e in edges if e['direction'] == 'in'))
    cp_out = len(set(e['counterparty'] for e in edges if e['direction'] == 'out'))
    
    return {
        'seed_name': seed_info['name'],
        'seed_address': seed_info['address'],
        'category': seed_info['category'],
        'role': seed_info.get('role', ''),
        'importance': seed_info.get('importance', 'medium'),
        'edges_count': len(edges),
        'vol_in_strk': round(vol_in, 4),
        'vol_out_strk': round(vol_out, 4),
        'net_flow_strk': round(vol_in - vol_out, 4),
        'unique_counterparties_in': cp_in,
        'unique_counterparties_out': cp_out,
    }


# ============================================================
# OUTPUT
# ============================================================

def write_edges_csv(all_edges: List[Dict], filepath: Path):
    """Записывает все edges в CSV."""
    if not all_edges:
        logger.warning("No edges to write.")
        return
    
    fields = list(all_edges[0].keys())
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_edges)
    logger.info(f"Wrote {len(all_edges)} edges to {filepath}")


def write_summary_json(seed_summaries: List[Dict], meta: Dict, filepath: Path):
    """
    Записывает summary JSON — вход для classify_flow.py и orchestrator.
    Формат совместим с ожиданиями MUST #6.
    """
    output = {
        'as_of': meta['as_of'],
        'chain': 'ethereum',
        'token_contract': meta['token_contract'],
        'lookback_days': meta['lookback_days'],
        'seeds_processed': meta['seeds_processed'],
        'total_edges': meta['total_edges'],
        'not_checked': False,
        'flow_class': None,  # заполняется classify_flow.py
        'route': None,        # заполняется classify_flow.py
        'new_addresses': [],  # заполняется classify_flow.py
        'seeds_summary': seed_summaries,
    }
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote summary JSON to {filepath}")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', help='Single seed address to process')
    parser.add_argument('--deep', action='store_true', help='30-day lookback (REVIEW mode)')
    parser.add_argument('--dry-run', action='store_true', help='No API calls, just show plan')
    args = parser.parse_args()
    
    lookback = FLOW_LOOKBACK_DEEP_DAYS if args.deep else FLOW_LOOKBACK_DAYS
    logger.info(f"=" * 60)
    logger.info(f"flow_eth.py starting · lookback={lookback}d · deep={args.deep}")
    
    # Load registries
    try:
        seeds = load_seeds()
        token_contract = load_token_contract()
    except FileNotFoundError as e:
        logger.error(f"Registry file missing: {e}")
        return 1
    
    logger.info(f"STRK L1 token contract: {token_contract}")
    
    # Extract SEED addresses
    seed_list = extract_l1_seed_addresses(seeds)
    
    if args.seed:
        seed_list = [s for s in seed_list if s['address'] == args.seed.lower()]
        if not seed_list:
            logger.error(f"Seed {args.seed} not found in registry")
            return 1
    
    logger.info(f"Processing {len(seed_list)} SEED addresses")
    
    if args.dry_run:
        for s in seed_list:
            logger.info(f"  [DRY] Would fetch: {s['name']} @ {s['address']}")
        return 0
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set. Set it in config.env or environment.")
        return 1
    
    # Fetch + build edges
    all_edges = []
    all_summaries = []
    
    for seed_info in seed_list:
        logger.info(f"Fetching {seed_info['name']} ({seed_info['address'][:10]}...)")
        transfers = fetch_tokentx(seed_info['address'], token_contract, lookback)
        edges = build_edges(seed_info, transfers)
        summary = build_seed_summary(seed_info, edges)
        
        all_edges.extend(edges)
        all_summaries.append(summary)
        
        # Etherscan rate limit: 5 req/sec free tier
        time.sleep(0.3)
    
    # Write outputs
    edges_csv = CACHE_DIR / 'flow_eth_edges.csv'
    summary_json = CACHE_DIR / 'flow_eth_summary.json'
    
    write_edges_csv(all_edges, edges_csv)
    write_summary_json(
        all_summaries,
        meta={
            'as_of': datetime.now(timezone.utc).isoformat(),
            'token_contract': token_contract,
            'lookback_days': lookback,
            'seeds_processed': len(seed_list),
            'total_edges': len(all_edges),
        },
        filepath=summary_json,
    )
    
    # Print short report
    logger.info(f"=" * 60)
    logger.info(f"DONE · {len(all_edges)} edges from {len(seed_list)} seeds")
    logger.info(f"CSV: {edges_csv}")
    logger.info(f"JSON: {summary_json}")
    logger.info(f"NEXT: run classify_flow.py to add flow_class + route")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
