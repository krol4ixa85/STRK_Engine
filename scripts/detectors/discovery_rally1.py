#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discovery_rally1.py — Эксперимент: найти movers перед Rally #1

Задача: за 14 дней ДО 05.11.2024 (Rally #1 start) найти:
  1. Все Ethereum-адреса, получившие >1M STRK
  2. Классифицировать их: HOLDER (retention >90%) / TRANSIT / CEX-related
  3. Проверить сегодня: держат ли эти адреса STRK?

Если найдём HOLDERS с retention >90% которые накапливали в окне
Oct 22 - Nov 5, 2024 — это ранний сигнал который мы упустили.

Метод:
  Etherscan tokentx endpoint возвращает transfers, отсортированные по времени.
  Пагинируем назад от 05.11.2024 до 22.10.2024, собираем всех получателей >1M.
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
OUTPUT_DIR = SCRIPT_DIR / 'data' / 'validation'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

# Rally #1 window
EVENT_DATE = datetime(2024, 11, 5, tzinfo=timezone.utc)
WINDOW_START = EVENT_DATE - timedelta(days=14)
WINDOW_END_TS = int(EVENT_DATE.timestamp())
WINDOW_START_TS = int(WINDOW_START.timestamp())

# Threshold: адреса, получившие суммарно >1M STRK в окне
MIN_RECEIVED_STRK = 1_000_000

# Известные CEX hot wallets для фильтрации
KNOWN_CEX_LABELS = {
    '0x28c6c06298d514db089934071355e5743bf21d60': 'Binance 14',
    '0x21a31ee1afc51d94c2efccaa2092ad1028285549': 'Binance 15',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d': 'Binance 16',
    '0x56eddb7aa87536c09ccc2793473599fd21a8b17f': 'Binance 17',
    '0x9696f59e4d72e237be84ffd425dcad154bf96976': 'Binance 18',
    '0x5a52e96bacdabb82fd05763e25335261b270efcb': 'Binance 25',
    '0xf977814e90da44bfa03b6295a0616a897441acec': 'Binance 8',
    '0xa7efae728d2936e78bda97dc267687568dd593f4': 'OKX',
    '0xe93685f3bba03016f02bd1828badd6195988d950': 'OKX 8',
    '0xf89d7b9c864f589bbf53a82105107622b35eaa40': 'ByBit hot',
    '0x9642b23ed1e01df1092b92641051881a322f5d4e': 'ByBit cold',
    '0x0d0707963952f2fba59dd06f2b425ace40b492fe': 'Gate.io 1',
    '0x1c4b70a3968436b9a0a9cf5205c787eb81bb558c': 'Gate.io 2',
    '0x2f47a1c2db4a3b78cda44eade915c3b19107ddcc': 'Bitget 1',
    '0xce5485cfb26914c5dce00b9baf0580364dafc7a4': 'StarkGate L1 bridge',
    '0xa86309988947559b6e72ef716c5058f479386c0f': 'Coinbase Prime Gas Funder',
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('discovery')


def fetch_paginated_transfers(from_ts, to_ts, max_pages=15):
    """Get all STRK L1 transfers via token address in time window."""
    all_txs = []
    
    # Etherscan tokentx by contract_address (no address filter):
    # We use logs endpoint that returns transfers by block range
    # But easier: query the contract itself with pagination
    
    logger.info(f"Fetching STRK transfers Etherscan V2 tokentx from {datetime.fromtimestamp(from_ts, timezone.utc).date()} to {datetime.fromtimestamp(to_ts, timezone.utc).date()}")
    
    # Etherscan V2 - use logs from token contract
    # Transfer event topic: 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
    # Format: eth_getLogs
    
    # For simpler approach - fetch tokentx for STRK bridge (the biggest single seed)
    # to see what's flowing to bridge, plus scan Foundation multisig
    # But we want ALL flow, not to one address
    # 
    # Better approach: use Etherscan getLogs
    
    transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    
    # Etherscan approx block for Nov 5 2024: ~21100000
    # Approx block for Oct 22 2024: ~21030000
    # ~7000 blocks per day on Ethereum after merge (12s block time)
    # 14 days = ~98000 blocks
    
    # Convert timestamps to approx blocks (Ethereum uses ~12s blocks)
    ETH_GENESIS = 1438269973  # July 30, 2015
    # Very rough conversion: at Nov 2024, block ~21M with 12s blocks
    # Use Etherscan getBlockByTime API
    
    def get_block_at_time(ts):
        params = {
            'chainid': 1,
            'module': 'block',
            'action': 'getblocknobytime',
            'timestamp': ts,
            'closest': 'before',
            'apikey': ETHERSCAN_API_KEY,
        }
        url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            return int(data['result'])
        except Exception as e:
            logger.error(f"Block-by-time failed: {e}")
            return None
    
    from_block = get_block_at_time(from_ts)
    to_block = get_block_at_time(to_ts)
    logger.info(f"Block range: {from_block} - {to_block}")
    time.sleep(0.3)
    
    if not from_block or not to_block:
        return []
    
    # eth_getLogs via Etherscan for Transfer events on STRK contract
    # We paginate by 1000 logs at a time; Etherscan limits to 1000 per response
    current_block = from_block
    
    while current_block < to_block:
        # Query getLogs
        params = {
            'chainid': 1,
            'module': 'logs',
            'action': 'getLogs',
            'address': STRK_L1,
            'topic0': transfer_topic,
            'fromBlock': current_block,
            'toBlock': to_block,
            'page': 1,
            'offset': 1000,
            'apikey': ETHERSCAN_API_KEY,
        }
        url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.error(f"getLogs failed at block {current_block}: {e}")
            break
        
        if data.get('status') != '1' or not data.get('result'):
            if data.get('message', '') and 'No records' not in str(data.get('message')):
                logger.warning(f"getLogs status: {data.get('status')} msg: {data.get('message')}")
            break
        
        logs = data['result']
        
        max_block_in_batch = 0
        for log in logs:
            try:
                # Topics: [transfer_topic, from_padded, to_padded]
                topics = log['topics']
                if len(topics) < 3:
                    continue
                # Parse from/to from padded topics (last 40 chars = address)
                from_addr = '0x' + topics[1][-40:]
                to_addr = '0x' + topics[2][-40:]
                # Data field contains amount (uint256, 32 bytes hex)
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                ts = int(log['timeStamp'], 16)
                
                max_block_in_batch = max(max_block_in_batch, block)
                
                if from_ts <= ts <= to_ts:
                    all_txs.append({
                        'block': block,
                        'ts': ts,
                        'from': from_addr.lower(),
                        'to': to_addr.lower(),
                        'amount': amount,
                        'tx_hash': log['transactionHash'],
                    })
            except (KeyError, ValueError, IndexError) as e:
                continue
        
        logger.info(f"  Batch: {len(logs)} logs, max block {max_block_in_batch}, total collected {len(all_txs)}")
        
        if len(logs) < 1000:
            break  # last page
        
        current_block = max_block_in_batch + 1
        time.sleep(0.3)
    
    return all_txs


def aggregate_by_recipient(txs):
    """Aggregate transfers by recipient — total received in window."""
    received = defaultdict(float)
    sent = defaultdict(float)
    tx_count_in = defaultdict(int)
    tx_count_out = defaultdict(int)
    sources_by_recipient = defaultdict(set)
    destinations_by_sender = defaultdict(set)
    
    for tx in txs:
        received[tx['to']] += tx['amount']
        sent[tx['from']] += tx['amount']
        tx_count_in[tx['to']] += 1
        tx_count_out[tx['from']] += 1
        sources_by_recipient[tx['to']].add(tx['from'])
        destinations_by_sender[tx['from']].add(tx['to'])
    
    return {
        'received': received,
        'sent': sent,
        'tx_count_in': tx_count_in,
        'tx_count_out': tx_count_out,
        'sources_by_recipient': sources_by_recipient,
        'destinations_by_sender': destinations_by_sender,
    }


def get_current_balance(address):
    """Balance today via Etherscan tokenbalance."""
    params = {
        'chainid': 1,
        'module': 'account',
        'action': 'tokenbalance',
        'contractaddress': STRK_L1,
        'address': address,
        'tag': 'latest',
        'apikey': ETHERSCAN_API_KEY,
    }
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if data.get('status') == '1':
            return int(data['result']) / 1e18
    except Exception as e:
        logger.error(f"Balance check failed for {address[:12]}: {e}")
    return None


def classify_role(addr, agg):
    """Classify address role by pattern."""
    received = agg['received'].get(addr, 0)
    sent = agg['sent'].get(addr, 0)
    n_in = agg['tx_count_in'].get(addr, 0)
    n_out = agg['tx_count_out'].get(addr, 0)
    n_sources = len(agg['sources_by_recipient'].get(addr, set()))
    n_dests = len(agg['destinations_by_sender'].get(addr, set()))
    
    if addr in KNOWN_CEX_LABELS:
        return f"CEX ({KNOWN_CEX_LABELS[addr]})"
    
    if received > 0:
        retention = 1 - (sent / received)
    else:
        retention = None
    
    # Multiple sources → 1 destination pattern
    if n_sources >= 5 and n_dests <= 2 and retention is not None and retention > 0.9:
        return "ACCUMULATOR (many→one, holds >90%)"
    if n_sources <= 2 and n_dests >= 10:
        return "DISTRIBUTOR (one→many)"
    if retention is not None and retention < 0.1 and n_in <= 5 and n_out <= 5:
        return "TRANSIT (in ≈ out, few counterparties)"
    if retention is not None and retention > 0.9:
        return "HOLDER (retention >90%)"
    if retention is not None and 0.1 < retention < 0.9:
        return "PARTIAL_HOLDER"
    return "UNCLASSIFIED"


def main():
    logger.info(f"\n{'='*80}")
    logger.info(f"DISCOVERY EXPERIMENT: Rally #1 (05.11.2024)")
    logger.info(f"Window: {WINDOW_START.date()} - {EVENT_DATE.date()} (14 days)")
    logger.info(f"Threshold: recipients with total received >{MIN_RECEIVED_STRK:,.0f} STRK")
    logger.info(f"{'='*80}\n")
    
    # Fetch all Transfer logs in window
    txs = fetch_paginated_transfers(WINDOW_START_TS, WINDOW_END_TS)
    logger.info(f"\nTotal transfers collected: {len(txs)}")
    
    if not txs:
        logger.error("No transfers found. Aborting.")
        return 1
    
    # Aggregate
    agg = aggregate_by_recipient(txs)
    
    # Filter recipients over threshold
    big_recipients = [(addr, amt) for addr, amt in agg['received'].items() if amt >= MIN_RECEIVED_STRK]
    big_recipients.sort(key=lambda x: -x[1])
    
    logger.info(f"Recipients >={MIN_RECEIVED_STRK:,.0f} STRK: {len(big_recipients)}")
    logger.info(f"\n{'RANK':<5} {'ADDRESS':<44} {'RECEIVED':<16} {'SENT':<16} {'RETENTION':<10} {'ROLE'}")
    logger.info("-" * 130)
    
    results = []
    for i, (addr, recv) in enumerate(big_recipients[:50], 1):
        sent = agg['sent'].get(addr, 0)
        retention_pct = (1 - sent/recv) * 100 if recv > 0 else 0
        role = classify_role(addr, agg)
        
        logger.info(f"{i:<5} {addr:<44} {recv:>14,.0f}   {sent:>14,.0f}   {retention_pct:>8.1f}%  {role}")
        results.append({
            'rank': i,
            'address': addr,
            'received_strk': round(recv, 2),
            'sent_strk': round(sent, 2),
            'retention_pct': round(retention_pct, 2),
            'n_sources': len(agg['sources_by_recipient'].get(addr, set())),
            'n_destinations': len(agg['destinations_by_sender'].get(addr, set())),
            'role_in_window': role,
        })
    
    # For top 20 non-CEX recipients — check their balance TODAY to see if they still hold
    logger.info(f"\n{'='*80}")
    logger.info("Checking current balances of non-CEX top recipients...")
    logger.info(f"{'='*80}\n")
    
    non_cex = [r for r in results if 'CEX' not in r['role_in_window']][:15]
    
    for r in non_cex:
        bal = get_current_balance(r['address'])
        time.sleep(0.3)
        if bal is None:
            r['current_balance_strk'] = None
            r['retention_today'] = None
            continue
        r['current_balance_strk'] = round(bal, 2)
        r['retention_today'] = round((bal / r['received_strk']) * 100, 2) if r['received_strk'] > 0 else 0
        
        marker = "★ HOLDER" if r['retention_today'] > 50 else ("• partial" if r['retention_today'] > 5 else "  (empty)")
        logger.info(f"{r['address']:<44} received {r['received_strk']:>12,.0f} · today {bal:>12,.0f} STRK · retention {r['retention_today']:>6.1f}% {marker}")
    
    # Save
    output = {
        'experiment': 'Rally_1_discovery',
        'window_start': WINDOW_START.isoformat(),
        'window_end': EVENT_DATE.isoformat(),
        'threshold_strk': MIN_RECEIVED_STRK,
        'total_transfers_in_window': len(txs),
        'total_recipients_over_threshold': len(big_recipients),
        'top_50': results,
    }
    
    output_file = OUTPUT_DIR / 'rally1_discovery.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\n{'='*80}")
    logger.info(f"SAVED: {output_file}")
    logger.info(f"{'='*80}\n")
    
    # Verdict
    holders_still = [r for r in non_cex if r.get('retention_today') is not None and r['retention_today'] > 50]
    logger.info(f"VERDICT:")
    logger.info(f"  Top-15 non-CEX recipients checked")
    logger.info(f"  Still holding >50% of received: {len(holders_still)}")
    for h in holders_still:
        logger.info(f"    {h['address']} · received {h['received_strk']:,.0f} · today {h.get('current_balance_strk', 0):,.0f}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
