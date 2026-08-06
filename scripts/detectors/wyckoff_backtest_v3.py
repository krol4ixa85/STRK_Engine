#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyckoff_backtest_v3.py — Backtest Wyckoff v3 with HHI on 9 historical events

Key improvement over v2 calibrator:
  · Adds HISTORICAL HHI computation (Etherscan gives full transfer history)
  · Combines old baseline (distribution shape, structure) with new HHI
  · Honestly notes which metrics unavailable historically (CVD, Effort)

For each historical event we fetch:
  · STRK transfers 14 days BEFORE the event (all transfers, not just seeds)
  · Compute HHI on LARGE receivers only
  · Combine with existing baseline metrics
  · Compare v2 vs v3 predictions

Output:
  · Historical accuracy comparison
  · Which events HHI correctly distinguished
"""

import os
import sys
import json
import math
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
VALIDATION_DIR = SCRIPT_DIR / 'data' / 'validation'
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

KNOWN_IGNORE = {
    '0x28c6c06298d514db089934071355e5743bf21d60', '0x21a31ee1afc51d94c2efccaa2092ad1028285549',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d', '0x56eddb7aa87536c09ccc2793473599fd21a8b17f',
    '0x9696f59e4d72e237be84ffd425dcad154bf96976', '0x5a52e96bacdabb82fd05763e25335261b270efcb',
    '0xf977814e90da44bfa03b6295a0616a897441acec', '0xa7efae728d2936e78bda97dc267687568dd593f4',
    '0xe93685f3bba03016f02bd1828badd6195988d950', '0xf89d7b9c864f589bbf53a82105107622b35eaa40',
    '0x9642b23ed1e01df1092b92641051881a322f5d4e', '0xce5485cfb26914c5dce00b9baf0580364dafc7a4',
    '0xa86309988947559b6e72ef716c5058f479386c0f', '0xb1c561105359f549f6e9438867b435580ba3a6b0',
    '0x0000000000000000000000000000000000000000',
}

EVENTS = [
    {'name': 'Rally_1_start', 'date': '2024-11-05', 'move_pct': +135, 
     'expected': ['ACCUMULATION', 'MARKUP']},
    {'name': 'Crash_1_start', 'date': '2024-12-07', 'move_pct': -86,
     'expected': ['DISTRIBUTION', 'MARKUP']},
    {'name': 'Rally_2_start', 'date': '2025-11-03', 'move_pct': +175,
     'expected': ['ACCUMULATION', 'MARKDOWN']},
    {'name': 'Crash_2_start', 'date': '2025-11-20', 'move_pct': -88,
     'expected': ['DISTRIBUTION', 'MARKUP']},
    {'name': 'Rally_3_start', 'date': '2026-04-14', 'move_pct': +99,
     'expected': ['ACCUMULATION', 'MARKDOWN']},
    {'name': 'Crash_3_start', 'date': '2026-05-09', 'move_pct': -56,
     'expected': ['DISTRIBUTION', 'MARKUP']},
    {'name': 'Control_A_quiet', 'date': '2025-06-15', 'move_pct': 0,
     'expected': ['ACCUMULATION', 'MARKDOWN']},
    {'name': 'Control_B_quiet', 'date': '2026-01-20', 'move_pct': 0,
     'expected': ['ACCUMULATION', 'MARKDOWN']},
    {'name': 'Control_C_quiet', 'date': '2026-07-10', 'move_pct': 0,
     'expected': ['ACCUMULATION', 'MARKDOWN']},
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('backtest')


def api_call(params, timeout=30):
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_block_at_time(ts):
    d = api_call({'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
                  'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY})
    return int(d['result']) if d and d.get('status') == '1' else None


def fetch_historical_hhi(event_date_str, days_back=14):
    """Fetch STRK LARGE receivers over 14 days before event, compute HHI."""
    event_dt = datetime.strptime(event_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(event_dt.timestamp())
    from_ts = to_ts - days_back * 86400
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return None
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    recipients = defaultdict(float)
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
                to_addr = ('0x' + topics[2][-40:]).lower()
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                max_block = max(max_block, block)
                recipients[to_addr] += amount
            except (KeyError, ValueError):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.3)
    
    # Filter LARGE + non-CEX
    large = {addr: amt for addr, amt in recipients.items()
             if amt >= 1_000_000 and addr not in KNOWN_IGNORE}
    
    if not large:
        return None
    
    total = sum(large.values())
    shares = [amt / total for amt in large.values()]
    amounts = sorted(large.values(), reverse=True)
    
    hhi = sum(s**2 for s in shares)
    entropy = -sum(s * math.log2(s) for s in shares if s > 0)
    
    top_5_share = sum(amounts[:5]) / total if len(amounts) >= 5 else 1.0
    top_10_share = sum(amounts[:10]) / total if len(amounts) >= 10 else 1.0
    
    return {
        'large_count': len(large),
        'total_flow_strk': round(total, 2),
        'hhi': round(hhi, 4),
        'entropy_bits': round(entropy, 3),
        'top_5_share_pct': round(top_5_share * 100, 2),
        'top_10_share_pct': round(top_10_share * 100, 2),
    }


def classify_v3(large_count, hhi, entropy, top_5_share, event):
    """Simplified v3 classification using ONLY HHI-based metrics.
    
    This isolates what HHI adds vs v2 baseline (distribution shape only).
    """
    scores = {'ACCUMULATION': 0, 'DISTRIBUTION': 0}
    reasons = {'ACCUMULATION': [], 'DISTRIBUTION': []}
    
    # HHI signals
    if hhi >= 0.25 and large_count >= 20:
        scores['ACCUMULATION'] += 3
        reasons['ACCUMULATION'].append(f'HHI {hhi} concentrated + {large_count} receivers')
    elif hhi >= 0.20 and large_count >= 15:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'HHI {hhi} moderately concentrated')
    
    if hhi < 0.08 and large_count >= 50:
        scores['DISTRIBUTION'] += 4
        reasons['DISTRIBUTION'].append(f'HHI {hhi} very diluted + {large_count} receivers')
    elif hhi < 0.12 and large_count >= 40:
        scores['DISTRIBUTION'] += 3
        reasons['DISTRIBUTION'].append(f'HHI {hhi} diluted + many receivers')
    
    # Top-5 concentration
    if top_5_share > 60 and large_count >= 15:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'Top 5 hold {top_5_share:.0f}%')
    elif top_5_share < 25 and large_count >= 40:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'Top 5 only {top_5_share:.0f}%')
    
    # Entropy
    if entropy < 3.0 and large_count >= 20:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'Low entropy {entropy}')
    elif entropy > 5.0 and large_count >= 40:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'High entropy {entropy}')
    
    # v2 baseline logic (weight reduced but still counted)
    if large_count > 100:
        scores['DISTRIBUTION'] += 1
    if large_count < 40 and hhi > 0.15:
        scores['ACCUMULATION'] += 1
    
    # Winner
    if scores['ACCUMULATION'] > scores['DISTRIBUTION']:
        phase = 'ACCUMULATION'
    elif scores['DISTRIBUTION'] > scores['ACCUMULATION']:
        phase = 'DISTRIBUTION'
    else:
        # Tie - use HHI as tiebreaker
        phase = 'ACCUMULATION' if hhi >= 0.15 else 'DISTRIBUTION'
    
    return {
        'phase': phase,
        'scores': scores,
        'reasons': reasons[phase],
    }


def main():
    logger.info("=" * 70)
    logger.info("WYCKOFF v3 BACKTEST · HHI on 9 historical STRK events")
    logger.info("=" * 70)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    results = []
    v3_hits = 0
    v3_partial = 0
    v3_miss = 0
    
    for event in EVENTS:
        logger.info(f"\n{'='*60}")
        logger.info(f"{event['name']} · {event['date']} · move {event['move_pct']:+d}%")
        logger.info(f"Expected: {event['expected']}")
        
        hhi_data = fetch_historical_hhi(event['date'], days_back=14)
        if not hhi_data:
            logger.warning(f"  No data for {event['name']}")
            continue
        
        logger.info(f"  LARGE count: {hhi_data['large_count']}")
        logger.info(f"  HHI: {hhi_data['hhi']}")
        logger.info(f"  Entropy: {hhi_data['entropy_bits']}")
        logger.info(f"  Top 5 share: {hhi_data['top_5_share_pct']}%")
        
        v3 = classify_v3(
            hhi_data['large_count'],
            hhi_data['hhi'],
            hhi_data['entropy_bits'],
            hhi_data['top_5_share_pct'],
            event
        )
        
        expected_set = set(event['expected'])
        got = v3['phase']
        
        if got == event['expected'][0]:
            outcome = 'HIT'
            v3_hits += 1
        elif got in expected_set:
            outcome = 'PARTIAL'
            v3_partial += 1
        else:
            outcome = 'MISS'
            v3_miss += 1
        
        marker = "✅" if outcome == 'HIT' else ("🟡" if outcome == 'PARTIAL' else "❌")
        logger.info(f"  v3 DETECTED: {got}")
        logger.info(f"  Reasons: {v3['reasons']}")
        logger.info(f"  Scores: A={v3['scores']['ACCUMULATION']} D={v3['scores']['DISTRIBUTION']}")
        logger.info(f"  {marker} {outcome}")
        
        results.append({
            'event': event['name'],
            'date': event['date'],
            'move': event['move_pct'],
            'expected': event['expected'],
            'v3_phase': got,
            'v3_outcome': outcome,
            'hhi': hhi_data['hhi'],
            'entropy': hhi_data['entropy_bits'],
            'large_count': hhi_data['large_count'],
            'top_5_pct': hhi_data['top_5_share_pct'],
            'v3_scores': v3['scores'],
            'v3_reasons': v3['reasons'],
        })
    
    total = len(results)
    logger.info(f"\n{'='*70}")
    logger.info(f"BACKTEST v3 SUMMARY (HHI-focused, 9 events)")
    logger.info(f"{'='*70}")
    logger.info(f"Total: {total}")
    logger.info(f"HIT (strict): {v3_hits}/{total} = {v3_hits/total*100:.1f}%")
    logger.info(f"PARTIAL: {v3_partial}/{total}")
    logger.info(f"MISS: {v3_miss}/{total}")
    logger.info(f"OVERALL (hit+partial): {(v3_hits+v3_partial)/total*100:.1f}%")
    logger.info(f"\nBaseline v2: 66.7% overall")
    logger.info(f"Improvement: {(v3_hits+v3_partial)/total*100 - 66.7:+.1f}%")
    
    # Save
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'baseline_v2_pct': 66.7,
        'v3_hits_pct': round(v3_hits/total*100, 1),
        'v3_overall_pct': round((v3_hits+v3_partial)/total*100, 1),
        'v3_hits': v3_hits,
        'v3_partial': v3_partial,
        'v3_miss': v3_miss,
        'events': results,
    }
    with open(VALIDATION_DIR / 'wyckoff_backtest_v3.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
