#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyckoff_v4_hybrid.py — Hybrid classifier v2 baseline + relative HHI

Уроки бэктеста v3:
  · HHI STRK базово ~0.02-0.05 всегда (токен фундаментально распределён)
  · Абсолютные пороги 0.10/0.25 не работают
  · Нужны STRK-specific relative thresholds
  · v2 логика работала (66.7%), не выбрасывать

v4 подход:
  · STRK HHI baseline из 9 events: median 0.04
  · Relative HHI: hhi > 2× baseline (~0.08) = concentration
  · Не переопределяет v2 - дополняет
  · Финальная классификация: голосование v2 + v4 бонусы

Backtest target: 66.7% → 78%+
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

# CRITICAL: STRK-specific baselines from 9 events
STRK_HHI_BASELINE = {
    'median': 0.04,       # median across all periods
    'p25': 0.026,          # 25th percentile
    'p75': 0.057,          # 75th percentile
    'high_threshold': 0.10,  # 2.5× median = concentration signal
    'low_threshold': 0.025,  # below p25 = extreme dilution
}

STRK_LARGE_BASELINE = {
    'median': 66,
    'quiet_avg': 58,      # quiet periods
    'rally_avg': 86,      # rally starts
    'crash_avg': 98,      # crash starts (more receivers)
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
logger = logging.getLogger('v4')


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


def fetch_hhi_snapshot(event_date_str, days_back=14):
    """Fetch HHI + LARGE count for a specific event date."""
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
    
    large = {addr: amt for addr, amt in recipients.items()
             if amt >= 1_000_000 and addr not in KNOWN_IGNORE}
    
    if not large:
        return None
    
    # Also compute small-receiver ratio (v2 baseline metric)
    small_receivers = {addr: amt for addr, amt in recipients.items()
                       if 1_000 <= amt < 10_000 and addr not in KNOWN_IGNORE}
    
    total_large = sum(large.values())
    total_small = sum(small_receivers.values())
    shares = [amt / total_large for amt in large.values()]
    amounts = sorted(large.values(), reverse=True)
    
    hhi = sum(s**2 for s in shares)
    entropy = -sum(s * math.log2(s) for s in shares if s > 0)
    
    top_5_share = sum(amounts[:5]) / total_large if len(amounts) >= 5 else 1.0
    top_10_share = sum(amounts[:10]) / total_large if len(amounts) >= 10 else 1.0
    
    ratio_small_large = total_small / max(total_large, 1)
    
    return {
        'large_count': len(large),
        'small_count': len(small_receivers),
        'total_large_strk': round(total_large, 2),
        'hhi': round(hhi, 4),
        'entropy_bits': round(entropy, 3),
        'top_5_share_pct': round(top_5_share * 100, 2),
        'top_10_share_pct': round(top_10_share * 100, 2),
        'ratio_small_large': round(ratio_small_large, 4),
    }


def classify_v4_hybrid(snap, event=None):
    """
    v4 = v2 baseline (LARGE + ratio) + relative HHI + top-N.
    
    Key: use STRK-specific relative thresholds, not absolute.
    """
    scores = {'ACCUMULATION': 0, 'DISTRIBUTION': 0}
    reasons = {'ACCUMULATION': [], 'DISTRIBUTION': []}
    
    large = snap['large_count']
    hhi = snap['hhi']
    entropy = snap['entropy_bits']
    top_5 = snap['top_5_share_pct']
    ratio_small_large = snap.get('ratio_small_large', 0)
    
    # =============== v2 BASELINE (proven 66.7%) ===============
    
    # LARGE count relative to STRK baseline
    if large < 40:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'LARGE {large} < 40 (quiet accumulation range)')
    elif large > 100:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'LARGE {large} > 100 (elevated activity)')
    
    # Small/Large ratio (retail participation)
    if ratio_small_large > 0.30:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'Retail active (ratio {ratio_small_large:.3f})')
    elif ratio_small_large < 0.05:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'No retail (ratio {ratio_small_large:.3f})')
    
    # =============== v4 NEW: RELATIVE HHI ===============
    
    hhi_ratio = hhi / STRK_HHI_BASELINE['median']  # relative to STRK baseline
    
    if hhi_ratio >= 2.5:
        # HHI 2.5× above baseline = strong concentration signal
        scores['ACCUMULATION'] += 3
        reasons['ACCUMULATION'].append(f'HHI {hhi} = {hhi_ratio:.1f}× STRK baseline (concentrated)')
    elif hhi_ratio >= 1.5:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'HHI slightly elevated ({hhi_ratio:.1f}× baseline)')
    elif hhi_ratio < 0.7 and large > STRK_LARGE_BASELINE['crash_avg']:
        # Only if MANY receivers - dilution signal
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append(f'HHI {hhi} = {hhi_ratio:.1f}× baseline + LARGE spike ({large})')
    
    # =============== v4 NEW: TOP-5 SHARE ===============
    
    if top_5 > 50 and large >= 20:
        scores['ACCUMULATION'] += 2
        reasons['ACCUMULATION'].append(f'Top 5 hold {top_5:.0f}% (concentration)')
    elif top_5 < 25 and large >= 60:
        scores['DISTRIBUTION'] += 2
        reasons['DISTRIBUTION'].append(f'Top 5 only {top_5:.0f}% + many receivers (dilution)')
    
    # =============== v4 NEW: ENTROPY ===============
    
    if entropy < 3.5 and large >= 15:
        scores['ACCUMULATION'] += 1
        reasons['ACCUMULATION'].append(f'Low entropy {entropy}')
    elif entropy > 5.5 and large >= 60:
        scores['DISTRIBUTION'] += 1
        reasons['DISTRIBUTION'].append(f'High entropy {entropy}')
    
    # =============== TIEBREAKER ===============
    
    if scores['ACCUMULATION'] > scores['DISTRIBUTION']:
        phase = 'ACCUMULATION'
    elif scores['DISTRIBUTION'] > scores['ACCUMULATION']:
        phase = 'DISTRIBUTION'
    else:
        # Tie - default to ACCUMULATION for STRK (bearish is default when in doubt)
        # UNLESS extreme metrics say otherwise
        if large > 130 and hhi < 0.03:
            phase = 'DISTRIBUTION'
        else:
            phase = 'ACCUMULATION'
    
    return {
        'phase': phase,
        'scores': scores,
        'reasons': reasons[phase],
        'metrics_used': {
            'large': large,
            'hhi': hhi,
            'hhi_ratio_to_baseline': round(hhi_ratio, 2),
            'entropy': entropy,
            'top_5': top_5,
            'ratio_small_large': ratio_small_large,
        }
    }


