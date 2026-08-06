#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
distribution_shape.py — Эксперимент 1: distribution shape hypothesis

Гипотеза: Перед rally распределение получателей смещено к МЕЛКИМ (smart money накапливает
distributed чтобы не спугнуть цену). Перед quiet/crash — распределение более широкое
или смещено к крупным.

Метод:
  Для каждого из 9 событий (Rally #1-3, Crash #1-3, Control A/B/C):
    · Собрать все STRK L1 Transfer events за 14 дней до
    · Классифицировать получателей по размеру:
        MICRO   100k-500k STRK
        SMALL   500k-1M
        MEDIUM  1M-10M
        LARGE   10M+
    · Отсеять CEX/infra
    · Посчитать shape distribution
    · Сравнить между типами событий

Что ищем:
  · Rally: MICRO+SMALL count больше чем LARGE count?
  · Крупная концентрация в LARGE (кит один накопил) → crash risk?
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
from collections import defaultdict, Counter

SCRIPT_DIR = Path(__file__).parent.parent.parent
OUTPUT_DIR = SCRIPT_DIR / 'data' / 'validation'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

KNOWN_CEX = {
    '0x28c6c06298d514db089934071355e5743bf21d60', '0x21a31ee1afc51d94c2efccaa2092ad1028285549',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d', '0x56eddb7aa87536c09ccc2793473599fd21a8b17f',
    '0x9696f59e4d72e237be84ffd425dcad154bf96976', '0x5a52e96bacdabb82fd05763e25335261b270efcb',
    '0xf977814e90da44bfa03b6295a0616a897441acec', '0xa7efae728d2936e78bda97dc267687568dd593f4',
    '0xe93685f3bba03016f02bd1828badd6195988d950', '0xf89d7b9c864f589bbf53a82105107622b35eaa40',
    '0x9642b23ed1e01df1092b92641051881a322f5d4e', '0xce5485cfb26914c5dce00b9baf0580364dafc7a4',
    '0xa86309988947559b6e72ef716c5058f479386c0f', '0xb1c561105359f549f6e9438867b435580ba3a6b0',
    '0xa8a5b3d0c320ac2ed724169b7f554e3740230586', '0x9b6c368d707481eb215f52b6ced3b81b281ca65c',
    '0x0d0707963952f2fba59dd06f2b425ace40b492fe', '0x1c4b70a3968436b9a0a9cf5205c787eb81bb558c',
    '0x2f47a1c2db4a3b78cda44eade915c3b19107ddcc',
}

EVENTS = [
    {'name': 'Rally_1', 'date': '2024-11-05', 'type': 'rally'},
    {'name': 'Crash_1', 'date': '2024-12-07', 'type': 'crash'},
    {'name': 'Rally_2', 'date': '2025-11-03', 'type': 'rally'},
    {'name': 'Crash_2', 'date': '2025-11-20', 'type': 'crash'},
    {'name': 'Rally_3', 'date': '2026-04-14', 'type': 'rally'},
    {'name': 'Crash_3', 'date': '2026-05-09', 'type': 'crash'},
    {'name': 'Control_A_quiet', 'date': '2025-07-15', 'type': 'quiet'},
    {'name': 'Control_B_quiet', 'date': '2025-08-20', 'type': 'quiet'},
    {'name': 'Control_C_quiet', 'date': '2026-02-15', 'type': 'quiet'},
]

BUCKETS = [
    ('MICRO',  100_000,   500_000),
    ('SMALL',  500_000,   1_000_000),
    ('MEDIUM', 1_000_000, 10_000_000),
    ('LARGE',  10_000_000, float('inf')),
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('shape')


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


def fetch_transfers(from_ts, to_ts):
    from_block = get_block_at_time(from_ts)
    time.sleep(0.3)
    to_block = get_block_at_time(to_ts)
    time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    all_txs = []
    current = from_block
    
    for _ in range(20):
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
                to_addr = '0x' + topics[2][-40:]
                from_addr = '0x' + topics[1][-40:]
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                ts = int(log['timeStamp'], 16)
                max_block = max(max_block, block)
                if from_ts <= ts <= to_ts:
                    all_txs.append({'from': from_addr.lower(), 'to': to_addr.lower(),
                                    'amount': amount, 'ts': ts})
            except (KeyError, ValueError, IndexError):
                continue
        if len(logs) < 1000:
            break
        current = max_block + 1
        time.sleep(0.3)
    
    return all_txs


def bucketize(received_totals):
    """Group addresses by size bucket, exclude CEX."""
    buckets = {name: [] for name, _, _ in BUCKETS}
    total_by_bucket = {name: 0 for name, _, _ in BUCKETS}
    
    for addr, amt in received_totals.items():
        if addr in KNOWN_CEX:
            continue
        for name, lo, hi in BUCKETS:
            if lo <= amt < hi:
                buckets[name].append((addr, amt))
                total_by_bucket[name] += amt
                break
    
    return buckets, total_by_bucket


def analyze_event(event):
    d = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(d.timestamp())
    from_ts = int((d - timedelta(days=14)).timestamp())
    
    logger.info(f"\n=== {event['name']} ({event['type']}) · window {(d-timedelta(days=14)).date()} → {d.date()} ===")
    
    txs = fetch_transfers(from_ts, to_ts)
    if not txs:
        logger.error("No transfers")
        return None
    
    received = defaultdict(float)
    sent = defaultdict(float)
    for tx in txs:
        received[tx['to']] += tx['amount']
        sent[tx['from']] += tx['amount']
    
    # Filter to net receivers (received > sent, retention >50%)
    net_receivers = {}
    for addr, r in received.items():
        s = sent.get(addr, 0)
        if r > 100_000 and s < r * 0.5:  # retention >50%
            net_receivers[addr] = r - s  # net accumulated
    
    buckets, totals = bucketize(net_receivers)
    
    # Metrics
    counts = {name: len(items) for name, items in buckets.items()}
    total_count = sum(counts.values())
    total_amt = sum(totals.values())
    
    logger.info(f"  Total STRK transfers: {len(txs)}")
    logger.info(f"  Non-CEX net receivers (retention >50%, received >100k): {total_count}")
    logger.info(f"  Total net accumulated: {total_amt:,.0f} STRK")
    logger.info(f"")
    
    for name in ['MICRO', 'SMALL', 'MEDIUM', 'LARGE']:
        cnt = counts[name]
        tot = totals[name]
        pct_cnt = cnt / total_count * 100 if total_count > 0 else 0
        pct_amt = tot / total_amt * 100 if total_amt > 0 else 0
        logger.info(f"    {name:<7} count={cnt:<4} ({pct_cnt:>5.1f}%)  total={tot:>15,.0f} ({pct_amt:>5.1f}%)")
    
    # Ratios
    micro_small_count = counts['MICRO'] + counts['SMALL']
    large_count = counts['LARGE']
    ratio_small_to_large = micro_small_count / max(large_count, 1)
    
    micro_small_amt = totals['MICRO'] + totals['SMALL']
    large_amt = totals['LARGE']
    ratio_amt_small_to_large = micro_small_amt / max(large_amt, 1)
    
    logger.info(f"")
    logger.info(f"  RATIOS:")
    logger.info(f"    (MICRO+SMALL) count / LARGE count = {ratio_small_to_large:.2f}")
    logger.info(f"    (MICRO+SMALL) amt / LARGE amt = {ratio_amt_small_to_large:.3f}")
    
    return {
        'event': event,
        'window_start': (d - timedelta(days=14)).isoformat(),
        'window_end': d.isoformat(),
        'total_transfers': len(txs),
        'total_net_receivers': total_count,
        'total_net_accumulated_strk': round(total_amt, 2),
        'counts': counts,
        'totals': {k: round(v, 2) for k, v in totals.items()},
        'ratio_smallcount_over_largecount': round(ratio_small_to_large, 3),
        'ratio_smallamt_over_largeamt': round(ratio_amt_small_to_large, 4),
    }


def main():
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    results = []
    for event in EVENTS:
        r = analyze_event(event)
        if r:
            results.append(r)
        with open(OUTPUT_DIR / 'distribution_shape.json', 'w', encoding='utf-8') as f:
            json.dump({'results': results}, f, indent=2, ensure_ascii=False)
    
    # Comparative table
    logger.info(f"\n{'='*110}")
    logger.info(f"DISTRIBUTION SHAPE COMPARISON")
    logger.info(f"{'='*110}")
    logger.info(f"{'EVENT':<20} {'TYPE':<8} {'MICRO':<8} {'SMALL':<8} {'MEDIUM':<8} {'LARGE':<8} {'S/L cnt':<10} {'S/L amt':<10}")
    logger.info(f"{'-'*110}")
    
    for r in results:
        e = r['event']
        c = r['counts']
        logger.info(f"{e['name']:<20} {e['type']:<8} "
                   f"{c['MICRO']:<8} {c['SMALL']:<8} {c['MEDIUM']:<8} {c['LARGE']:<8} "
                   f"{r['ratio_smallcount_over_largecount']:<10.2f} "
                   f"{r['ratio_smallamt_over_largeamt']:<10.3f}")
    
    # Averages by type
    logger.info(f"\n{'='*110}")
    logger.info(f"AVERAGES BY EVENT TYPE")
    logger.info(f"{'='*110}")
    
    for t in ['rally', 'crash', 'quiet']:
        subset = [r for r in results if r['event']['type'] == t]
        if not subset:
            continue
        avg_micro = sum(r['counts']['MICRO'] for r in subset) / len(subset)
        avg_small = sum(r['counts']['SMALL'] for r in subset) / len(subset)
        avg_medium = sum(r['counts']['MEDIUM'] for r in subset) / len(subset)
        avg_large = sum(r['counts']['LARGE'] for r in subset) / len(subset)
        avg_ratio_c = sum(r['ratio_smallcount_over_largecount'] for r in subset) / len(subset)
        avg_ratio_a = sum(r['ratio_smallamt_over_largeamt'] for r in subset) / len(subset)
        logger.info(f"{t.upper():<10} · MICRO={avg_micro:.1f}  SMALL={avg_small:.1f}  MEDIUM={avg_medium:.1f}  LARGE={avg_large:.1f}  "
                   f"S/L cnt={avg_ratio_c:.2f}  S/L amt={avg_ratio_a:.3f}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
