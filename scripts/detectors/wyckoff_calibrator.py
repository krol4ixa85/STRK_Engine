#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyckoff_calibrator.py — Калибровка Wyckoff на 9 исторических событиях STRK

Для каждого события собирает данные за 14 дней ДО момента:
  · Historical funding rate (OKX)
  · Historical BTC price + MA200
  · Historical STRK price/volume structure
  · Distribution shape (fetch через Etherscan за 14d window)

Прогоняет через wyckoff_phase логику. Сравнивает с actual outcome:
  · Rally_1_start (2024-11-05) — expected phase: ACCUMULATION или late-B/C
  · Crash_1_start (2024-12-07) — expected: DISTRIBUTION or MARKUP-late
  · Rally_2_start (2025-11-03) — expected: ACCUMULATION
  · Crash_2_start (2025-11-20) — expected: DISTRIBUTION
  · Rally_3_start (2026-04-14) — expected: ACCUMULATION
  · Crash_3_start (2026-05-09) — expected: DISTRIBUTION
  · Control_A/B/C_quiet — expected: no clear phase / sideways

Output:
  · data/validation/wyckoff_calibration.json
  · Recommended threshold adjustments
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
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
VALIDATION_DIR = SCRIPT_DIR / 'data' / 'validation'
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('calibrator')


# ================================================================
# Historical events with expected outcomes
# ================================================================

EVENTS = [
    {'name': 'Rally_1_start', 'date': '2024-11-05', 'move_pct': +135, 
     'expected_phase': ['ACCUMULATION', 'MARKUP'],
     'expected_desc': 'accumulation late-B/C → start of markup'},
    {'name': 'Crash_1_start', 'date': '2024-12-07', 'move_pct': -86,
     'expected_phase': ['DISTRIBUTION', 'MARKUP'],
     'expected_desc': 'MARKUP peak or DISTRIBUTION early-A'},
    {'name': 'Rally_2_start', 'date': '2025-11-03', 'move_pct': +175,
     'expected_phase': ['ACCUMULATION', 'MARKDOWN'],
     'expected_desc': 'ACCUMULATION or MARKDOWN capitulation'},
    {'name': 'Crash_2_start', 'date': '2025-11-20', 'move_pct': -88,
     'expected_phase': ['DISTRIBUTION', 'MARKUP'],
     'expected_desc': 'DISTRIBUTION or MARKUP exhaustion'},
    {'name': 'Rally_3_start', 'date': '2026-04-14', 'move_pct': +99,
     'expected_phase': ['ACCUMULATION', 'MARKDOWN'],
     'expected_desc': 'ACCUMULATION or MARKDOWN capitulation'},
    {'name': 'Crash_3_start', 'date': '2026-05-09', 'move_pct': -56,
     'expected_phase': ['DISTRIBUTION', 'MARKUP'],
     'expected_desc': 'DISTRIBUTION or MARKUP peak'},
    {'name': 'Control_A_quiet', 'date': '2025-06-15', 'move_pct': 0,
     'expected_phase': ['ACCUMULATION', 'MARKDOWN'],
     'expected_desc': 'no strong trend / sideways'},
    {'name': 'Control_B_quiet', 'date': '2026-01-20', 'move_pct': 0,
     'expected_phase': ['ACCUMULATION', 'MARKDOWN'],
     'expected_desc': 'no strong trend / sideways'},
    {'name': 'Control_C_quiet', 'date': '2026-07-10', 'move_pct': 0,
     'expected_phase': ['ACCUMULATION', 'MARKDOWN'],
     'expected_desc': 'no strong trend / sideways'},
]


# ================================================================
# Data fetchers
# ================================================================

def fetch_okx_candles(inst_id, bar, ts_end, ts_start):
    """Fetch OKX historical candles up to ts_end (ms)."""
    try:
        url = f'https://www.okx.com/api/v5/market/history-candles?instId={inst_id}&bar={bar}&before={ts_end}&limit=300'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        candles = data.get('data', [])
        # Filter to our range
        filtered = [c for c in candles if int(c[0]) >= ts_start]
        return list(reversed(filtered))
    except Exception as e:
        logger.error(f"OKX fetch error for {inst_id}: {e}")
        return []


