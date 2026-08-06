#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feature_analysis.py — What actually discriminates rally vs crash?

Собираем данные для каждого события за 3 разных окна:
  · 3d before (short-term)
  · 7d before (medium)
  · 14d before (long)

Для каждой feature вычисляем:
  · Mean for RALLY events
  · Mean for CRASH events
  · Mean for QUIET events
  · Discrimination power = |mean_rally - mean_crash| / std

Это раскроет что реально работает vs что overlap'ит.
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
VALIDATION_DIR = SCRIPT_DIR / 'data' / 'validation'

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
    {'name': 'Rally_1', 'date': '2024-11-05', 'type': 'RALLY', 'move': +135},
    {'name': 'Crash_1', 'date': '2024-12-07', 'type': 'CRASH', 'move': -86},
    {'name': 'Rally_2', 'date': '2025-11-03', 'type': 'RALLY', 'move': +175},
    {'name': 'Crash_2', 'date': '2025-11-20', 'type': 'CRASH', 'move': -88},
    {'name': 'Rally_3', 'date': '2026-04-14', 'type': 'RALLY', 'move': +99},
    {'name': 'Crash_3', 'date': '2026-05-09', 'type': 'CRASH', 'move': -56},
    {'name': 'Quiet_A', 'date': '2025-06-15', 'type': 'QUIET', 'move': 0},
    {'name': 'Quiet_B', 'date': '2026-01-20', 'type': 'QUIET', 'move': 0},
    {'name': 'Quiet_C', 'date': '2026-07-10', 'type': 'QUIET', 'move': 0},
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('feat')


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


def fetch_transfers(from_ts, to_ts):
    """Fetch all STRK transfers in window."""
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    all_txs = []
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
                max_block = max(max_block, block)
                all_txs.append({'from': from_addr, 'to': to_addr, 'amount': amount, 'block': block})
            except (KeyError, ValueError):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.3)
    
    return all_txs


def compute_features(txs, window_label):
    """Compute all features from transfer list."""
    recipients = defaultdict(float)
    for tx in txs:
        recipients[tx['to']] += tx['amount']
    
    # Filter LARGE, non-CEX
    large = {a: amt for a, amt in recipients.items()
             if amt >= 1_000_000 and a not in KNOWN_IGNORE}
    medium = {a: amt for a, amt in recipients.items()
              if 100_000 <= amt < 1_000_000 and a not in KNOWN_IGNORE}
    small = {a: amt for a, amt in recipients.items()
             if 1_000 <= amt < 100_000 and a not in KNOWN_IGNORE}
    
    total_large = sum(large.values()) or 1
    total_medium = sum(medium.values()) or 1
    total_small = sum(small.values()) or 1
    
    shares_large = [amt / total_large for amt in large.values()]
    
    features = {
        'window': window_label,
        'total_txs': len(txs),
        'large_count': len(large),
        'medium_count': len(medium),
        'small_count': len(small),
        'total_large_strk': round(total_large, 2),
        'total_medium_strk': round(total_medium, 2),
        'total_small_strk': round(total_small, 2),
    }
    
    # HHI
    features['hhi'] = round(sum(s**2 for s in shares_large), 4) if shares_large else 0
    features['entropy_bits'] = round(-sum(s * math.log2(s) for s in shares_large if s > 0), 3) if shares_large else 0
    
    # Top-N shares
    sorted_amounts = sorted(large.values(), reverse=True)
    if sorted_amounts:
        features['top_1_share_pct'] = round(sorted_amounts[0] / total_large * 100, 2)
        features['top_3_share_pct'] = round(sum(sorted_amounts[:3]) / total_large * 100, 2)
        features['top_5_share_pct'] = round(sum(sorted_amounts[:5]) / total_large * 100, 2)
    else:
        features['top_1_share_pct'] = 0
        features['top_3_share_pct'] = 0
        features['top_5_share_pct'] = 0
    
    # Retail participation ratios
    features['small_over_large_pct'] = round(total_small / total_large * 100, 2)
    features['medium_over_large_pct'] = round(total_medium / total_large * 100, 2)
    
    # Absolute size of largest holder
    features['largest_receiver_strk'] = round(sorted_amounts[0], 2) if sorted_amounts else 0
    
    # Number of ultra-large receivers (>5M)
    features['ultra_large_count'] = sum(1 for a in large.values() if a >= 5_000_000)
    features['mega_large_count'] = sum(1 for a in large.values() if a >= 10_000_000)
    
    return features