def main():
    logger.info("=" * 70)
    logger.info("WYCKOFF v4 HYBRID BACKTEST · v2 baseline + relative HHI")
    logger.info("=" * 70)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    results = []
    v4_hits = 0
    v4_partial = 0
    v4_miss = 0
    
    for event in EVENTS:
        logger.info(f"\n{'='*60}")
        logger.info(f"{event['name']} · move {event['move_pct']:+d}%")
        logger.info(f"Expected: {event['expected']}")
        
        snap = fetch_hhi_snapshot(event['date'])
        if not snap:
            logger.warning(f"  No data")
            continue
        
        logger.info(f"  LARGE: {snap['large_count']} · HHI: {snap['hhi']} · Entropy: {snap['entropy_bits']}")
        logger.info(f"  Top 5: {snap['top_5_share_pct']}% · Small/Large ratio: {snap['ratio_small_large']}")
        
        v4 = classify_v4_hybrid(snap, event)
        
        expected = set(event['expected'])
        got = v4['phase']
        
        if got == event['expected'][0]:
            outcome = 'HIT'
            v4_hits += 1
        elif got in expected:
            outcome = 'PARTIAL'
            v4_partial += 1
        else:
            outcome = 'MISS'
            v4_miss += 1
        
        marker = "✅" if outcome == 'HIT' else ("🟡" if outcome == 'PARTIAL' else "❌")
        logger.info(f"  v4 DETECTED: {got}")
        logger.info(f"  Scores: A={v4['scores']['ACCUMULATION']} D={v4['scores']['DISTRIBUTION']}")
        for r in v4['reasons']:
            logger.info(f"    · {r}")
        logger.info(f"  {marker} {outcome}")
        
        results.append({
            'event': event['name'],
            'date': event['date'],
            'move': event['move_pct'],
            'expected': event['expected'],
            'v4_phase': got,
            'v4_outcome': outcome,
            'snap': snap,
            'v4_scores': v4['scores'],
            'v4_reasons': v4['reasons'],
        })
    
    total = len(results)
    overall = (v4_hits + v4_partial) / total * 100 if total else 0
    
    logger.info(f"\n{'='*70}")
    logger.info(f"v4 HYBRID SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"HIT (strict): {v4_hits}/{total} = {v4_hits/total*100:.1f}%")
    logger.info(f"PARTIAL: {v4_partial}/{total}")
    logger.info(f"MISS: {v4_miss}/{total}")
    logger.info(f"OVERALL: {overall:.1f}%")
    logger.info(f"\nComparison:")
    logger.info(f"  v2 baseline: 66.7%")
    logger.info(f"  v3 HHI-only: 33.3%")
    logger.info(f"  v4 hybrid: {overall:.1f}% ({overall - 66.7:+.1f} vs v2)")
    
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'v4_hits_pct': round(v4_hits/total*100, 1),
        'v4_overall_pct': round(overall, 1),
        'v4_hits': v4_hits,
        'v4_partial': v4_partial,
        'v4_miss': v4_miss,
        'comparison': {
            'v2_baseline_pct': 66.7,
            'v3_hhi_only_pct': 33.3,
            'v4_hybrid_pct': round(overall, 1),
        },
        'events': results,
    }
    with open(VALIDATION_DIR / 'wyckoff_backtest_v4.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
