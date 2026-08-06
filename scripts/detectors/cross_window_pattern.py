#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_window_pattern.py — Многодневный паттерн-детектор

Ловит момент когда пересекаются несколько окон:
  1. Distribution over 7d, 14d, 30d — что делают крупные wallets
  2. Auto-discovery velocity — сколько новых holders за окно
  3. Funding trend over 3d, 7d, 14d — накапливаются ли шорты/лонги
  4. BTC acceleration — макро разворот

Ищет 4 главных pattern:

PATTERN A · SQUEEZE_SETUP (bullish contrarian)
  · 14d distribution ratio растёт (менее LARGE receivers, больше SMALL holders)
  · funding avg 7d < 0 OR pct_negative_7d > 40%
  · BTC not extreme DOWN (dist200 > -15% или slope30 > -3%)
  · Auto-discovery accepted 3+ candidates за 7d
  → Готовится short squeeze

PATTERN B · SUSTAINED_ACCUMULATION (bullish)
  · Distribution shape stable/improving за 30d
  · Watchlist growing (auto-accepted 5+ за 14d)
  · Общий баланс watched >100M growing
  · BTC UP или DOWN_REVERSING
  → Устойчивое накопление, долгосрочный рост

PATTERN C · DISTRIBUTION_PHASE (bearish)
  · LARGE receivers count >10 за 14d
  · Distribution ratio падает (уменьшается retention)
  · funding avg >5% ann за 7d (лонги crowded)
  · CEX inflow >50% для watched
  → Активная дистрибьюция, готовится сброс

PATTERN D · CAPITULATION (bearish contrarian)
  · CEX outflow watched >90% за 14d (все побежали в CEX)
  · Auto-rejected count > auto-accepted (плохие patterns)
  · funding neg trend + BTC DOWN
  → Капитуляция, дно близко (но не сейчас)

Output:
  · data/cache/cross_window_pattern.json
  · Feed в composite_detector_v2 как дополнительный сигнал (weight 3)
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
DECISION_LOG = CACHE_DIR / 'decision_log.json'
OUTPUT_FILE = CACHE_DIR / 'cross_window_pattern.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('cross_window')


