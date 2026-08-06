#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
historical_snapshots.py — сбор on-chain snapshots для 6 значимых событий
========================================================================

Собирает on-chain flow данные ЗА 7 И 14 ДНЕЙ ДО каждого события,
чтобы проверить работал ли бы наш детектор.

События (из истории STRK):
  · Rally #1  · 2024-11-05 → 2024-12-07 (+135%)
  · Crash #1  · 2024-12-07 → 2025-04-07 (-86%)
  · Rally #2  · 2025-11-03 → 2025-11-20 (+175%)
  · Crash #2  · 2025-11-20 → 2026-04-14 (-88%)
  · Rally #3  · 2026-04-14 → 2026-05-09 (+99%)
  · Crash #3  · 2026-05-09 → 2026-08-03 (-56%)

Usage:
    python3 historical_snapshots.py
"""

import os
import sys
import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List

SCRIPT_DIR = Path(__file__).parent.parent.parent  # .../STRK_Engine
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
TOKENS_FILE = SCRIPT_DIR / 'config' / 'tokens.json'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
STARKSCAN_BASE = 'https://api.starkscan.co/api/v1/SN_MAIN'
DEFILLAMA_TVL = 'https://api.llama.fi/v2/historicalChainTvl/Starknet'

ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STARKSCAN_API_KEY = os.environ.get('STARKSCAN_API_KEY', '')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('history')


# ============================================================
# EVENTS (from user's historical data)
# ============================================================

EVENTS = [
    {'name': 'Rally_1_start', 'date': '2024-11-05', 'type': 'rally_start', 'expected_move_pct': 135},
    {'name': 'Crash_1_start', 'date': '2024-12-07', 'type': 'crash_start', 'expected_move_pct': -86},
    {'name': 'Rally_2_start', 'date': '2025-11-03', 'type': 'rally_start', 'expected_move_pct': 175},
    {'name': 'Crash_2_start', 'date': '2025-11-20', 'type': 'crash_start', 'expected_move_pct': -88},
    {'name': 'Rally_3_start', 'date': '2026-04-14', 'type': 'rally_start', 'expected_move_pct': 99},
    {'name': 'Crash_3_start', 'date': '2026-05-09', 'type': 'crash_start', 'expected_move_pct': -56},
]

# Also collect a couple of NEGATIVE examples (dates where NOTHING happened)
# to check false-positive rate
CONTROL_DATES = [
    {'name': 'Control_A_quiet', 'date': '2025-07-15', 'type': 'quiet', 'expected_move_pct': 0},
    {'name': 'Control_B_quiet', 'date': '2025-08-20', 'type': 'quiet', 'expected_move_pct': 0},
    {'name': 'Control_C_quiet', 'date': '2026-02-15', 'type': 'quiet', 'expected_move_pct': 0},
]


# ============================================================
# HELPERS
# ============================================================

def normalize_stark_address(addr):
    if not addr or not isinstance(addr, str):
        return addr
    addr = addr.lower().strip()
    if not addr.startswith('0x'):
        return addr
    hex_part = addr[2:].lstrip('0')
    if not hex_part:
        hex_part = '0'
    return '0x' + hex_part


def load_seeds():
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_all_seeds():
    seeds = load_seeds()
    result = []
    SKIP = {'_meta', '_phantoms'}
    for cat, data in seeds.items():
        if cat in SKIP or not isinstance(data, dict):
            continue
        for name, entry in data.items():
            if name.startswith('_') or not isinstance(entry, dict):
                continue
            addr = entry.get('address', '')
            if not addr or addr.startswith('TBD'):
                continue
            chain = 'starknet' if cat == 'l2_native' else 'ethereum'
            if chain == 'starknet' and name == 'vstrk_governance':
                continue  # token, not seed
            result.append({
                'name': name,
                'address': normalize_stark_address(addr) if chain == 'starknet' else addr.lower(),
                'category': cat,
                'chain': chain,
                'role': entry.get('role', ''),
            })
    return result


# ============================================================
# ETHERSCAN — historical L1 transfers with time range
# ============================================================

def fetch_l1_transfers_by_time(address, token_contract, from_ts, to_ts):
    """Fetch L1 transfers for address in a specific time window."""
    # Etherscan V2 doesn't have direct time filter — use large offset + filter locally
    params = {
        'chainid': 1,
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': token_contract,
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'page': 1,
        'offset': 1000,   # up to 1000 recent txs
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    }
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.error(f"Etherscan error for {address[:12]}: {e}")
        return []
    
    if data.get('status') != '1':
        if data.get('message') != 'No transactions found':
            logger.warning(f"Etherscan status 0 for {address[:12]}: {data.get('message')}")
        return []
    
    filtered = []
    for tx in data.get('result', []):
        try:
            ts = int(tx['timeStamp'])
            if from_ts <= ts <= to_ts:
                filtered.append({
                    'ts': ts,
                    'from': tx['from'].lower(),
                    'to': tx['to'].lower(),
                    'amount_strk': int(tx['value']) / (10 ** int(tx.get('tokenDecimal', 18))),
                    'tx_hash': tx['hash'],
                })
        except (KeyError, ValueError):
            continue
    
    return filtered


# ============================================================
# STARKSCAN — historical L2 transfers
# ============================================================

def fetch_l2_transfers_by_time(address, token_contract, from_ts, to_ts):
    """Fetch L2 transfers for address in time window (paginate as needed)."""
    if not STARKSCAN_API_KEY:
        return []
    
    all_txs = []
    cursor = None
    max_pages = 8   # cap 800 transfers per seed
    headers = {
        'X-Starkscan-Api-Key': STARKSCAN_API_KEY,
        'User-Agent': 'STRK-Engine/1.0',
    }
    
    for page in range(max_pages):
        url = f"{STARKSCAN_BASE}/address/{address}/transfers?limit=100"
        if cursor:
            url += f"&cursor={cursor}"
        
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.error(f"Starkscan error for {address[:12]}: {e}")
            break
        
        items = data.get('data') or data.get('items') or []
        if not items:
            break
        
        oldest_in_page = None
        for tx in items:
            tx_token = normalize_stark_address(tx.get('tokenAddress', ''))
            if tx_token != token_contract:
                continue
            
            try:
                ts_iso = tx.get('timestampIso', '')
                ts_dt = datetime.fromisoformat(ts_iso.replace('Z', '+00:00'))
                ts = int(ts_dt.timestamp())
                oldest_in_page = ts if oldest_in_page is None else min(oldest_in_page, ts)
            except (ValueError, AttributeError):
                continue
            
            if from_ts <= ts <= to_ts:
                try:
                    amt = int(tx.get('amount', 0)) / 1e18
                except (ValueError, TypeError):
                    continue
                all_txs.append({
                    'ts': ts,
                    'from': normalize_stark_address(tx.get('fromAddress', '')),
                    'to': normalize_stark_address(tx.get('toAddress', '')),
                    'amount_strk': amt,
                    'tx_hash': tx.get('txHash', ''),
                })
        
        # If oldest in page is before window → we've paginated past the window
        if oldest_in_page and oldest_in_page < from_ts:
            break
        
        cursor = data.get('nextCursor') or data.get('next_cursor')
        if not cursor:
            break
        
        time.sleep(0.3)
    
    return all_txs


# ============================================================
# TVL history from DefiLlama
# ============================================================

def fetch_tvl_around_date(target_date):
    """TVL at target_date, 7d ago, 14d ago."""
    try:
        req = urllib.request.Request(DEFILLAMA_TVL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.error(f"TVL fetch error: {e}")
        return None
    
    target_ts = int(target_date.timestamp())
    
    def nearest(days_offset):
        want_ts = target_ts - days_offset * 86400
        best = None
        best_diff = float('inf')
        for row in data:
            diff = abs(row['date'] - want_ts)
            if diff < best_diff:
                best_diff = diff
                best = row
        return best
    
    on_date = nearest(0)
    d7 = nearest(7)
    d14 = nearest(14)
    d30 = nearest(30)
    
    if not on_date:
        return None
    
    return {
        'tvl_at_date': on_date['tvl'],
        'tvl_7d_ago': d7['tvl'] if d7 else None,
        'tvl_14d_ago': d14['tvl'] if d14 else None,
        'tvl_30d_ago': d30['tvl'] if d30 else None,
        'trend_7d_pct': (on_date['tvl'] / d7['tvl'] - 1) * 100 if d7 and d7['tvl'] > 0 else None,
        'trend_14d_pct': (on_date['tvl'] / d14['tvl'] - 1) * 100 if d14 and d14['tvl'] > 0 else None,
        'trend_30d_pct': (on_date['tvl'] / d30['tvl'] - 1) * 100 if d30 and d30['tvl'] > 0 else None,
    }


# ============================================================
# BUILD SNAPSHOT for one event date
# ============================================================

def build_snapshot(event, all_seeds, l1_token, l2_token):
    event_date = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    window_end_ts = int(event_date.timestamp())
    window_7d_start = int((event_date - timedelta(days=7)).timestamp())
    window_14d_start = int((event_date - timedelta(days=14)).timestamp())
    
    logger.info(f"\n=== {event['name']} · {event['date']} ({event['type']}) ===")
    logger.info(f"Windows: 7d [{datetime.fromtimestamp(window_7d_start, timezone.utc).date()}]"
                f" - 14d [{datetime.fromtimestamp(window_14d_start, timezone.utc).date()}]")
    
    snapshot = {
        'event': event,
        'as_of': event_date.isoformat(),
        'seeds_7d': {},
        'seeds_14d': {},
        'tvl': None,
    }
    
    # TVL
    logger.info("  Fetching TVL history...")
    snapshot['tvl'] = fetch_tvl_around_date(event_date)
    if snapshot['tvl']:
        logger.info(f"    TVL 7d trend: {snapshot['tvl'].get('trend_7d_pct'):.2f}%"
                    if snapshot['tvl'].get('trend_7d_pct') is not None else "    TVL: no data")
    
    # Per-seed flows
    for seed in all_seeds:
        seed_key = f"{seed['chain']}_{seed['name']}"
        
        # 7-day window
        if seed['chain'] == 'ethereum':
            txs_7d = fetch_l1_transfers_by_time(seed['address'], l1_token, window_7d_start, window_end_ts)
            time.sleep(0.3)
        else:
            txs_7d = fetch_l2_transfers_by_time(seed['address'], l2_token, window_7d_start, window_end_ts)
            time.sleep(0.5)
        
        vol_in = sum(t['amount_strk'] for t in txs_7d if t['to'] == seed['address'])
        vol_out = sum(t['amount_strk'] for t in txs_7d if t['from'] == seed['address'])
        cp_in = len(set(t['from'] for t in txs_7d if t['to'] == seed['address']))
        cp_out = len(set(t['to'] for t in txs_7d if t['from'] == seed['address']))
        
        snapshot['seeds_7d'][seed_key] = {
            'seed_name': seed['name'],
            'chain': seed['chain'],
            'category': seed['category'],
            'role': seed['role'],
            'edges_count': len(txs_7d),
            'vol_in_strk': round(vol_in, 2),
            'vol_out_strk': round(vol_out, 2),
            'net_flow_strk': round(vol_in - vol_out, 2),
            'cp_in': cp_in,
            'cp_out': cp_out,
        }
        
        if len(txs_7d) > 0:
            logger.info(f"  {seed['name']:35s} [{seed['chain']:8}] "
                       f"in={vol_in:>12,.0f} out={vol_out:>12,.0f} net={vol_in-vol_out:>+12,.0f}")
    
    return snapshot


# ============================================================
# MAIN
# ============================================================

def main():
    if not ETHERSCAN_API_KEY or not STARKSCAN_API_KEY:
        logger.error("API keys not set. Set ETHERSCAN_API_KEY and STARKSCAN_API_KEY.")
        return 1
    
    # Load token contracts
    with open(TOKENS_FILE, 'r', encoding='utf-8') as f:
        tokens = json.load(f)
    l1_token = tokens['ethereum_l1']['strk_erc20']['address'].lower()
    l2_token = normalize_stark_address(tokens['starknet_l2']['strk_native']['address'])
    
    logger.info(f"L1 token: {l1_token}")
    logger.info(f"L2 token: {l2_token}")
    
    all_seeds = extract_all_seeds()
    logger.info(f"Processing {len(all_seeds)} seeds across {len(EVENTS) + len(CONTROL_DATES)} dates")
    
    # Build snapshots for events + controls
    all_dates = EVENTS + CONTROL_DATES
    
    for event in all_dates:
        snap = build_snapshot(event, all_seeds, l1_token, l2_token)
        output_file = HISTORY_DIR / f"{event['name']}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(snap, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"  → saved to {output_file.name}")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"DONE · {len(all_dates)} snapshots saved to {HISTORY_DIR}")
    logger.info(f"Next: run scripts/detectors/validate_detector.py to test rules against these")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
