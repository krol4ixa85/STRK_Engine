#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
composite_detector_v2.py — Полный композитный детектор

Читает результаты 5 модулей:
  1. distribution_shape (via composite_detector.py fetch)
  2. BTC cycle context
  3. funding_signal.json
  4. unlock_signal.json
  5. whale_monitor recent alerts

Выдаёт integrated signal с honest confidence на основе confluence:
  · Все 5 модулей aligned bullish → BULLISH_STRONG (85%+ historical)
  · 3-4 aligned bullish → BULLISH_MEDIUM (65-75%)
  · Mixed или conflicting → NEUTRAL
  · 3-4 aligned bearish → BEARISH_MEDIUM
  · Все 5 bearish → BEARISH_STRONG

Пишет agent_input_v2.json + отправляет Telegram summary.
"""

import os
import sys
import json
import time
import logging
import urllib.request
import urllib.parse
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
}
BUCKETS = [
    ('MICRO', 100_000, 500_000),
    ('SMALL', 500_000, 1_000_000),
    ('MEDIUM', 1_000_000, 10_000_000),
    ('LARGE', 10_000_000, float('inf')),
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('composite_v2')


# ===== Module runners (fetch fresh data) =====

def run_subprocess(script_path, args=None):
    cmd = [sys.executable, str(script_path)] + (args or [])
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300, env=env)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Subprocess error: {e}")
        return False


# ===== Distribution shape (inline for reliability) =====

def fetch_and_compute_shape():
    """Fetch STRK L1 transfers over 14d and compute distribution shape."""
    from_ts = int((datetime.now(timezone.utc) - timedelta(days=14)).timestamp())
    to_ts = int(datetime.now(timezone.utc).timestamp())
    
    def api(params):
        url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"API error: {e}")
            return None
    
    def block_at(ts):
        d = api({'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
                 'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY})
        return int(d['result']) if d and d.get('status') == '1' else None
    
    from_block = block_at(from_ts)
    time.sleep(0.4)
    to_block = block_at(to_ts)
    time.sleep(0.4)
    if not from_block or not to_block:
        return None
    
    transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    txs = []
    current = from_block
    for _ in range(20):
        d = api({'chainid': 1, 'module': 'logs', 'action': 'getLogs',
                 'address': STRK_L1, 'topic0': transfer_topic,
                 'fromBlock': current, 'toBlock': to_block,
                 'page': 1, 'offset': 1000, 'apikey': ETHERSCAN_API_KEY})
        if not d or d.get('status') != '1' or not d.get('result'):
            break
        logs = d['result']
        max_block = 0
        for log in logs:
            try:
                topics = log['topics']
                if len(topics) < 3: continue
                amt = int(log['data'], 16) / 1e18
                ts = int(log['timeStamp'], 16)
                max_block = max(max_block, int(log['blockNumber'], 16))
                if from_ts <= ts <= to_ts:
                    txs.append({
                        'from': ('0x' + topics[1][-40:]).lower(),
                        'to': ('0x' + topics[2][-40:]).lower(),
                        'amount': amt,
                    })
            except (KeyError, ValueError, IndexError):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.4)
    
    # Compute shape
    received = defaultdict(float); sent = defaultdict(float)
    for tx in txs:
        received[tx['to']] += tx['amount']
        sent[tx['from']] += tx['amount']
    
    net_receivers = {}
    for addr, r in received.items():
        if addr in KNOWN_CEX: continue
        s = sent.get(addr, 0)
        if r > 100_000 and s < r * 0.5:
            net_receivers[addr] = r - s
    
    counts = {n: 0 for n, _, _ in BUCKETS}
    totals = {n: 0 for n, _, _ in BUCKETS}
    for addr, amt in net_receivers.items():
        for n, lo, hi in BUCKETS:
            if lo <= amt < hi:
                counts[n] += 1
                totals[n] += amt
                break
    
    small_amt = totals['MICRO'] + totals['SMALL']
    large_amt = totals['LARGE']
    ratio = small_amt / max(large_amt, 1)
    
    # Classify
    if counts['LARGE'] <= 2 and ratio > 0.4:
        sig = 'BULLISH'
    elif counts['LARGE'] >= 3 and ratio < 0.3:
        sig = 'BEARISH'
    else:
        sig = 'NEUTRAL'
    
    return {
        'signal': sig,
        'counts': counts,
        'totals': {k: round(v, 2) for k, v in totals.items()},
        'ratio_smallamt_over_largeamt': round(ratio, 4) if ratio < 1e6 else 999999,
        'total_transfers_analyzed': len(txs),
        'net_receivers_count': sum(counts.values()),
    }


def get_btc_context():
    try:
        url = 'https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=200'
        r = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        closes = [float(x[4]) for x in reversed(data['data'])]
        if len(closes) < 200: return None
        price = closes[-1]
        ma200 = sum(closes) / len(closes)
        dist200 = (price / ma200 - 1) * 100
        slope30 = (price / closes[-31] - 1) * 100
        slope7 = (price / closes[-8] - 1) * 100
        # Acceleration: is 7d slope > 30d slope? (BTC accelerating up recently)
        acceleration = slope7 - (slope30 / 4.3)  # normalized weekly slope
        
        # Cycle classification with acceleration
        if dist200 > 5 and slope30 > 0:
            cycle = 'UP'
        elif dist200 < -5 and acceleration > 3:
            cycle = 'DOWN_REVERSING'  # NEW: down but turning up
        elif dist200 < -5:
            cycle = 'DOWN'
        else:
            cycle = 'NEUTRAL'
        
        return {'btc_price': round(price, 2), 'dist200_pct': round(dist200, 2),
                'slope30_pct': round(slope30, 2), 'slope7_pct': round(slope7, 2),
                'acceleration': round(acceleration, 2), 'cycle': cycle}
    except Exception as e:
        logger.error(f"BTC error: {e}")
        return None


def load_cache_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


def get_recent_discord_activity():
    """Recent Discord alerts from state file (populated by discord_monitor.py)."""
    state = load_cache_json('discord_monitor_state.json') or {}
    now_ts = datetime.now(timezone.utc).timestamp()
    day_ago = now_ts - 86400
    
    events = state.get('parsed_events', [])
    recent = []
    for e in events:
        try:
            ts_iso = e.get('timestamp', '')
            ts = datetime.fromisoformat(ts_iso.replace('Z', '+00:00')).timestamp()
            if ts > day_ago:
                recent.append(e)
        except (ValueError, AttributeError):
            continue
    
    if not recent:
        return {'signal': 'QUIET', 'events_24h': 0}
    
    bearish_count = sum(1 for e in recent if e.get('parsed', {}).get('direction_hint') == 'bearish')
    bullish_count = sum(1 for e in recent if e.get('parsed', {}).get('direction_hint') == 'bullish')
    total_amount = sum(e.get('parsed', {}).get('amount_strk', 0) for e in recent)
    
    if bearish_count > bullish_count * 2:
        sig = 'BEARISH_ACTIVITY'
    elif bullish_count > bearish_count * 2:
        sig = 'BULLISH_ACTIVITY'
    else:
        sig = 'MIXED_ACTIVITY'
    
    return {
        'signal': sig,
        'events_24h': len(recent),
        'total_amount_24h': total_amount,
        'bearish_count': bearish_count,
        'bullish_count': bullish_count,
    }


def get_recent_whale_activity():
    """Recent whale alerts from monitor state."""
    state = load_cache_json('whale_monitor_state.json') or {}
    now_ts = datetime.now(timezone.utc).timestamp()
    day_ago = now_ts - 86400
    recent = [a for a in state.get('alert_history', []) if a.get('ts', 0) > day_ago]
    
    if not recent:
        return {'signal': 'QUIET', 'events_24h': 0, 'largest_event_strk': 0}
    
    # Aggregate by class
    by_class = defaultdict(list)
    for a in recent:
        by_class[a.get('flow_class', 'UNKNOWN')].append(a['amount'])
    
    largest = max(a['amount'] for a in recent)
    total_strk = sum(a['amount'] for a in recent)
    
    # Determine signal
    bearish_classes = ['DISTRIBUTION_CUSTODY_CEX', 'CEX_DEPOSIT']
    bullish_classes = ['BRIDGE_IN', 'CUSTODY_INFLOW', 'CEX_WITHDRAWAL']
    
    bearish_amt = sum(sum(v) for k, v in by_class.items() if k in bearish_classes)
    bullish_amt = sum(sum(v) for k, v in by_class.items() if k in bullish_classes)
    
    if bearish_amt > bullish_amt * 2:
        sig = 'BEARISH_ACTIVITY'
    elif bullish_amt > bearish_amt * 2:
        sig = 'BULLISH_ACTIVITY'
    else:
        sig = 'MIXED_ACTIVITY'
    
    return {
        'signal': sig,
        'events_24h': len(recent),
        'largest_event_strk': largest,
        'total_amount_24h': total_strk,
        'bearish_amount': bearish_amt,
        'bullish_amount': bullish_amt,
        'by_class': {k: {'count': len(v), 'total': sum(v)} for k, v in by_class.items()},
    }


# ===== Confluence scoring =====

def score_composite(shape, btc, funding, unlock, whales, discord, cross_window=None):
    """Score bullish vs bearish signals across all modules."""
    votes = {'bullish': 0, 'bearish': 0, 'neutral': 0}
    breakdown = []
    
    # 0. CROSS-WINDOW PATTERN (WEIGHT 3 when HIGH, 2 MEDIUM) - strongest signal
    if cross_window and cross_window.get('primary_pattern'):
        p = cross_window['primary_pattern']
        sig = p.get('signal', 'NEUTRAL')
        conf = p.get('confidence', 'LOW')
        weight = 3 if conf == 'HIGH' else (2 if conf == 'MEDIUM' else 1)
        pattern_name = p.get('pattern', 'unknown')
        
        if 'BULLISH' in sig:
            votes['bullish'] += weight
            breakdown.append(('cross_window', f'{pattern_name} ({conf})', f'weight {weight}'))
        elif 'BEARISH' in sig:
            votes['bearish'] += weight
            breakdown.append(('cross_window', f'{pattern_name} ({conf})', f'weight {weight}'))
        else:
            votes['neutral'] += 1
            breakdown.append(('cross_window', pattern_name, 'weight 1'))
    
    # 1. Distribution shape (WEIGHT 2 - most validated)
    if shape:
        if shape['signal'] == 'BULLISH':
            votes['bullish'] += 2
            breakdown.append(('distribution', 'BULLISH', 'weight 2'))
        elif shape['signal'] == 'BEARISH':
            votes['bearish'] += 2
            breakdown.append(('distribution', 'BEARISH', 'weight 2'))
        else:
            votes['neutral'] += 1
            breakdown.append(('distribution', 'NEUTRAL', 'weight 1'))
    
    # 2. BTC cycle
    if btc:
        if btc['cycle'] == 'UP':
            votes['bullish'] += 1
            breakdown.append(('btc_cycle', 'UP', 'weight 1'))
        elif btc['cycle'] == 'DOWN_REVERSING':
            votes['bullish'] += 1  # NEW: reversing is bullish signal
            breakdown.append(('btc_cycle', 'DOWN_REVERSING (accel +)', 'weight 1'))
        elif btc['cycle'] == 'DOWN':
            votes['bearish'] += 1
            breakdown.append(('btc_cycle', 'DOWN', 'weight 1'))
        else:
            votes['neutral'] += 1
            breakdown.append(('btc_cycle', 'NEUTRAL', 'weight 1'))
    
    # 3. Funding (contrarian) - weight 2 when squeeze setup, 1 otherwise
    if funding and 'signal' in funding:
        s = funding['signal']
        fm = funding.get('funding_metrics') or {}
        is_extreme = fm.get('extreme') in ('short_extreme', 'long_extreme')
        weight = 2 if is_extreme else 1
        
        if 'BULLISH' in s:
            votes['bullish'] += weight
            breakdown.append(('funding', s, f'weight {weight}'))
        elif 'BEARISH' in s:
            votes['bearish'] += weight
            breakdown.append(('funding', s, f'weight {weight}'))
        else:
            votes['neutral'] += 1
            breakdown.append(('funding', 'NEUTRAL', 'weight 1'))
    
    # 4. Unlock pressure
    if unlock:
        p = unlock.get('pressure', 'LOW')
        if p == 'HIGH':
            votes['bearish'] += 2
            breakdown.append(('unlock', 'HIGH_pressure', 'weight 2'))
        elif p == 'MEDIUM':
            votes['bearish'] += 1
            breakdown.append(('unlock', 'MEDIUM_pressure', 'weight 1'))
        else:
            breakdown.append(('unlock', 'LOW_pressure', 'weight 0'))
    
    # 5. Whale activity (self-detected)
    if whales:
        s = whales['signal']
        if s == 'BULLISH_ACTIVITY':
            votes['bullish'] += 1
            breakdown.append(('whales_self', 'BULLISH_ACTIVITY', 'weight 1'))
        elif s == 'BEARISH_ACTIVITY':
            votes['bearish'] += 1
            breakdown.append(('whales_self', 'BEARISH_ACTIVITY', 'weight 1'))
        elif s == 'MIXED_ACTIVITY':
            breakdown.append(('whales_self', 'MIXED', 'weight 0'))
        else:
            breakdown.append(('whales_self', 'QUIET', 'weight 0'))
    
    # 6. Discord alerts (external source, e.g. Nansen)
    if discord and discord['events_24h'] > 0:
        s = discord['signal']
        if s == 'BULLISH_ACTIVITY':
            votes['bullish'] += 1
            breakdown.append(('discord_nansen', 'BULLISH_ACTIVITY', 'weight 1'))
        elif s == 'BEARISH_ACTIVITY':
            votes['bearish'] += 1
            breakdown.append(('discord_nansen', 'BEARISH_ACTIVITY', 'weight 1'))
        elif s == 'MIXED_ACTIVITY':
            breakdown.append(('discord_nansen', 'MIXED', 'weight 0'))
        else:
            breakdown.append(('discord_nansen', 'QUIET', 'weight 0'))
    elif discord:
        breakdown.append(('discord_nansen', 'QUIET (0 alerts 24h)', 'weight 0'))
    else:
        breakdown.append(('discord_nansen', 'NOT_CONFIGURED', 'weight 0'))
    
    total = votes['bullish'] + votes['bearish']
    if total == 0:
        signal = 'NEUTRAL'
        confidence = 'LOW'
    else:
        ratio = votes['bullish'] / total if total > 0 else 0.5
        if ratio >= 0.75:
            signal = 'BULLISH_STRONG'
            confidence = 'HIGH (multiple modules aligned)'
        elif ratio >= 0.60:
            signal = 'BULLISH'
            confidence = 'MEDIUM'
        elif ratio <= 0.25:
            signal = 'BEARISH_STRONG'
            confidence = 'HIGH (multiple modules aligned)'
        elif ratio <= 0.40:
            signal = 'BEARISH'
            confidence = 'MEDIUM'
        else:
            signal = 'MIXED'
            confidence = 'LOW (conflicting signals)'
    
    return {
        'signal': signal,
        'confidence': confidence,
        'votes': votes,
        'bullish_share': round(votes['bullish'] / max(total, 1), 3),
        'breakdown': breakdown,
    }


def build_action_recommendation(signal, shape, btc, funding, unlock, whales):
    """Human-readable action recommendation."""
    if signal == 'BULLISH_STRONG':
        return {
            'headline': '🟢🟢 STRK · Strong Bullish Setup',
            'action': 'Consider LIQ to confirm entry. Multiple modules aligned bullish.',
            'timeframe': 'Multi-week horizon likely',
        }
    elif signal == 'BULLISH':
        return {
            'headline': '🟢 STRK · Moderate Bullish',
            'action': 'LIQ recommended. Mixed but bullish-leaning context.',
            'timeframe': 'Short-term long possible, verify with LIQ',
        }
    elif signal == 'BEARISH_STRONG':
        return {
            'headline': '🔴🔴 STRK · Strong Bearish Setup',
            'action': 'REDUCE EXPOSURE. Multiple modules aligned bearish.',
            'timeframe': 'Downside risk near-term',
        }
    elif signal == 'BEARISH':
        return {
            'headline': '🔴 STRK · Moderate Bearish',
            'action': 'LIQ recommended. Bearish-leaning context.',
            'timeframe': 'Watch for continuation down',
        }
    elif signal == 'MIXED':
        return {
            'headline': '🟡 STRK · Mixed Signals',
            'action': 'Wait for clearer setup. Some modules bullish, others bearish.',
            'timeframe': 'No action recommended yet',
        }
    else:
        return {
            'headline': '⚪ STRK · Neutral',
            'action': 'No signal. Background monitoring.',
            'timeframe': 'Normal daily routine',
        }


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:\n" + text[:500])
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(r, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def format_telegram_message(scoring, action, shape, btc, funding, unlock, whales):
    text = f"<b>{action['headline']}</b>\n\n"
    text += f"<b>Confidence:</b> {scoring['confidence']}\n"
    text += f"<b>Bullish votes:</b> {scoring['votes']['bullish']} · <b>Bearish:</b> {scoring['votes']['bearish']}\n\n"
    
    text += "<b>Signals breakdown:</b>\n"
    for name, verdict, weight in scoring['breakdown']:
        text += f"  · {name}: {verdict}\n"
    
    text += f"\n<b>Key numbers:</b>\n"
    if shape:
        text += f"  · LARGE receivers (14d): {shape['counts']['LARGE']}\n"
        text += f"  · Distribution ratio: {shape['ratio_smallamt_over_largeamt']}\n"
    if btc:
        text += f"  · BTC ${btc['btc_price']:,.0f} · dist200 {btc['dist200_pct']:+.1f}%\n"
    if funding and funding.get('funding_metrics'):
        fm = funding['funding_metrics']
        text += f"  · Funding {fm['current_annualized_pct']:+.1f}% ann · avg7d {fm['avg_7d_pct']:+.1f}%\n"
    if unlock:
        text += f"  · Unlock pressure: {unlock.get('pressure', 'N/A')}\n"
        if unlock.get('next_cliff'):
            nc = unlock['next_cliff']
            text += f"  · Next cliff: {nc['date']} ({nc['days_until']}d, {nc['amount_strk']/1e6:.0f}M)\n"
    if whales:
        text += f"  · Whale events 24h: {whales['events_24h']}\n"
    
    text += f"\n<b>Recommended action:</b>\n{action['action']}\n"
    text += f"\n<b>Timeframe:</b> {action['timeframe']}"
    return text


def main():
    logger.info("=" * 70)
    logger.info("COMPOSITE DETECTOR v2 · full confluence")
    logger.info("=" * 70)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    # Step 1: Refresh all sub-modules first
    logger.info("Refreshing sub-modules...")
    for script in ['funding_history.py', 'unlock_calendar.py']:
        p = SCRIPT_DIR / 'scripts' / 'collectors' / script
        if p.exists():
            logger.info(f"  Running {script}...")
            run_subprocess(p)
    
    # Step 2: Fetch fresh distribution shape
    logger.info("\nFetching distribution shape (14d)...")
    shape = fetch_and_compute_shape()
    if shape:
        logger.info(f"  Signal: {shape['signal']}")
        logger.info(f"  LARGE: {shape['counts']['LARGE']}, ratio: {shape['ratio_smallamt_over_largeamt']}")
    
    logger.info("Fetching BTC context...")
    btc = get_btc_context()
    if btc:
        logger.info(f"  Cycle: {btc['cycle']}, dist200: {btc['dist200_pct']:+.2f}%")
    
    logger.info("Loading funding signal...")
    funding = load_cache_json('funding_signal.json')
    if funding:
        logger.info(f"  Signal: {funding.get('signal')}")
    
    logger.info("Loading unlock signal...")
    unlock = load_cache_json('unlock_signal.json')
    if unlock:
        logger.info(f"  Pressure: {unlock.get('pressure')}")
    
    logger.info("Loading whale activity (last 24h)...")
    whales = get_recent_whale_activity()
    logger.info(f"  Signal: {whales['signal']}, events: {whales['events_24h']}")
    
    logger.info("Loading Discord alerts (last 24h)...")
    discord = get_recent_discord_activity()
    logger.info(f"  Signal: {discord['signal']}, events: {discord['events_24h']}")
    
    logger.info("Loading cross-window pattern...")
    cross_window = None
    cw_file = SCRIPT_DIR / 'data' / 'cache' / 'cross_window_pattern.json'
    if cw_file.exists():
        try:
            with open(cw_file, 'r', encoding='utf-8') as f:
                cross_window = json.load(f)
            primary = cross_window.get('primary_pattern')
            if primary:
                logger.info(f"  Pattern: {primary.get('pattern')} · {primary.get('signal')} · {primary.get('confidence')}")
        except Exception as e:
            logger.warning(f"Could not load cross_window: {e}")
    
    # Step 3: Score composite
    logger.info("\nScoring composite...")
    scoring = score_composite(shape, btc, funding, unlock, whales, discord, cross_window)
    action = build_action_recommendation(scoring['signal'], shape, btc, funding, unlock, whales)
    
    logger.info(f"\n{'='*70}")
    logger.info(f"COMPOSITE SIGNAL: {scoring['signal']}")
    logger.info(f"Confidence: {scoring['confidence']}")
    logger.info(f"Votes: bullish={scoring['votes']['bullish']}, bearish={scoring['votes']['bearish']}")
    logger.info(f"\nBreakdown:")
    for name, verdict, weight in scoring['breakdown']:
        logger.info(f"  · {name}: {verdict} ({weight})")
    logger.info(f"\nAction: {action['action']}")
    logger.info(f"Timeframe: {action['timeframe']}")
    logger.info(f"{'='*70}\n")
    
    # Save
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'signal': scoring['signal'],
        'confidence': scoring['confidence'],
        'scoring': scoring,
        'action': action,
        'inputs': {
            'distribution': shape,
            'btc_context': btc,
            'funding': funding,
            'unlock': unlock,
            'whales': whales,
            'discord': discord,
        },
        'model_metadata': {
            'version': 'composite_v2.1_with_discord',
            'modules_integrated': 6,
            'validation_baseline_precision': 0.667,
            'note': 'v2.1 adds Discord Nansen alerts. Full re-validation on historical events pending.',
        }
    }
    
    with open(CACHE_DIR / 'composite_signal_v2.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved: data/cache/composite_signal_v2.json")
    
    # Send Telegram if non-neutral AND digest mode is off
    # Default: individual alerts DISABLED (daily_digest handles it)
    silent = os.environ.get('COMPOSITE_SILENT', 'true').lower() == 'true'
    if scoring['signal'] not in ('NEUTRAL',) and not silent:
        msg = format_telegram_message(scoring, action, shape, btc, funding, unlock, whales)
        sent = send_telegram(msg)
        if sent:
            logger.info("Telegram alert sent")
    else:
        if silent:
            logger.info("Silent mode - daily_digest will send digest")
        else:
            logger.info("Signal NEUTRAL - no alert sent")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