def load_json(path):
    if Path(path).exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


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
    d = api_call({'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
                  'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY})
    return int(d['result']) if d and d.get('status') == '1' else None


def fetch_transfers_window(hours_back):
    """Fetch STRK transfers for a window."""
    import time
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    all_txs = []
    current = from_block
    
    for _ in range(25):
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
                if from_ts <= ts <= to_ts:
                    all_txs.append({'from': from_addr, 'to': to_addr, 'amount': amount, 'ts': ts})
            except (KeyError, ValueError, IndexError):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.4)
    
    return all_txs


def compute_distribution_metrics(txs, label='window'):
    """Compute distribution shape for a set of transfers."""
    if not txs:
        return None
    
    # Aggregate by recipient
    received = defaultdict(float)
    for tx in txs:
        received[tx['to']] += tx['amount']
    
    # Classify
    counts = {'MICRO': 0, 'SMALL': 0, 'MEDIUM': 0, 'LARGE': 0}
    amounts = {'MICRO': 0, 'SMALL': 0, 'MEDIUM': 0, 'LARGE': 0}
    
    for addr, amt in received.items():
        if amt < 10_000:
            bucket = 'MICRO'
        elif amt < 100_000:
            bucket = 'SMALL'
        elif amt < 1_000_000:
            bucket = 'MEDIUM'
        else:
            bucket = 'LARGE'
        counts[bucket] += 1
        amounts[bucket] += amt
    
    total_amt = sum(amounts.values()) or 1
    ratio = (amounts['MICRO'] + amounts['SMALL']) / max(amounts['LARGE'], 1)
    
    return {
        'label': label,
        'total_transfers': len(txs),
        'unique_recipients': len(received),
        'counts': counts,
        'amounts_strk': {k: round(v, 2) for k, v in amounts.items()},
        'ratio_smallamt_over_largeamt': round(ratio, 4),
        'large_receiver_count': counts['LARGE'],
        'total_distributed_strk': round(total_amt, 2),
    }


def get_watchlist_growth():
    """How many auto-accepted addresses per window."""
    log = load_json(DECISION_LOG)
    if not log:
        return {'accepted_7d': 0, 'accepted_14d': 0, 'accepted_30d': 0,
                'rejected_7d': 0}
    
    now = datetime.now(timezone.utc)
    windows = {'7d': 7, '14d': 14, '30d': 30}
    result = {}
    
    for label, days in windows.items():
        cutoff = now - timedelta(days=days)
        accepted = 0
        rejected = 0
        for d in log.get('decisions', []):
            try:
                ts = datetime.fromisoformat(d['ts'].replace('Z', '+00:00'))
                if ts >= cutoff:
                    if d['decision'] == 'ACCEPT':
                        accepted += 1
                    elif d['decision'] == 'REJECT':
                        rejected += 1
            except (ValueError, KeyError):
                continue
        result[f'accepted_{label}'] = accepted
        if label == '7d':
            result['rejected_7d'] = rejected
    
    return result


def get_watchlist_balance_change():
    """Total STRK held by watchlist addresses - simple snapshot."""
    if not SEEDS_FILE.exists():
        return {'watchlist_count': 0, 'total_balance_strk': 0}
    
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        seeds = json.load(f)
    
    watchlist = seeds.get('watchlist', {})
    addrs = []
    for name, entry in watchlist.items():
        if not name.startswith('_') and isinstance(entry, dict):
            a = entry.get('address', '').lower()
            if a and a.startswith('0x') and len(a) == 42:
                addrs.append(a)
    
    # Sum current balances
    import time
    total_bal = 0
    for addr in addrs[:20]:  # cap to avoid rate limits
        try:
            d = api_call({'chainid': 1, 'module': 'account', 'action': 'tokenbalance',
                         'contractaddress': STRK_L1, 'address': addr, 'tag': 'latest',
                         'apikey': ETHERSCAN_API_KEY})
            if d and d.get('status') == '1':
                total_bal += int(d['result']) / 1e18
        except Exception:
            pass
        time.sleep(0.2)
    
    return {'watchlist_count': len(addrs), 'total_balance_strk': round(total_bal, 2)}


def classify_pattern(metrics):
    """Determine which pattern (A/B/C/D) matches."""
    d7 = metrics.get('dist_7d') or {}
    d14 = metrics.get('dist_14d') or {}
    d30 = metrics.get('dist_30d') or {}
    growth = metrics.get('watchlist_growth') or {}
    balance = metrics.get('watchlist_balance') or {}
    
    # Get funding metrics
    funding = load_json(CACHE_DIR / 'funding_signal.json') or {}
    fm = funding.get('funding_metrics') or {}
    
    # Get BTC context
    composite = load_json(CACHE_DIR / 'composite_signal_v2.json') or {}
    btc = (composite.get('inputs', {}) or {}).get('btc_context') or {}
    
    # === Metric extraction ===
    large_14d = d14.get('large_receiver_count', 0)
    ratio_14d = d14.get('ratio_smallamt_over_largeamt', 0)
    ratio_7d = d7.get('ratio_smallamt_over_largeamt', 0)
    ratio_30d = d30.get('ratio_smallamt_over_largeamt', 0)
    
    ratio_trend_improving = ratio_7d > ratio_14d > ratio_30d  # small-holders growing
    ratio_trend_worsening = ratio_7d < ratio_14d < ratio_30d  # small-holders shrinking
    
    accepted_7d = growth.get('accepted_7d', 0)
    accepted_14d = growth.get('accepted_14d', 0)
    rejected_7d = growth.get('rejected_7d', 0)
    
    funding_neg_7d = fm.get('pct_negative_7d', 0)
    funding_avg_7d = fm.get('avg_7d_pct', 0)
    funding_min_7d = fm.get('min_ann_7d', 0)
    short_crowded = fm.get('short_crowded', False)
    long_crowded = fm.get('long_crowded', False)
    
    btc_cycle = btc.get('cycle', 'NEUTRAL')
    btc_dist200 = btc.get('dist200_pct', 0)
    btc_slope30 = btc.get('slope30_pct', 0)
    btc_accel = btc.get('acceleration', 0)
    
    total_watched = balance.get('total_balance_strk', 0)
    
    patterns_detected = []
    
    # === PATTERN A · SQUEEZE_SETUP ===
    squeeze_conditions = {
        'short_crowded': short_crowded,
        'not_bearish_btc': btc_dist200 > -15 or btc_accel > 0,
        'auto_accepted_recent': accepted_7d >= 2,
        'funding_extreme_or_neg': funding_min_7d < -8 or funding_neg_7d > 40,
    }
    if sum(1 for v in squeeze_conditions.values() if v) >= 3:
        patterns_detected.append({
            'pattern': 'SQUEEZE_SETUP',
            'signal': 'BULLISH_CONTRARIAN',
            'confidence': 'HIGH' if all(squeeze_conditions.values()) else 'MEDIUM',
            'conditions_met': squeeze_conditions,
            'reason': f"Short-crowded ({funding_min_7d:+.1f}% min funding) + {accepted_7d} new holders 7d + BTC not extreme down. Squeeze fuel accumulating.",
        })
    
    # === PATTERN B · SUSTAINED_ACCUMULATION ===
    accumulation_conditions = {
        'watchlist_growing': accepted_14d >= 3,
        'shape_improving_or_stable': ratio_trend_improving or (0.05 < ratio_14d < 0.30),
        'btc_supportive': btc_cycle in ('UP', 'DOWN_REVERSING', 'NEUTRAL'),
        'sufficient_holders': balance.get('watchlist_count', 0) >= 3,
    }
    if sum(1 for v in accumulation_conditions.values() if v) >= 3:
        patterns_detected.append({
            'pattern': 'SUSTAINED_ACCUMULATION',
            'signal': 'BULLISH',
            'confidence': 'HIGH' if all(accumulation_conditions.values()) else 'MEDIUM',
            'conditions_met': accumulation_conditions,
            'reason': f"{accepted_14d} new holders 14d, ~{total_watched/1e6:.1f}M STRK watched, BTC {btc_cycle}. Sustained accumulation base.",
        })
    
    # === PATTERN C · DISTRIBUTION_PHASE ===
    distribution_conditions = {
        'many_large_recipients': large_14d >= 10,
        'shape_worsening': ratio_trend_worsening or ratio_14d < 0.10,
        'long_crowded_or_positive_extreme': long_crowded or funding_avg_7d > 5,
        'btc_down': btc_cycle == 'DOWN',
    }
    if sum(1 for v in distribution_conditions.values() if v) >= 3:
        patterns_detected.append({
            'pattern': 'DISTRIBUTION_PHASE',
            'signal': 'BEARISH',
            'confidence': 'HIGH' if all(distribution_conditions.values()) else 'MEDIUM',
            'conditions_met': distribution_conditions,
            'reason': f"{large_14d} LARGE recipients 14d, ratio {ratio_14d:.3f} declining, funding {funding_avg_7d:+.1f}% ann. Active distribution.",
        })
    
    # === PATTERN D · CAPITULATION ===
    capitulation_conditions = {
        'many_rejected': rejected_7d >= 5,
        'btc_extreme_down': btc_dist200 < -15,
        'funding_neg': funding_avg_7d < -2,
        'no_new_accumulators': accepted_7d == 0 and accepted_14d < 2,
    }
    if sum(1 for v in capitulation_conditions.values() if v) >= 3:
        patterns_detected.append({
            'pattern': 'CAPITULATION',
            'signal': 'BEARISH_CONTRARIAN',
            'confidence': 'HIGH' if all(capitulation_conditions.values()) else 'MEDIUM',
            'conditions_met': capitulation_conditions,
            'reason': f"{rejected_7d} rejected 7d, no new holders, BTC {btc_dist200:.1f}% below MA200. Capitulation phase — bottom setup but not yet.",
        })
    
    if not patterns_detected:
        return [{
            'pattern': 'NO_CLEAR_PATTERN',
            'signal': 'NEUTRAL',
            'confidence': 'LOW',
            'reason': 'No cross-window alignment detected.',
        }]
    
    # Sort by confidence
    order = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
    patterns_detected.sort(key=lambda x: -order.get(x['confidence'], 0))
    return patterns_detected


def run_analysis():
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return None
    
    logger.info("Fetching distribution data across windows...")
    logger.info("  7 days...")
    txs_7d = fetch_transfers_window(24 * 7)
    logger.info(f"    {len(txs_7d)} transfers")
    
    logger.info("  14 days...")
    txs_14d = fetch_transfers_window(24 * 14)
    logger.info(f"    {len(txs_14d)} transfers")
    
    logger.info("  30 days...")
    txs_30d = fetch_transfers_window(24 * 30)
    logger.info(f"    {len(txs_30d)} transfers")
    
    d7 = compute_distribution_metrics(txs_7d, '7d')
    d14 = compute_distribution_metrics(txs_14d, '14d')
    d30 = compute_distribution_metrics(txs_30d, '30d')
    
    logger.info("Loading watchlist growth from decision_log...")
    growth = get_watchlist_growth()
    logger.info(f"  Accepted 7d: {growth.get('accepted_7d')}, 14d: {growth.get('accepted_14d')}, 30d: {growth.get('accepted_30d')}")
    
    logger.info("Fetching watchlist balances...")
    balance = get_watchlist_balance_change()
    logger.info(f"  Watchlist: {balance.get('watchlist_count')} addrs, {balance.get('total_balance_strk', 0)/1e6:.1f}M STRK")
    
    metrics = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'dist_7d': d7,
        'dist_14d': d14,
        'dist_30d': d30,
        'watchlist_growth': growth,
        'watchlist_balance': balance,
    }
    
    logger.info("\nClassifying patterns...")
    patterns = classify_pattern(metrics)
    
    metrics['patterns_detected'] = patterns
    metrics['primary_pattern'] = patterns[0] if patterns else None
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    
    return metrics


def main():
    logger.info("=" * 70)
    logger.info("CROSS-WINDOW PATTERN DETECTOR")
    logger.info("Looking for 7d/14d/30d accumulation × funding × BTC alignment")
    logger.info("=" * 70)
    
    metrics = run_analysis()
    if not metrics:
        return 1
    
    logger.info("\n" + "=" * 70)
    logger.info("PATTERNS DETECTED:")
    logger.info("=" * 70)
    for p in metrics['patterns_detected']:
        logger.info(f"\n  {p['pattern']} · {p['signal']} · {p['confidence']} confidence")
        logger.info(f"  {p['reason']}")
        if 'conditions_met' in p:
            for cond, met in p['conditions_met'].items():
                marker = '✓' if met else '✗'
                logger.info(f"    {marker} {cond}")
    logger.info("=" * 70)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
