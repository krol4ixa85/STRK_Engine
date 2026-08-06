#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyckoff_ensemble.py — v2 baseline + v5 3-day features ensemble

Key insight from backtesting:
  · v2 (14d): 66.7% — best single model
  · v5 (3d): 55.6% but strong on extremes
  · Neither alone reaches 70%+

Ensemble strategy:
  · HIGH confidence ONLY when BOTH models agree
  · MEDIUM confidence when one model strong, other neutral
  · LOW confidence when models disagree
  · This trades recall for precision

Precision target: 75%+ (when we say HIGH, we're right 75%+)
Recall target: 40-50% (we say HIGH only on clear setups)

For user: reduced false signals. When digest says HIGH, act on it.
When digest says LOW/MIXED, stay flat — model doesn't know.
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
OUTPUT_FILE = CACHE_DIR / 'wyckoff_ensemble.json'

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
logger = logging.getLogger('ens')


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


def fetch_features(from_ts, to_ts):
    """Fetch all recipient stats in window."""
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
    
    large = {a: amt for a, amt in recipients.items() if amt >= 1_000_000 and a not in KNOWN_IGNORE}
    medium = {a: amt for a, amt in recipients.items() if 100_000 <= amt < 1_000_000 and a not in KNOWN_IGNORE}
    small = {a: amt for a, amt in recipients.items() if 1_000 <= amt < 100_000 and a not in KNOWN_IGNORE}
    
    total_large = sum(large.values()) or 1
    total_medium = sum(medium.values())
    total_small = sum(small.values())
    shares_large = [amt / total_large for amt in large.values()]
    entropy = -sum(s * math.log2(s) for s in shares_large if s > 0) if shares_large else 0
    
    return {
        'large_count': len(large),
        'medium_count': len(medium),
        'small_count': len(small),
        'entropy_bits': round(entropy, 3),
        'total_small_strk': round(total_small, 2),
        'total_medium_strk': round(total_medium, 2),
        'total_large_strk': round(total_large, 2),
        'small_over_large_pct': round(total_small / total_large * 100, 2) if total_large else 0,
        'ultra_large_count': sum(1 for a in large.values() if a >= 5_000_000),
    }


def classify_v2(features):
    """v2 baseline classifier - 14-day window logic simplified."""
    large = features['large_count']
    ratio = features.get('small_over_large_pct', 0) / 100  # ratio form
    
    signals = {'ACCUMULATION': 0, 'DISTRIBUTION': 0}
    reasons = []
    
    if large < 40:
        signals['ACCUMULATION'] += 2
        reasons.append(f'v2: large<40 ({large})')
    elif large > 100:
        signals['DISTRIBUTION'] += 2
        reasons.append(f'v2: large>100 ({large})')
    elif large > 60:
        signals['DISTRIBUTION'] += 1
        reasons.append(f'v2: large elevated ({large})')
    
    if ratio > 0.25:
        signals['ACCUMULATION'] += 2
        reasons.append(f'v2: retail active ({ratio:.2f})')
    elif ratio < 0.02:
        signals['DISTRIBUTION'] += 2
        reasons.append(f'v2: no retail ({ratio:.3f})')
    
    if signals['ACCUMULATION'] > signals['DISTRIBUTION']:
        return {'signal': 'ACCUMULATION', 'score': signals['ACCUMULATION'], 'reasons': reasons}
    elif signals['DISTRIBUTION'] > signals['ACCUMULATION']:
        return {'signal': 'DISTRIBUTION', 'score': signals['DISTRIBUTION'], 'reasons': reasons}
    else:
        return {'signal': 'NEUTRAL', 'score': 0, 'reasons': reasons}


def classify_v5_3day(features):
    """v5 classifier - 3-day window with top-8 features."""
    votes = {'RALLY': 0, 'CRASH': 0}
    reasons = []
    
    # Entropy
    if features['entropy_bits'] < 2.5:
        votes['RALLY'] += 2
        reasons.append(f'v5: entropy<2.5 ({features["entropy_bits"]})')
    elif features['entropy_bits'] > 4.0:
        votes['CRASH'] += 2
        reasons.append(f'v5: entropy>4.0 ({features["entropy_bits"]})')
    
    # Medium count
    if features['medium_count'] < 40:
        votes['RALLY'] += 2
        reasons.append(f'v5: medium<40 ({features["medium_count"]})')
    elif features['medium_count'] > 80:
        votes['CRASH'] += 2
        reasons.append(f'v5: medium>80 ({features["medium_count"]})')
    
    # Small count
    if features['small_count'] < 250:
        votes['RALLY'] += 1
        reasons.append(f'v5: small<250 ({features["small_count"]})')
    elif features['small_count'] > 500:
        votes['CRASH'] += 1
        reasons.append(f'v5: small>500 ({features["small_count"]})')
    
    # Large count
    if features['large_count'] < 15:
        votes['RALLY'] += 2
        reasons.append(f'v5: large<15 ({features["large_count"]})')
    elif features['large_count'] > 50:
        votes['CRASH'] += 2
        reasons.append(f'v5: large>50 ({features["large_count"]})')
    
    # Ultra large
    if features['ultra_large_count'] < 5:
        votes['RALLY'] += 1
        reasons.append(f'v5: ultra_large<5 ({features["ultra_large_count"]})')
    elif features['ultra_large_count'] > 10:
        votes['CRASH'] += 1
        reasons.append(f'v5: ultra_large>10 ({features["ultra_large_count"]})')
    
    if votes['RALLY'] > votes['CRASH']:
        return {'signal': 'RALLY', 'score': votes['RALLY'], 'reasons': reasons}
    elif votes['CRASH'] > votes['RALLY']:
        return {'signal': 'CRASH', 'score': votes['CRASH'], 'reasons': reasons}
    else:
        return {'signal': 'NEUTRAL', 'score': 0, 'reasons': reasons}


def ensemble_classify(v2_result, v5_result):
    """Combine v2 (accumulation/distribution) with v5 (rally/crash).
    
    Mapping:
      v2 ACCUMULATION ~ v5 RALLY (both bullish)
      v2 DISTRIBUTION ~ v5 CRASH (both bearish)
    
    HIGH confidence: both agree
    MEDIUM: one strong, other neutral
    LOW: disagree
    """
    v2_dir = None
    if v2_result['signal'] == 'ACCUMULATION': v2_dir = 'BULLISH'
    elif v2_result['signal'] == 'DISTRIBUTION': v2_dir = 'BEARISH'
    
    v5_dir = None
    if v5_result['signal'] == 'RALLY': v5_dir = 'BULLISH'
    elif v5_result['signal'] == 'CRASH': v5_dir = 'BEARISH'
    
    # Ensemble logic
    if v2_dir and v5_dir and v2_dir == v5_dir:
        # BOTH AGREE
        confidence = 'HIGH'
        if v2_dir == 'BULLISH':
            signal = 'ACCUMULATION_CONFIRMED'
            summary = 'ACCUMULATION (rally setup)'
        else:
            signal = 'DISTRIBUTION_CONFIRMED'
            summary = 'DISTRIBUTION (crash setup)'
    elif v2_dir and v5_dir and v2_dir != v5_dir:
        # DISAGREE
        confidence = 'LOW'
        signal = 'MIXED'
        summary = f'MIXED (v2 {v2_dir}, v5 {v5_dir})'
    elif v2_dir and not v5_dir:
        # V2 only
        confidence = 'MEDIUM'
        if v2_dir == 'BULLISH':
            signal = 'ACCUMULATION_WEAK'
            summary = 'ACCUMULATION (v5 neutral)'
        else:
            signal = 'DISTRIBUTION_WEAK'
            summary = 'DISTRIBUTION (v5 neutral)'
    elif v5_dir and not v2_dir:
        # V5 only
        confidence = 'MEDIUM'
        if v5_dir == 'BULLISH':
            signal = 'RALLY_SETUP_WEAK'
            summary = 'RALLY setup (v2 neutral)'
        else:
            signal = 'CRASH_SETUP_WEAK'
            summary = 'CRASH setup (v2 neutral)'
    else:
        # Both neutral
        confidence = 'LOW'
        signal = 'NEUTRAL'
        summary = 'No clear signal'
    
    return {
        'signal': signal,
        'summary': summary,
        'confidence': confidence,
        'v2': v2_result,
        'v5': v5_result,
    }


def backtest_ensemble():
    """Backtest ensemble on 9 events."""
    logger.info("=" * 70)
    logger.info("ENSEMBLE v2+v5 BACKTEST")
    logger.info("=" * 70)
    
    hits_high = 0
    total_high = 0
    hits_medium = 0
    total_medium = 0
    all_hits = 0
    total = 0
    
    for event in EVENTS:
        logger.info(f"\n{'='*60}")
        logger.info(f"{event['name']} · {event['type']} · move {event['move']:+d}%")
        
        event_dt = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        to_ts = int(event_dt.timestamp())
        
        # Fetch both windows
        logger.info("  Fetching 3d window...")
        feats_3d = fetch_features(to_ts - 3*86400, to_ts)
        time.sleep(0.5)
        logger.info("  Fetching 14d window...")
        feats_14d = fetch_features(to_ts - 14*86400, to_ts)
        
        if not feats_3d or not feats_14d:
            logger.warning("  Missing data")
            continue
        
        v2 = classify_v2(feats_14d)
        v5 = classify_v5_3day(feats_3d)
        ensemble = ensemble_classify(v2, v5)
        
        logger.info(f"  v2 (14d): {v2['signal']}")
        logger.info(f"  v5 (3d):  {v5['signal']}")
        logger.info(f"  ENSEMBLE: {ensemble['signal']} · {ensemble['confidence']}")
        
        # Determine correctness
        expected_dir = None
        if event['type'] == 'RALLY': expected_dir = 'BULLISH'
        elif event['type'] == 'CRASH': expected_dir = 'BEARISH'
        else: expected_dir = 'NEUTRAL'
        
        detected_dir = None
        if 'ACCUMULATION' in ensemble['signal'] or 'RALLY' in ensemble['signal']:
            detected_dir = 'BULLISH'
        elif 'DISTRIBUTION' in ensemble['signal'] or 'CRASH' in ensemble['signal']:
            detected_dir = 'BEARISH'
        else:
            detected_dir = 'NEUTRAL'
        
        total += 1
        outcome = ''
        if expected_dir == detected_dir:
            all_hits += 1
            outcome = 'HIT'
        elif expected_dir == 'NEUTRAL' and ensemble['confidence'] == 'LOW':
            all_hits += 1  # LOW confidence for quiet is correct
            outcome = 'HIT (quiet as LOW)'
        else:
            outcome = 'MISS'
        
        # Precision @ HIGH
        if ensemble['confidence'] == 'HIGH':
            total_high += 1
            if expected_dir == detected_dir:
                hits_high += 1
        elif ensemble['confidence'] == 'MEDIUM':
            total_medium += 1
            if expected_dir == detected_dir:
                hits_medium += 1
        
        marker = "✅" if 'HIT' in outcome else "❌"
        logger.info(f"  {marker} {outcome}")
        
        time.sleep(1)
    
    logger.info(f"\n{'='*70}")
    logger.info("ENSEMBLE SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"Overall accuracy: {all_hits}/{total} = {all_hits/total*100:.1f}%")
    logger.info(f"HIGH confidence precision: {hits_high}/{total_high} = {hits_high/max(total_high,1)*100:.1f}%")
    logger.info(f"MEDIUM confidence precision: {hits_medium}/{total_medium} = {hits_medium/max(total_medium,1)*100:.1f}%")
    logger.info(f"\nComparison:")
    logger.info(f"  v2 baseline:   66.7%")
    logger.info(f"  v3 HHI:        33.3%")
    logger.info(f"  v4 hybrid:     28.6%")
    logger.info(f"  v5 (3d):       55.6%")
    logger.info(f"  ENSEMBLE:      {all_hits/total*100:.1f}%")
    logger.info(f"  ENSEMBLE HIGH: {hits_high/max(total_high,1)*100:.1f}% (precision when confident)")
    
    return {
        'total_accuracy_pct': round(all_hits/total*100, 1),
        'high_precision_pct': round(hits_high/max(total_high,1)*100, 1),
        'medium_precision_pct': round(hits_medium/max(total_medium,1)*100, 1),
        'high_count': total_high,
        'medium_count': total_medium,
    }


def main():
    if not ETHERSCAN_API_KEY:
        return 1
    
    stats = backtest_ensemble()
    
    with open(VALIDATION_DIR / 'ensemble_backtest.json', 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