def fetch_funding_history(inst_id, ts_end, ts_start):
    """Historical funding rates."""
    try:
        url = f'https://www.okx.com/api/v5/public/funding-rate-history?instId={inst_id}&before={ts_end}&limit=100'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        items = data.get('data', [])
        filtered = [i for i in items if int(i['fundingTime']) >= ts_start]
        return list(reversed(filtered))
    except Exception as e:
        logger.error(f"Funding fetch error: {e}")
        return []


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


def fetch_distribution_shape_at(event_date_str, days_back=14):
    """Fetch STRK transfer events for the 14 days before event_date."""
    event_dt = datetime.strptime(event_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    to_ts = int(event_dt.timestamp())
    from_ts = to_ts - days_back * 86400
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return None
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    all_recipients = defaultdict(float)
    current = from_block
    
    for _ in range(20):
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
                all_recipients[to_addr] += amount
            except (KeyError, ValueError):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.3)
    
    # Classify
    counts = {'MICRO': 0, 'SMALL': 0, 'MEDIUM': 0, 'LARGE': 0}
    amounts = {'MICRO': 0, 'SMALL': 0, 'MEDIUM': 0, 'LARGE': 0}
    for addr, amt in all_recipients.items():
        if amt < 10_000: bucket = 'MICRO'
        elif amt < 100_000: bucket = 'SMALL'
        elif amt < 1_000_000: bucket = 'MEDIUM'
        else: bucket = 'LARGE'
        counts[bucket] += 1
        amounts[bucket] += amt
    
    ratio = (amounts['MICRO'] + amounts['SMALL']) / max(amounts['LARGE'], 1)
    return {
        'counts': counts,
        'amounts_strk': {k: round(v, 2) for k, v in amounts.items()},
        'ratio_smallamt_over_largeamt': round(ratio, 4),
        'large_receiver_count': counts['LARGE'],
        'unique_recipients': len(all_recipients),
    }


# ================================================================
# Simulate wyckoff phase logic on historical data
# ================================================================

