#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discovery_batch.py — Prognat discovery для всех 9 дат (6 событий + 3 контроля)

Метод (одинаковый для каждого):
  1. Взять 14-дневное окно ДО target даты
  2. Собрать все STRK L1 Transfer events через eth_getLogs
  3. Aгрегировать по получателям
  4. Классифицировать паттерн in-window (retention, sources, destinations)
  5. Для каждого non-CEX получателя >1M — проверить balance сегодня

Итог: словари HOLDER-адресов для каждой даты + компаративный анализ.
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
MIN_RECEIVED = 500_000  # понижаем порог до 500k STRK

# CEX / infra labels
KNOWN_CEX = {
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
    '0xce5485cfb26914c5dce00b9baf0580364dafc7a4': 'StarkGate bridge',
    '0xa86309988947559b6e72ef716c5058f479386c0f': 'Coinbase Prime',
    '0xb1c561105359f549f6e9438867b435580ba3a6b0': 'Team multisig',
    '0xa8a5b3d0c320ac2ed724169b7f554e3740230586': 'Transit bridger 1',
    '0x9b6c368d707481eb215f52b6ced3b81b281ca65c': 'Custody endpoint 1',
}

EVENTS = [
    {'name': 'Rally_1', 'date': '2024-11-05', 'type': 'rally', 'move_pct': 135},
    {'name': 'Crash_1', 'date': '2024-12-07', 'type': 'crash', 'move_pct': -86},
    {'name': 'Rally_2', 'date': '2025-11-03', 'type': 'rally', 'move_pct': 175},
    {'name': 'Crash_2', 'date': '2025-11-20', 'type': 'crash', 'move_pct': -88},
    {'name': 'Rally_3', 'date': '2026-04-14', 'type': 'rally', 'move_pct': 99},
    {'name': 'Crash_3', 'date': '2026-05-09', 'type': 'crash', 'move_pct': -56},
    {'name': 'Control_A_quiet', 'date': '2025-07-15', 'type': 'quiet', 'move_pct': 0},
    {'name': 'Control_B_quiet', 'date': '2025-08-20', 'type': 'quiet', 'move_pct': 0},
    {'name': 'Control_C_quiet', 'date': '2026-02-15', 'type': 'quiet', 'move_pct': 0},
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('batch')


def api_call(params, timeout=30):
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error(f"API error: {e}")
        return None


def get_block_at_time(ts):
    data = api_call({
        'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
        'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY,
    })
    if data and data.get('status') == '1':
        return int(data['result'])
    return None


def fetch_transfer_logs(from_ts, to_ts):
    """Fetch STRK Transfer events in window."""
    transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    
    from_block = get_block_at_time(from_ts)
    time.sleep(0.3)
    to_block = get_block_at_time(to_ts)
    time.sleep(0.3)
    
    if not from_block or not to_block:
        return []
    
    all_txs = []
    current = from_block
    max_pages = 20
    
    for _ in range(max_pages):
        data = api_call({
            'chainid': 1, 'module': 'logs', 'action': 'getLogs',
            'address': STRK_L1, 'topic0': transfer_topic,
            'fromBlock': current, 'toBlock': to_block,
            'page': 1, 'offset': 1000, 'apikey': ETHERSCAN_API_KEY,
        })
        
        if not data or data.get('status') != '1' or not data.get('result'):
            break
        
        logs = data['result']
        max_block = 0
        for log in logs:
            try:
                topics = log['topics']
                if len(topics) < 3:
                    continue
                from_addr = '0x' + topics[1][-40:]
                to_addr = '0x' + topics[2][-40:]
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                ts = int(log['timeStamp'], 16)
                max_block = max(max_block, block)
                
                if from_ts <= ts <= to_ts:
                    all_txs.append({
                        'ts': ts, 'from': from_addr.lower(), 'to': to_addr.lower(),
                        'amount': amount, 'block': block,
                    })
            except (KeyError, ValueError, IndexError):
                continue
        
        if len(logs) < 1000:
            break
        current = max_block + 1
        time.sleep(0.3)
    
    return all_txs


def aggregate(txs):
    """Aggregate transfers by recipient."""
    received = defaultdict(float)
    sent = defaultdict(float)
    sources = defaultdict(set)
    dests = defaultdict(set)
    
    for tx in txs:
        received[tx['to']] += tx['amount']
        sent[tx['from']] += tx['amount']
        sources[tx['to']].add(tx['from'])
        dests[tx['from']].add(tx['to'])
    
    return received, sent, sources, dests


def get_balance(address):
    data = api_call({
        'chainid': 1, 'module': 'account', 'action': 'tokenbalance',
        'contractaddress': STRK_L1, 'address': address, 'tag': 'latest',
        'apikey': ETHERSCAN_API_KEY,
    })
    if data and data.get('status') == '1':
        return int(data['result']) / 1e18
    return None


def analyze_event(event):
    event_date = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(event_date.timestamp())
    from_ts = int((event_date - timedelta(days=14)).timestamp())
    
    logger.info(f"\n{'='*80}")
    logger.info(f"{event['name']} ({event['date']}, {event['type']}, expected {event['move_pct']:+d}%)")
    logger.info(f"Window: {event_date - timedelta(days=14):%Y-%m-%d} - {event_date:%Y-%m-%d}")
    logger.info(f"{'='*80}")
    
    txs = fetch_transfer_logs(from_ts, to_ts)
    logger.info(f"Total STRK transfers in window: {len(txs)}")
    
    if not txs:
        return None
    
    received, sent, sources, dests = aggregate(txs)
    
    # Top recipients over threshold
    big = [(a, r) for a, r in received.items() if r >= MIN_RECEIVED]
    big.sort(key=lambda x: -x[1])
    
    logger.info(f"Recipients >={MIN_RECEIVED:,.0f}: {len(big)}")
    
    non_cex_holders_in_window = []  # retention >90% в окне, non-CEX
    total_accumulated_non_cex = 0.0
    
    for addr, recv in big:
        if addr in KNOWN_CEX:
            continue
        s = sent.get(addr, 0)
        retention_pct = (1 - s/recv) * 100 if recv > 0 else 0
        n_sources = len(sources.get(addr, set()))
        n_dests = len(dests.get(addr, set()))
        
        if retention_pct > 90:
            non_cex_holders_in_window.append({
                'address': addr,
                'received': round(recv, 2),
                'retention_in_window_pct': round(retention_pct, 2),
                'n_sources': n_sources,
                'n_dests_out': n_dests,
            })
            total_accumulated_non_cex += recv
    
    logger.info(f"Non-CEX high-retention holders in window: {len(non_cex_holders_in_window)}")
    logger.info(f"Total non-CEX accumulated: {total_accumulated_non_cex:,.0f} STRK")
    
    # Check current balances for top 5 non-CEX holders
    still_holding_today = 0
    for h in non_cex_holders_in_window[:5]:
        bal = get_balance(h['address'])
        time.sleep(0.3)
        h['balance_today'] = round(bal, 2) if bal else 0
        h['retention_today_pct'] = round((bal / h['received']) * 100, 2) if bal and h['received'] > 0 else 0
        if h.get('retention_today_pct', 0) > 50:
            still_holding_today += 1
    
    result = {
        'event': event,
        'window_start': (event_date - timedelta(days=14)).isoformat(),
        'window_end': event_date.isoformat(),
        'total_transfers': len(txs),
        'total_big_recipients': len(big),
        'non_cex_holders_in_window_count': len(non_cex_holders_in_window),
        'non_cex_total_accumulated_strk': round(total_accumulated_non_cex, 2),
        'top_5_non_cex_still_holding_today': still_holding_today,
        'top_holders_detail': non_cex_holders_in_window[:10],
    }
    
    logger.info(f"  ★ {result['non_cex_holders_in_window_count']} non-CEX holders, ${total_accumulated_non_cex/1e6:.1f}M accumulated")
    logger.info(f"  ★ {still_holding_today}/5 of top holders still hold >50% today")
    
    return result


def main():
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    results = []
    for event in EVENTS:
        r = analyze_event(event)
        if r:
            results.append(r)
        # Save partial progress
        with open(OUTPUT_DIR / 'batch_discovery.json', 'w', encoding='utf-8') as f:
            json.dump({'results': results}, f, indent=2, ensure_ascii=False)
    
    # Summary table
    logger.info(f"\n\n{'='*100}")
    logger.info("COMPARATIVE RESULT")
    logger.info(f"{'='*100}")
    logger.info(f"{'EVENT':<20} {'TYPE':<8} {'MOVE':<8} {'HOLDERS':<10} {'ACCUM STRK':<15} {'STILL HOLD/5'}")
    logger.info(f"{'-'*100}")
    
    for r in results:
        e = r['event']
        move_s = f"{e['move_pct']:+d}%"
        logger.info(f"{e['name']:<20} {e['type']:<8} {move_s:<8} "
                   f"{r['non_cex_holders_in_window_count']:<10} "
                   f"{r['non_cex_total_accumulated_strk']:>13,.0f}  "
                   f"{r['top_5_non_cex_still_holding_today']}/5")
    
    # Rally vs Quiet analysis
    logger.info(f"\n{'='*100}")
    logger.info("RALLY vs QUIET COMPARISON:")
    logger.info(f"{'='*100}")
    
    rallies = [r for r in results if r['event']['type'] == 'rally']
    crashes = [r for r in results if r['event']['type'] == 'crash']
    quiets = [r for r in results if r['event']['type'] == 'quiet']
    
    def avg_metric(items, key):
        vals = [r[key] for r in items]
        return sum(vals) / len(vals) if vals else 0
    
    logger.info(f"Avg non-CEX holders in window (rally):  {avg_metric(rallies, 'non_cex_holders_in_window_count'):.1f}")
    logger.info(f"Avg non-CEX holders in window (crash):  {avg_metric(crashes, 'non_cex_holders_in_window_count'):.1f}")
    logger.info(f"Avg non-CEX holders in window (quiet):  {avg_metric(quiets, 'non_cex_holders_in_window_count'):.1f}")
    logger.info(f"")
    logger.info(f"Avg accumulated STRK (rally): {avg_metric(rallies, 'non_cex_total_accumulated_strk'):,.0f}")
    logger.info(f"Avg accumulated STRK (crash): {avg_metric(crashes, 'non_cex_total_accumulated_strk'):,.0f}")
    logger.info(f"Avg accumulated STRK (quiet): {avg_metric(quiets, 'non_cex_total_accumulated_strk'):,.0f}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
