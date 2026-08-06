#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cohort_tracker.py — Отслеживает поведение 4 когорт + append-only history log

4 когорты:
  1. CUSTODY vaults (Binance cold, Coinbase custody, OKX cold)
  2. SMART accumulators (watchlist smart_accumulator_*)
  3. EXCHANGE hot wallets (Binance_1, Coinbase_1 etc.)
  4. NEW receivers (адреса которые получили STRK за последние 14 дней впервые)

Для каждой когорты за 24h/7d:
  · Total balance change (STRK)
  · Activity (# transactions)
  · Net flow (buy - sell) 
  · Interpretation (accumulating / distributing / neutral)

Append-only history log в data/history/cohort_snapshots.jsonl:
  · Каждый snapshot: timestamp + метрики всех когорт
  · Weekly aggregation script может анализировать trends
  · Строить график поведения когорт со временем

Каждый snapshot добавляется в конец файла — не переписывает старые.
"""

import os
import sys
import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = CACHE_DIR / 'cohort_tracker.json'
HISTORY_LOG = HISTORY_DIR / 'cohort_snapshots.jsonl'  # append-only

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_TOKEN = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('cohort')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def load_seeds():
    """Load flow_seeds.json for cohort membership."""
    p = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def api_call(params, timeout=20):
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"API error: {e}")
        return None


def get_balance_strk(address):
    """Get current STRK balance."""
    result = api_call({
        'chainid': 1, 'module': 'account', 'action': 'tokenbalance',
        'contractaddress': STRK_TOKEN, 'address': address,
        'tag': 'latest', 'apikey': ETHERSCAN_API_KEY,
    })
    if result and result.get('status') == '1':
        try:
            return int(result['result']) / 10**18
        except Exception:
            return 0
    return 0


def get_transfer_stats(address, hours_back=24):
    """Get in/out transfer stats for the address."""
    now = datetime.now(timezone.utc)
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    # Get block for the from time
    block_res = api_call({
        'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
        'timestamp': from_ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY,
    })
    if not block_res or block_res.get('status') != '1':
        return {'inflow': 0, 'outflow': 0, 'txs': 0}
    from_block = int(block_res['result'])
    time.sleep(0.3)
    
    # Get ERC-20 transfers
    result = api_call({
        'chainid': 1, 'module': 'account', 'action': 'tokentx',
        'contractaddress': STRK_TOKEN, 'address': address,
        'startblock': from_block, 'endblock': 99999999,
        'page': 1, 'offset': 200, 'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    })
    
    inflow = 0
    outflow = 0
    txs = 0
    if result and result.get('status') == '1':
        for tx in result.get('result', [])[:200]:
            try:
                value = int(tx['value']) / 10**18
                if tx.get('to', '').lower() == address.lower():
                    inflow += value
                else:
                    outflow += value
                txs += 1
            except Exception:
                continue
    return {'inflow': inflow, 'outflow': outflow, 'txs': txs}


def classify_cohort_behavior(net_flow_pct):
    """Classify based on net flow as % of balance."""
    if net_flow_pct > 5:
        return 'ACCUMULATING'
    elif net_flow_pct > 1:
        return 'MILD_ACCUMULATING'
    elif net_flow_pct < -5:
        return 'DISTRIBUTING'
    elif net_flow_pct < -1:
        return 'MILD_DISTRIBUTING'
    else:
        return 'NEUTRAL'


def track_cohort(name, addresses, hours_back=24):
    """Track a cohort of addresses. Returns aggregated metrics."""
    total_balance = 0
    total_inflow = 0
    total_outflow = 0
    total_txs = 0
    active_count = 0
    
    for addr_info in addresses:
        addr = addr_info if isinstance(addr_info, str) else addr_info.get('address', '')
        if not addr or not addr.startswith('0x'):
            continue
        
        # Balance (rate limited — sample first N addresses)
        balance = get_balance_strk(addr)
        total_balance += balance
        time.sleep(0.25)
        
        # Recent transfers
        stats = get_transfer_stats(addr, hours_back=hours_back)
        total_inflow += stats['inflow']
        total_outflow += stats['outflow']
        total_txs += stats['txs']
        if stats['txs'] > 0:
            active_count += 1
        time.sleep(0.3)
    
    net_flow = total_inflow - total_outflow
    net_flow_pct = (net_flow / total_balance * 100) if total_balance > 0 else 0
    
    return {
        'cohort': name,
        'address_count': len(addresses),
        'active_addresses': active_count,
        'total_balance_strk': round(total_balance, 2),
        'inflow_strk': round(total_inflow, 2),
        'outflow_strk': round(total_outflow, 2),
        'net_flow_strk': round(net_flow, 2),
        'net_flow_pct': round(net_flow_pct, 2),
        'txs': total_txs,
        'behavior': classify_cohort_behavior(net_flow_pct),
    }


def build_cohorts_from_seeds(seeds):
    """Extract cohort address lists from seeds."""
    cohorts = {
        'CUSTODY': [],       # Cold storage
        'SMART': [],         # Smart accumulators (from watchlist)
        'EXCHANGE_HOT': [],  # CEX hot wallets
        'WATCHLIST': [],     # Everything else being watched
    }
    
    SKIP = {'_meta', '_phantoms'}
    
    for cat, data in seeds.items():
        if cat in SKIP or not isinstance(data, dict):
            continue
        
        cat_lower = cat.lower()
        for name, entry in data.items():
            if name.startswith('_') or not isinstance(entry, dict):
                continue
            addr = entry.get('address', '')
            if not addr:
                continue
            
            # Categorize
            addr_lower = name.lower()
            if 'custody' in cat_lower or 'cold' in cat_lower or 'vault' in addr_lower:
                cohorts['CUSTODY'].append({'address': addr, 'name': name})
            elif 'smart' in addr_lower or 'accumulator' in addr_lower:
                cohorts['SMART'].append({'address': addr, 'name': name})
            elif any(cex in addr_lower for cex in ['binance', 'coinbase', 'okx', 'bybit', 'bingx', 'kraken', 'kucoin']):
                cohorts['EXCHANGE_HOT'].append({'address': addr, 'name': name})
            else:
                cohorts['WATCHLIST'].append({'address': addr, 'name': name})
    
    return cohorts


def snapshot_and_log():
    """Take snapshot, save current + append to history."""
    now = datetime.now(timezone.utc)
    seeds = load_seeds()
    cohorts = build_cohorts_from_seeds(seeds)
    
    logger.info(f"Cohort sizes:")
    for name, addrs in cohorts.items():
        logger.info(f"  {name}: {len(addrs)} addresses")
    
    # Limit to protect rate limits — only track top 3 per cohort
    LIMIT_PER_COHORT = 3
    results = {}
    
    for cohort_name, addresses in cohorts.items():
        if not addresses:
            results[cohort_name] = {
                'cohort': cohort_name,
                'address_count': 0,
                'note': 'No addresses in this cohort',
            }
            continue
        
        logger.info(f"\n  Tracking {cohort_name} (top {min(len(addresses), LIMIT_PER_COHORT)})...")
        sample = addresses[:LIMIT_PER_COHORT]
        results[cohort_name] = track_cohort(cohort_name, sample, hours_back=24)
        logger.info(f"    Balance: {results[cohort_name].get('total_balance_strk', 0)/1e6:.2f}M STRK")
        logger.info(f"    Net flow 24h: {results[cohort_name].get('net_flow_strk', 0)/1e6:+.2f}M ({results[cohort_name].get('behavior', '?')})")
    
    # Aggregate signal
    accumulating = sum(1 for r in results.values() if r.get('behavior') in ('ACCUMULATING', 'MILD_ACCUMULATING'))
    distributing = sum(1 for r in results.values() if r.get('behavior') in ('DISTRIBUTING', 'MILD_DISTRIBUTING'))
    
    if accumulating >= 2 and distributing == 0:
        aggregate_signal = 'COHORTS_ACCUMULATING'
    elif distributing >= 2 and accumulating == 0:
        aggregate_signal = 'COHORTS_DISTRIBUTING'
    elif accumulating > distributing:
        aggregate_signal = 'MIXED_LEAN_BULLISH'
    elif distributing > accumulating:
        aggregate_signal = 'MIXED_LEAN_BEARISH'
    else:
        aggregate_signal = 'COHORTS_NEUTRAL'
    
    snapshot = {
        'timestamp': now.isoformat(),
        'aggregate_signal': aggregate_signal,
        'cohorts': results,
    }
    
    # Save current snapshot (overwrite)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSnapshot saved: {OUTPUT_FILE}")
    
    # Append to history log (append-only .jsonl)
    with open(HISTORY_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
    
    # Count log size
    try:
        with open(HISTORY_LOG, 'r') as f:
            log_lines = sum(1 for _ in f)
        logger.info(f"History log: {HISTORY_LOG} ({log_lines} snapshots total)")
    except Exception:
        pass
    
    return snapshot


def main():
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    logger.info("=" * 60)
    logger.info("COHORT TRACKER · 4 groups + append-only history")
    logger.info("=" * 60)
    
    snapshot = snapshot_and_log()
    
    logger.info(f"\n=== AGGREGATE ===")
    logger.info(f"Signal: {snapshot['aggregate_signal']}")
    for cohort_name, data in snapshot['cohorts'].items():
        if data.get('address_count', 0) == 0:
            continue
        logger.info(f"  {cohort_name}: {data.get('behavior', '?')} "
                   f"(net {data.get('net_flow_strk', 0)/1e6:+.2f}M · {data.get('active_addresses', 0)} active)")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
