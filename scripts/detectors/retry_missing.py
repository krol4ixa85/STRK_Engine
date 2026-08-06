#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Retry Rally_2 and Control_C_quiet with slower pace to avoid rate limits.
"""
import os, sys, json, time, logging, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
# Import functions from distribution_shape
from distribution_shape import (
    fetch_transfers, bucketize, api_call, get_block_at_time,
    BUCKETS, KNOWN_CEX, OUTPUT_DIR, ETHERSCAN_API_KEY
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('retry')

MISSING = [
    {'name': 'Rally_2', 'date': '2025-11-03', 'type': 'rally'},
    {'name': 'Control_C_quiet', 'date': '2026-02-15', 'type': 'quiet'},
]


def analyze_event_slow(event):
    d = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(d.timestamp())
    from_ts = int((d - timedelta(days=14)).timestamp())
    
    logger.info(f"\n=== {event['name']} · window {(d-timedelta(days=14)).date()} → {d.date()} ===")
    
    # Use SLOWER pace: 0.6s between calls instead of 0.3s
    time.sleep(1.0)  # cooldown before starting
    txs = fetch_transfers(from_ts, to_ts)
    if not txs:
        logger.error("Still no transfers")
        return None
    
    received = defaultdict(float)
    sent = defaultdict(float)
    for tx in txs:
        received[tx['to']] += tx['amount']
        sent[tx['from']] += tx['amount']
    
    net_receivers = {}
    for addr, r in received.items():
        s = sent.get(addr, 0)
        if r > 100_000 and s < r * 0.5:
            net_receivers[addr] = r - s
    
    buckets, totals = bucketize(net_receivers)
    counts = {name: len(items) for name, items in buckets.items()}
    total_count = sum(counts.values())
    total_amt = sum(totals.values())
    
    logger.info(f"  Total transfers: {len(txs)}, receivers: {total_count}, accumulated: {total_amt:,.0f}")
    for name in ['MICRO', 'SMALL', 'MEDIUM', 'LARGE']:
        logger.info(f"    {name:<7} count={counts[name]:<4}  total={totals[name]:>15,.0f}")
    
    micro_small_count = counts['MICRO'] + counts['SMALL']
    large_count = counts['LARGE']
    ratio_small_to_large = micro_small_count / max(large_count, 1)
    micro_small_amt = totals['MICRO'] + totals['SMALL']
    large_amt = totals['LARGE']
    ratio_amt_small_to_large = micro_small_amt / max(large_amt, 1)
    
    logger.info(f"  RATIOS: S/L cnt={ratio_small_to_large:.2f}  S/L amt={ratio_amt_small_to_large:.3f}")
    
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
    # Load existing
    existing_file = OUTPUT_DIR / 'distribution_shape.json'
    with open(existing_file, 'r', encoding='utf-8') as f:
        existing = json.load(f)
    
    new_results = []
    for event in MISSING:
        for attempt in range(3):
            r = analyze_event_slow(event)
            if r:
                new_results.append(r)
                break
            logger.warning(f"Attempt {attempt+1} failed, sleeping 30s...")
            time.sleep(30)
    
    # Merge
    combined = existing['results'] + new_results
    with open(existing_file, 'w', encoding='utf-8') as f:
        json.dump({'results': combined}, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\nTotal saved: {len(combined)} events")
    return 0


if __name__ == '__main__':
    sys.exit(main())