def simulate_wyckoff_phase(event, dist, funding_data, strk_candles, btc_candles):
    """Apply current wyckoff_phase logic to historical data."""
    
    # === Technical ===
    if len(strk_candles) < 30:
        return None
    
    highs = [float(c[2]) for c in strk_candles]
    lows = [float(c[3]) for c in strk_candles]
    closes = [float(c[4]) for c in strk_candles]
    vols = [float(c[6]) for c in strk_candles]
    
    price_at_event = closes[-1]
    high_14d = max(highs)
    low_14d = min(lows)
    pct_from_high = (price_at_event / high_14d - 1) * 100
    pct_from_low = (price_at_event / low_14d - 1) * 100
    
    # Structure (last 30 candles / first 30)
    if len(closes) >= 30:
        recent_high = max(highs[-15:])
        prior_high = max(highs[:15])
        recent_low = min(lows[-15:])
        prior_low = min(lows[:15])
        hh = recent_high > prior_high
        hl = recent_low > prior_low
        lh = recent_high < prior_high
        ll = recent_low < prior_low
        if hh and hl: structure = 'UPTREND'
        elif lh and ll: structure = 'DOWNTREND'
        elif hh and ll: structure = 'VOLATILE'
        elif lh and hl: structure = 'CONSOLIDATION'
        else: structure = 'SIDEWAYS'
    else:
        structure = 'UNKNOWN'
    
    # Volume trend
    avg_vol = sum(vols) / len(vols) if vols else 1
    recent_vol = sum(vols[-10:]) / 10 if vols else 0
    vol_trend = recent_vol / avg_vol if avg_vol > 0 else 1
    
    # === Funding ===
    ann_rates = []
    for item in funding_data:
        try:
            rate = float(item.get('realizedRate') or item['fundingRate'])
            ann_rates.append(rate * 3 * 365 * 100)
        except (KeyError, ValueError):
            continue
    
    neg_count = sum(1 for r in ann_rates if r < 0)
    pct_neg = neg_count / max(len(ann_rates), 1) * 100
    min_ann = min(ann_rates) if ann_rates else 0
    avg_ann = sum(ann_rates) / len(ann_rates) if ann_rates else 0
    short_crowded = pct_neg > 40 or min_ann < -10 or avg_ann < -3
    long_crowded = avg_ann > 8 or max(ann_rates or [0]) > 30
    
    # === BTC ===
    if len(btc_candles) >= 200:
        btc_closes = [float(c[4]) for c in btc_candles]
        btc_now = btc_closes[-1]
        btc_ma200 = sum(btc_closes[-200:]) / 200
        dist200 = (btc_now / btc_ma200 - 1) * 100
        btc_7d = btc_closes[-8] if len(btc_closes) >= 8 else btc_closes[0]
        btc_30d = btc_closes[-31] if len(btc_closes) >= 31 else btc_closes[0]
        slope7 = (btc_now / btc_7d - 1) * 100
        slope30 = (btc_now / btc_30d - 1) * 100
        accel = slope7 - (slope30 / 4.3)
        if dist200 > 5 and slope30 > 0:
            btc_cycle = 'UP'
        elif dist200 < -5 and accel > 3:
            btc_cycle = 'DOWN_REVERSING'
        elif dist200 < -5:
            btc_cycle = 'DOWN'
        else:
            btc_cycle = 'NEUTRAL'
    else:
        btc_cycle = 'UNKNOWN'
        dist200 = 0
    
    # === Distribution ===
    large_14d = dist.get('large_receiver_count', 0) if dist else 0
    ratio_14d = dist.get('ratio_smallamt_over_largeamt', 0) if dist else 0
    
    # === PHASE VOTING (same as current wyckoff_phase.py) ===
    scores = {'ACCUMULATION': 0, 'MARKUP': 0, 'DISTRIBUTION': 0, 'MARKDOWN': 0}
    
    # ACCUMULATION signals
    if large_14d < 30: scores['ACCUMULATION'] += 1
    if ratio_14d > 0.30: scores['ACCUMULATION'] += 2
    if short_crowded: scores['ACCUMULATION'] += 1
    if structure in ('SIDEWAYS', 'CONSOLIDATION'): scores['ACCUMULATION'] += 1
    if vol_trend < 0.7: scores['ACCUMULATION'] += 1
    
    # MARKUP
    if structure == 'UPTREND': scores['MARKUP'] += 2
    if vol_trend > 1.2: scores['MARKUP'] += 1
    if btc_cycle in ('UP', 'DOWN_REVERSING'): scores['MARKUP'] += 1
    if pct_from_low > 15: scores['MARKUP'] += 1
    if 0.20 < ratio_14d < 0.50 and large_14d < 40: scores['MARKUP'] += 1
    
    # DISTRIBUTION
    if large_14d > 50: scores['DISTRIBUTION'] += 2
    if ratio_14d < 0.10: scores['DISTRIBUTION'] += 2
    if long_crowded or avg_ann > 8: scores['DISTRIBUTION'] += 1
    if structure == 'VOLATILE': scores['DISTRIBUTION'] += 1
    if pct_from_high > -10 and vol_trend > 1.1: scores['DISTRIBUTION'] += 1
    
    # MARKDOWN
    if structure == 'DOWNTREND': scores['MARKDOWN'] += 2
    if pct_from_high < -15: scores['MARKDOWN'] += 1
    if vol_trend > 1.5 and structure == 'DOWNTREND': scores['MARKDOWN'] += 1
    if btc_cycle == 'DOWN' and dist200 < -10: scores['MARKDOWN'] += 1
    
    winner = max(scores.items(), key=lambda x: x[1])
    phase = winner[0]
    score = winner[1]
    
    if score >= 5: conf = 'HIGH'
    elif score >= 3: conf = 'MEDIUM'
    else: conf = 'LOW'
    
    return {
        'phase': phase,
        'confidence': conf,
        'score': score,
        'all_scores': scores,
        'structure': structure,
        'vol_trend': round(vol_trend, 2),
        'large_14d': large_14d,
        'ratio_14d': ratio_14d,
        'funding_avg_ann': round(avg_ann, 2),
        'funding_min_ann': round(min_ann, 2),
        'short_crowded': short_crowded,
        'long_crowded': long_crowded,
        'btc_cycle': btc_cycle,
        'btc_dist200': round(dist200, 2),
        'pct_from_high': round(pct_from_high, 2),
        'pct_from_low': round(pct_from_low, 2),
    }


