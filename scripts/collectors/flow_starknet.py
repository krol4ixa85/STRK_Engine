#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_starknet.py — Starknet L2 STRK Flow Collector
====================================================

Собирает STRK transfers на Starknet L2 по SEED-адресам через Starkscan API.
Симметричен flow_eth.py, но для L2 сети.

ЧТО ДЕЛАЕТ:
1. Читает L2 seeds из data/seeds/flow_seeds.json (l2_native категория)
2. Для каждого seed → Starkscan transfers query
3. Фильтрует по STRK L2 контракту (config/tokens.json)
4. Строит edges: from, to, amount_strk, tx_hash, ts, chain=starknet
5. Считает vol_7d, vol_30d по каждому seed
6. Записывает:
   - data/cache/flow_starknet_edges.csv
   - data/cache/flow_starknet_summary.json

ЧТО НЕ ДЕЛАЕТ:
- Не классифицирует flow (это делает classify_flow.py)
- Не принимает торговых решений
- Не делает BFS depth ≥ 2 (это deep flag, отдельный скрипт)

Usage:
    python3 flow_starknet.py                # daily 1-hop collection
    python3 flow_starknet.py --seed <addr>  # single seed only
    python3 flow_starknet.py --deep         # 30d lookback (REVIEW mode)
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
LOG_FILE = SCRIPT_DIR / 'logs' / 'flow_starknet.log'

CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Starkscan API
STARKSCAN_BASE = 'https://api.starkscan.co/api/v1/SN_MAIN'
STARKSCAN_API_KEY = os.environ.get('STARKSCAN_API_KEY', '')

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
logger = logging.getLogger('flow_starknet')

# ============================================================
# SAFETY CHECK
# ============================================================

if not STRICT_NO_TRADING:
    logger.error("STRICT_NO_TRADING=false detected. This script is analysis-only. Aborting.")
    sys.exit(1)


# ============================================================
# STARKNET ADDRESS NORMALIZATION
# ============================================================

def normalize_stark_address(addr: str) -> str:
    """
    Нормализует Starknet адрес: убирает leading zeros после 0x.
    
    Starknet API часто возвращает адреса без leading zeros,
    а мы можем хранить с ними. Приводим к каноническому виду.
    
    Examples:
        0x00ca1702e6... → 0xca1702e6...
        0x04718f5a... → 0x4718f5a...
        0x000...abc → 0xabc
    """
    if not addr or not isinstance(addr, str):
        return addr
    addr = addr.lower().strip()
    if not addr.startswith('0x'):
        return addr
    hex_part = addr[2:].lstrip('0')
    if not hex_part:
        hex_part = '0'
    return '0x' + hex_part


# ============================================================
# LOAD REGISTRIES
# ============================================================

def load_seeds() -> Dict:
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_token_contract() -> str:
    """STRK L2 native token contract (normalized)."""
    with open(TOKENS_FILE, 'r', encoding='utf-8') as f:
        tokens = json.load(f)
    return normalize_stark_address(tokens['starknet_l2']['strk_native']['address'])


def extract_l2_seed_addresses(seeds: Dict) -> List[Dict]:
    """
    Извлекает L2 адреса — только категория l2_native.
    
    L2 адреса длиннее (до 66 символов с 0x), а не 42 как в L1.
    Различаем по длине: L2 > 42 chars, L1 == 42 chars.
    """
    result = []
    l2_data = seeds.get('l2_native', {})
    
    for entry_name, entry in l2_data.items():
        if entry_name.startswith('_'):
            continue
        if not isinstance(entry, dict):
            continue
        
        addr = entry.get('address', '')
        if not addr or addr.startswith('TBD'):
            continue
        
        # Skip token contract itself (это TOKEN, не SEED)
        # vstrk_governance это одновременно и адрес контракта — не мониторим как seed
        if entry_name == 'vstrk_governance':
            logger.debug(f"Skipping {entry_name}: это токен-контракт, не seed")
            continue
        
        # L2 addresses can be shorter or longer than L1
        addr_lower = addr.lower()
        if not addr_lower.startswith('0x'):
            continue
        
        result.append({
            'name': entry_name,
            'address': normalize_stark_address(addr),
            'category': 'l2_native',
            'role': entry.get('role', ''),
            'importance': entry.get('importance', 'medium'),
        })
    
    return result


# ============================================================
# STARKSCAN API
# ============================================================

