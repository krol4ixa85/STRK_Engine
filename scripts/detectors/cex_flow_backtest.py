#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cex_flow_backtest.py — Backtest CEX flow signal on 9 STRK events

For each event date, compute CEX flow classification for 7 days BEFORE
and compare with actual outcome:
  · Rally events: expect STRONG_ACCUMULATION or MILD_ACCUMULATION before
  · Crash events: expect STRONG_DISTRIBUTION or MILD_DISTRIBUTION before
  · Quiet events: expect NEUTRAL or MIXED
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

# Import from cex_flow module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cex_flow import (
    CEX_ADDRESSES, CEX_LOWER, api_call, get_block_at_time,
    aggregate_by_day, classify_flow_signal, STRK_L1
)

SCRIPT_DIR = Path(__file__).parent.parent.parent
VALIDATION_DIR = SCRIPT_DIR / 'data' / 'validation'

ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')

EVENTS = [
    {'name': 'Rally_1_start', 'date': '2024-11-05', 'move_pct': +135, 
     'expected_flow': ['STRONG_ACCUMULATION', 'MILD_ACCUMULATION']},
    {'name': 'Crash_1_start', 'date': '2024-12-07', 'move_pct': -86,
     'expected_flow': ['STRONG_DISTRIBUTION', 'MILD_DISTRIBUTION']},
    {'name': 'Rally_2_start', 'date': '2025-11-03', 'move_pct': +175,
     'expected_flow': ['STRONG_ACCUMULATION', 'MILD_ACCUMULATION']},
    {'name': 'Crash_2_start', 'date': '2025-11-20', 'move_pct': -88,
     'expected_flow': ['STRONG_DISTRIBUTION', 'MILD_DISTRIBUTION']},
    {'name': 'Rally_3_start', 'date': '2026-04-14', 'move_pct': +99,
     'expected_flow': ['STRONG_ACCUMULATION', 'MILD_ACCUMULATION']},
    {'name': 'Crash_3_start', 'date': '2026-05-09', 'move_pct': -56,
     'expected_flow': ['STRONG_DISTRIBUTION', 'MILD_DISTRIBUTION']},
    {'name': 'Control_A_quiet', 'date': '2025-06-15', 'move_pct': 0,
     'expected_flow': ['NEUTRAL', 'MIXED']},
    {'name': 'Control_B_quiet', 'date': '2026-01-20', 'move_pct': 0,
     'expected_flow': ['NEUTRAL', 'MIXED']},
    {'name': 'Control_C_quiet', 'date': '2026-07-10', 'move_pct': 0,
     'expected_flow': ['NEUTRAL', 'MIXED']},
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('cex_bt')


def fetch_cex_flows_at_date(event_date_str, days_back=7):
    """Fetch CEX flows for 7 days BEFORE event."""
    event_dt = datetime.strptime(event_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(event_dt.timestamp())
    from_ts = to_ts - days_back * 86400
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    cex_txs = []
    current = from_block
    
    for _ in range(30):
        data = api_call({'chainid': 1, 'module': 'logs', 'action': 'getLogs',
                        'address': STRK_L1, 'topic0': topic,
                        'fromBlock': current, 'toBlock': to_block,
                        'page': 1, 'offset': 1000, 'apikey': ETHERSCAN_API_KEY})
        if not data or data.get('status') != '1' or not data.get('result'):
            break
        logs = data['result']
        max_block = 0
        for log in logs:
            try:
                topics = log['topics']
                if len(topics) < 3: continue
                from_addr = ('0x' + topics[1][-40:]).lower()
                to_addr = ('0x' + topics[2][-40:]).lower()
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                ts = int(log['timeStamp'], 16)
                max_block = max(max_block, block)
                
                if amount < 100_000: continue
                
                from_is_cex = from_addr in CEX_LOWER
                to_is_cex = to_addr in CEX_LOWER
                
                if to_is_cex and not from_is_cex:
                    cex_txs.append({
                        'ts': ts, 'from': from_addr, 'to': to_addr,
                        'amount': amount, 'direction': 'CEX_INFLOW',
                        'cex_name': CEX_ADDRESSES.get(next(k for k in CEX_ADDRESSES if k.lower() == to_addr), '?')
                    })
                elif from_is_cex and not to_is_cex:
                    cex_txs.append({
                        'ts': ts, 'from': from_addr, 'to': to_addr,
                        'amount': amount, 'direction': 'CEX_OUTFLOW',
                        'cex_name': CEX_ADDRESSES.get(next(k for k in CEX_ADDRESSES if k.lower() == from_addr), '?')
                    })
            except (KeyError, ValueError, StopIteration):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.3)
    
    return cex_txs


def main():
    logger.info("=" * 70)
    logger.info("CEX FLOW BACKTEST · 9 historical STRK events")
    logger.info("=" * 70)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    results = []
    hits = 0
    partials = 0
    misses = 0
    
    for event in EVENTS:
        logger.info(f"\n{'='*60}")
        logger.info(f"{event['name']} · {event['date']} · move {event['move_pct']:+d}%")
        logger.info(f"Expected flow: {event['expected_flow']}")
        
        logger.info("Fetching CEX flows 7d before...")
        txs = fetch_cex_flows_at_date(event['date'], days_back=7)
        logger.info(f"  Found {len(txs)} CEX transactions (>100K STRK)")
        
        if not txs:
            logger.warning("  No CEX flow data")
            continue
        
        days_data = aggregate_by_day(txs)
        classification = classify_flow_signal(days_data)
        
        signal = classification['signal']
        expected = set(event['expected_flow'])
        
        if signal in expected:
            if signal == event['expected_flow'][0]:
                outcome = 'HIT'
                hits += 1
            else:
                outcome = 'PARTIAL'
                partials += 1
        else:
            outcome = 'MISS'
            misses += 1
        
        marker = "✅" if outcome == 'HIT' else ("🟡" if outcome == 'PARTIAL' else "❌")
        logger.info(f"  DETECTED: {signal} · {classification['confidence']}")
        logger.info(f"  Net: {classification['stats']['total_net_strk']/1e6:+.1f}M STRK")
        logger.info(f"  Bullish days: {classification['stats']['bullish_days']} · Bearish: {classification['stats']['bearish_days']}")
        logger.info(f"  Consecutive: bullish={classification['stats']['consecutive_bullish']} bearish={classification['stats']['consecutive_bearish']}")
        logger.info(f"  {marker} {outcome}")
        
        results.append({
            'event': event['name'],
            'date': event['date'],
            'move': event['move_pct'],
            'expected': event['expected_flow'],
            'detected': signal,
            'confidence': classification['confidence'],
            'net_strk': classification['stats']['total_net_strk'],
            'outcome': outcome,
            'interpretation': classification['interpretation'],
        })
        
        time.sleep(2)  # rate limit
    
    total = len(results)
    if total > 0:
        strict_pct = hits / total * 100
        overall_pct = (hits + partials) / total * 100
        
        logger.info(f"\n{'='*70}")
        logger.info(f"CEX FLOW BACKTEST SUMMARY")
        logger.info(f"{'='*70}")
        logger.info(f"Strict hits: {hits}/{total} = {strict_pct:.1f}%")
        logger.info(f"Partial: {partials}/{total}")
        logger.info(f"Misses: {misses}/{total}")
        logger.info(f"Overall: {overall_pct:.1f}%")
        logger.info(f"\nComparison:")
        logger.info(f"  Wyckoff v2 baseline: 66.7%")
        logger.info(f"  CEX flow alone: {overall_pct:.1f}%")
        
        # Save
        output = {
            'as_of': datetime.now(timezone.utc).isoformat(),
            'strict_hits_pct': round(strict_pct, 1),
            'overall_pct': round(overall_pct, 1),
            'hits': hits,
            'partials': partials,
            'misses': misses,
            'events': results,
        }
        with open(VALIDATION_DIR / 'cex_flow_backtest.json', 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