def collect_event_features(event, windows=[3, 7, 14, 30]):
    """Get features for each window before event."""
    event_dt = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(event_dt.timestamp())
    
    results = {'event': event['name'], 'type': event['type'], 'move': event['move']}
    
    for w_days in windows:
        from_ts = to_ts - w_days * 86400
        logger.info(f"  Fetching {w_days}d window for {event['name']}...")
        txs = fetch_transfers(from_ts, to_ts)
        logger.info(f"    Got {len(txs)} transfers")
        feats = compute_features(txs, f'{w_days}d')
        results[f'window_{w_days}d'] = feats
    
    return results


def analyze_discrimination(all_results):
    """For each feature, compute discrimination between rally/crash/quiet."""
    features_to_test = [
        'large_count', 'medium_count', 'small_count',
        'total_large_strk', 'total_medium_strk', 'total_small_strk',
        'hhi', 'entropy_bits',
        'top_1_share_pct', 'top_3_share_pct', 'top_5_share_pct',
        'small_over_large_pct', 'medium_over_large_pct',
        'largest_receiver_strk', 'ultra_large_count', 'mega_large_count',
    ]
    
    windows = [3, 7, 14, 30]
    
    analysis = {}
    
    for w_days in windows:
        window_key = f'window_{w_days}d'
        analysis[window_key] = {}
        
        for feat in features_to_test:
            rally_vals = []
            crash_vals = []
            quiet_vals = []
            
            for r in all_results:
                v = r.get(window_key, {}).get(feat)
                if v is None: continue
                if r['type'] == 'RALLY': rally_vals.append(v)
                elif r['type'] == 'CRASH': crash_vals.append(v)
                elif r['type'] == 'QUIET': quiet_vals.append(v)
            
            if not rally_vals or not crash_vals:
                continue
            
            mean_rally = sum(rally_vals) / len(rally_vals)
            mean_crash = sum(crash_vals) / len(crash_vals)
            mean_quiet = sum(quiet_vals) / len(quiet_vals) if quiet_vals else 0
            
            # Discrimination = normalized difference
            all_vals = rally_vals + crash_vals + quiet_vals
            if len(all_vals) > 1:
                mean_all = sum(all_vals) / len(all_vals)
                variance = sum((v - mean_all) ** 2 for v in all_vals) / len(all_vals)
                std = math.sqrt(variance)
                if std > 0:
                    discrimination = abs(mean_rally - mean_crash) / std
                else:
                    discrimination = 0
            else:
                discrimination = 0
            
            analysis[window_key][feat] = {
                'mean_rally': round(mean_rally, 3),
                'mean_crash': round(mean_crash, 3),
                'mean_quiet': round(mean_quiet, 3),
                'discrimination': round(discrimination, 3),
                'rally_values': [round(v, 3) for v in rally_vals],
                'crash_values': [round(v, 3) for v in crash_vals],
                'quiet_values': [round(v, 3) for v in quiet_vals],
            }
    
    return analysis


def main():
    logger.info("=" * 70)
    logger.info("FEATURE DISCRIMINATION ANALYSIS")
    logger.info("=" * 70)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    # Only compute short + medium windows to save time
    all_results = []
    for event in EVENTS:
        logger.info(f"\n{'='*60}")
        logger.info(f"Event: {event['name']} ({event['type']}, {event['move']:+d}%)")
        result = collect_event_features(event, windows=[3, 7, 14])
        all_results.append(result)
    
    # Analyze
    logger.info(f"\n{'='*70}")
    logger.info("FEATURE DISCRIMINATION SCORES")
    logger.info(f"{'='*70}")
    
    analysis = analyze_discrimination(all_results)
    
    for window_key, features in analysis.items():
        logger.info(f"\n--- {window_key} ---")
        sorted_feats = sorted(features.items(), key=lambda x: -x[1]['discrimination'])
        for feat_name, data in sorted_feats[:8]:
            logger.info(f"  {feat_name:30s} disc={data['discrimination']:.2f}  R={data['mean_rally']}  C={data['mean_crash']}  Q={data['mean_quiet']}")
    
    # Save
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'events': all_results,
        'discrimination_analysis': analysis,
    }
    with open(VALIDATION_DIR / 'feature_analysis.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {VALIDATION_DIR / 'feature_analysis.json'}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