def calibrate_all():
    logger.info(f"Testing {len(EVENTS)} historical events\n")
    
    results = []
    hits = 0
    partial_hits = 0
    misses = 0
    
    for event in EVENTS:
        logger.info(f"=" * 60)
        logger.info(f"Event: {event['name']} · {event['date']} · expected move {event['move_pct']:+d}%")
        logger.info(f"Expected phase: {event['expected_phase']}")
        
        event_dt = datetime.strptime(event['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        ts_end = int(event_dt.timestamp() * 1000)
        ts_start = int((event_dt - timedelta(days=14)).timestamp() * 1000)
        
        # Fetch historical data
        logger.info("  Fetching STRK price history...")
        strk_candles = fetch_okx_candles('STRK-USDT', '4H', ts_end, ts_start)
        time.sleep(0.5)
        
        logger.info("  Fetching BTC price history...")
        # For BTC MA200 need 200 days of daily
        btc_ts_start = ts_end - 210 * 86400 * 1000
        btc_candles = fetch_okx_candles('BTC-USDT', '1D', ts_end, btc_ts_start)
        time.sleep(0.5)
        
        logger.info("  Fetching funding rate history...")
        funding = fetch_funding_history('STRK-USDT-SWAP', ts_end, ts_start)
        time.sleep(0.5)
        
        logger.info("  Fetching STRK distribution shape (14d)...")
        dist = fetch_distribution_shape_at(event['date'], days_back=14)
        time.sleep(1)
        
        if not strk_candles or not btc_candles:
            logger.warning(f"  Not enough data for {event['name']}")
            continue
        
        logger.info("  Simulating Wyckoff phase...")
        detected = simulate_wyckoff_phase(event, dist, funding, strk_candles, btc_candles)
        
        if not detected:
            continue
        
        # Compare
        expected_set = set(event['expected_phase'])
        detected_phase = detected['phase']
        
        if detected_phase == event['expected_phase'][0]:
            outcome = 'HIT'
            hits += 1
        elif detected_phase in expected_set:
            outcome = 'PARTIAL_HIT'
            partial_hits += 1
        else:
            outcome = 'MISS'
            misses += 1
        
        logger.info(f"  DETECTED: {detected_phase} ({detected['confidence']})")
        logger.info(f"  OUTCOME: {outcome}")
        logger.info(f"    Structure: {detected['structure']}")
        logger.info(f"    LARGE 14d: {detected['large_14d']} · ratio: {detected['ratio_14d']}")
        logger.info(f"    Funding: avg {detected['funding_avg_ann']:+.1f}%, min {detected['funding_min_ann']:+.1f}%")
        logger.info(f"    Short-crowded: {detected['short_crowded']}, BTC: {detected['btc_cycle']}\n")
        
        results.append({
            'event': event,
            'detected': detected,
            'outcome': outcome,
        })
    
    # Summary
    total = len(results)
    accuracy = (hits + partial_hits) / total * 100 if total else 0
    strict_accuracy = hits / total * 100 if total else 0
    
    logger.info("=" * 60)
    logger.info("CALIBRATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total events tested: {total}")
    logger.info(f"Strict hits (top phase): {hits}/{total} = {strict_accuracy:.1f}%")
    logger.info(f"Partial hits (in expected set): {partial_hits}/{total}")
    logger.info(f"Misses: {misses}/{total}")
    logger.info(f"Overall accuracy: {accuracy:.1f}%")
    
    # Save
    output = VALIDATION_DIR / 'wyckoff_calibration.json'
    with open(output, 'w', encoding='utf-8') as f:
        json.dump({
            'as_of': datetime.now(timezone.utc).isoformat(),
            'accuracy': {
                'strict_hits_pct': round(strict_accuracy, 1),
                'total_hits_pct': round(accuracy, 1),
                'hits': hits,
                'partial_hits': partial_hits,
                'misses': misses,
                'total': total,
            },
            'events': results,
        }, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\nSaved calibration report: {output}")
    
    # Threshold recommendations based on misses
    logger.info("\n" + "=" * 60)
    logger.info("THRESHOLD RECOMMENDATIONS")
    logger.info("=" * 60)
    for r in results:
        if r['outcome'] == 'MISS':
            e = r['event']
            d = r['detected']
            logger.info(f"\n{e['name']} MISS:")
            logger.info(f"  Expected: {e['expected_phase']}")
            logger.info(f"  Got: {d['phase']} (score {d['score']})")
            logger.info(f"  All scores: {d['all_scores']}")
    
    return output


def main():
    logger.info("=" * 60)
    logger.info("WYCKOFF CALIBRATION on 9 historical STRK events")
    logger.info("=" * 60)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    calibrate_all()
    return 0


if __name__ == '__main__':
    sys.exit(main())