def fetch_transfers(address: str, token_contract: str, lookback_days: int) -> List[Dict]:
    """
    Получает transfers для адреса через Starkscan API.
    Фильтрует по STRK L2 контракту.
    
    Starkscan API:
        GET /v1/SN_MAIN/address/{address}/transfers
        Header: X-Starkscan-Api-Key: <key>
        Optional params: token=<addr>, limit, cursor
    
    Возвращает список transfers в диапазоне последних lookback_days.
    """
    if not STARKSCAN_API_KEY:
        logger.error("STARKSCAN_API_KEY not set. Skipping fetch.")
        return []
    
    # Try token-filtered endpoint first
    url = f"{STARKSCAN_BASE}/token/{token_contract}/transfers?limit=100"
    headers = {
        'X-Starkscan-Api-Key': STARKSCAN_API_KEY,
        'User-Agent': 'STRK-Engine/1.0',
    }
    
    all_transfers = []
    cursor = None
    max_pages = 5  # cap at 500 transfers per seed to control cost
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    
    try:
        # For seed-specific address, we need /address/{addr}/transfers
        # but filtering by token contract inside our loop
        page_url = f"{STARKSCAN_BASE}/address/{address}/transfers?limit=100"
        
        for page_num in range(max_pages):
            if cursor:
                page_url_full = page_url + f"&cursor={cursor}"
            else:
                page_url_full = page_url
            
            req = urllib.request.Request(page_url_full, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            
            items = data.get('data', data.get('items', []))
            if not items:
                break
            
            page_had_recent = False
            for tx in items:
                # Only STRK token transfers
                tx_token = tx.get('tokenAddress') or tx.get('token_address') or ''
                tx_token_normalized = normalize_stark_address(tx_token)
                if tx_token_normalized != token_contract:
                    continue
                
                # Parse timestamp
                ts_iso = tx.get('timestampIso') or tx.get('timestamp_iso') or ''
                try:
                    ts_dt = datetime.fromisoformat(ts_iso.replace('Z', '+00:00'))
                    ts = int(ts_dt.timestamp())
                except (ValueError, AttributeError):
                    continue
                
                if ts < cutoff_ts:
                    # Continue but don't count as recent (paginated in DESC order)
                    continue
                page_had_recent = True
                
                # Parse amount (raw uint256 → STRK)
                amount_raw = tx.get('amount', tx.get('value', '0'))
                try:
                    amount_strk = int(amount_raw) / 1e18
                except (ValueError, TypeError):
                    continue
                
                all_transfers.append({
                    'tx_hash': tx.get('txHash') or tx.get('tx_hash', ''),
                    'block_number': tx.get('blockNumber') or tx.get('block_number', 0),
                    'timestamp': ts,
                    'timestamp_iso': ts_iso,
                    'from_address': normalize_stark_address(tx.get('fromAddress') or tx.get('from_address', '')),
                    'to_address': normalize_stark_address(tx.get('toAddress') or tx.get('to_address', '')),
                    'amount_strk': amount_strk,
                })
            
            if not page_had_recent:
                # Reached beyond lookback window
                break
            
            cursor = data.get('nextCursor') or data.get('next_cursor')
            if not cursor:
                break
            
            time.sleep(0.3)  # rate limit
    
    except Exception as e:
        logger.error(f"Starkscan fetch failed for {address[:12]}...: {e}")
        return []
    
    logger.info(f"  {address[:12]}...: {len(all_transfers)} STRK transfers in last {lookback_days}d")
    return all_transfers


# ============================================================
# EDGE BUILDING (same as flow_eth.py, uses 'starknet' chain tag)
# ============================================================

def build_edges(seed_info: Dict, transfers: List[Dict]) -> List[Dict]:
    edges = []
    seed_addr = seed_info['address']
    
    for t in transfers:
        direction = 'in' if t['to_address'] == seed_addr else 'out'
        counterparty = t['from_address'] if direction == 'in' else t['to_address']
        
        edges.append({
            'seed_name': seed_info['name'],
            'seed_category': seed_info['category'],
            'chain': 'starknet',
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
    output = {
        'as_of': meta['as_of'],
        'chain': 'starknet',
        'token_contract': meta['token_contract'],
        'lookback_days': meta['lookback_days'],
        'seeds_processed': meta['seeds_processed'],
        'total_edges': meta['total_edges'],
        'not_checked': False,
        'flow_class': None,
        'route': None,
        'new_addresses': [],
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
    parser.add_argument('--seed', help='Single seed address')
    parser.add_argument('--deep', action='store_true', help='30-day lookback')
    parser.add_argument('--dry-run', action='store_true', help='No API calls')
    args = parser.parse_args()
    
    lookback = FLOW_LOOKBACK_DEEP_DAYS if args.deep else FLOW_LOOKBACK_DAYS
    logger.info(f"=" * 60)
    logger.info(f"flow_starknet.py starting · lookback={lookback}d · deep={args.deep}")
    
    try:
        seeds = load_seeds()
        token_contract = load_token_contract()
    except FileNotFoundError as e:
        logger.error(f"Registry file missing: {e}")
        return 1
    
    logger.info(f"STRK L2 token contract: {token_contract}")
    
    seed_list = extract_l2_seed_addresses(seeds)
    
    if args.seed:
        seed_list = [s for s in seed_list if s['address'] == args.seed.lower()]
        if not seed_list:
            logger.error(f"Seed {args.seed} not found in L2 registry")
            return 1
    
    logger.info(f"Processing {len(seed_list)} L2 SEED addresses")
    
    if args.dry_run:
        for s in seed_list:
            logger.info(f"  [DRY] Would fetch: {s['name']} @ {s['address']}")
        return 0
    
    if not STARKSCAN_API_KEY:
        logger.error("STARKSCAN_API_KEY not set.")
        return 1
    
    all_edges = []
    all_summaries = []
    
    for seed_info in seed_list:
        logger.info(f"Fetching {seed_info['name']} ({seed_info['address'][:12]}...)")
        transfers = fetch_transfers(seed_info['address'], token_contract, lookback)
        edges = build_edges(seed_info, transfers)
        summary = build_seed_summary(seed_info, edges)
        all_edges.extend(edges)
        all_summaries.append(summary)
        time.sleep(0.5)
    
    edges_csv = CACHE_DIR / 'flow_starknet_edges.csv'
    summary_json = CACHE_DIR / 'flow_starknet_summary.json'
    
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
    
    logger.info(f"=" * 60)
    logger.info(f"DONE · {len(all_edges)} edges from {len(seed_list)} L2 seeds")
    return 0


if __name__ == '__main__':
    sys.exit(main())
