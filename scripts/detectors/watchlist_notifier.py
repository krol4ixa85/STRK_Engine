#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchlist_notifier.py — ЕДИНСТВЕННЫЙ источник "WATCH?" alerts в Telegram.

Читает два источника:
  · data/history/whale_events.jsonl (за последние 48h) — крупные переводы от unknown addresses
  · data/cache/auto_discovery_candidates.json — auto_discovery findings

Combine + dedup by address, apply priority thresholds, apply 7-day dedup.
Отправляет короткий формат:
    WATCH?
    0x<полный 42-char address>
    SMART → new EOA · 0.92M
    ADD: LOW/MEDIUM/HIGH
    почему: <одна строка>
    tx link

Не влияет на DECISION. Не входит в confluence_gate.
Persistence: data/history/discovery_candidates.jsonl (append-only, для бэктеста).

Правила:
  - Скипаем address если он уже в flow_seeds.json
  - Скипаем если alerted этот address в последние 7 дней (dedup)
  - Priority по amount:
      >= 5_000_000 STRK → HIGH
      >= 2_000_000 STRK → MEDIUM
      >= 500_000  STRK → LOW
      < 500k — не alert (но в log есть)
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
WHALE_EVENTS_FILE = HISTORY_DIR / 'whale_events.jsonl'
DISCOVERY_CANDIDATES_FILE = CACHE_DIR / 'auto_discovery_candidates.json'
STATE_FILE = CACHE_DIR / 'watchlist_notifier_state.json'
HISTORY_FILE = HISTORY_DIR / 'discovery_candidates.jsonl'

THRESHOLD_HIGH = 5_000_000
THRESHOLD_MEDIUM = 2_000_000
THRESHOLD_LOW = 500_000

DEDUP_DAYS = 7
WHALE_WINDOW_HOURS = 48


def load_json(path):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
    return {}


def load_state():
    return load_json(STATE_FILE) or {'address_last_alert': {}, 'alert_count': 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_seeds_addresses():
    seeds = load_json(SEEDS_FILE)
    addrs = set()
    for cat, entries in seeds.items():
        if cat.startswith('_'):
            continue
        if isinstance(entries, dict):
            for name, info in entries.items():
                if isinstance(info, dict) and 'address' in info:
                    addrs.add(info['address'].lower())
    return addrs


def determine_priority(received_strk):
    if received_strk >= THRESHOLD_HIGH:
        return 'HIGH'
    if received_strk >= THRESHOLD_MEDIUM:
        return 'MEDIUM'
    if received_strk >= THRESHOLD_LOW:
        return 'LOW'
    return None


def is_recently_alerted(address, state):
    last_alerts = state.get('address_last_alert', {})
    if address not in last_alerts:
        return False
    try:
        last_ts = datetime.fromisoformat(last_alerts[address])
        age = datetime.now(timezone.utc) - last_ts
        return age < timedelta(days=DEDUP_DAYS)
    except Exception:
        return False


def load_whale_candidates():
    """Read whale_events.jsonl — extract crypto candidates (unknown side of transfer)."""
    if not WHALE_EVENTS_FILE.exists():
        return []
    candidates = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WHALE_WINDOW_HOURS)
    try:
        with open(WHALE_EVENTS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    e = json.loads(line)
                    ts = datetime.fromisoformat(e['ts'])
                    if ts < cutoff:
                        continue
                    if e.get('both_known'):
                        continue
                    if e.get('amount_strk', 0) < THRESHOLD_LOW:
                        continue
                    from_cohort = e.get('from_cohort')
                    to_cohort = e.get('to_cohort')
                    if from_cohort and not to_cohort:
                        candidate_addr = e.get('to_addr', '').lower()
                        route = f"{from_cohort} \u2192 new EOA"
                        reason = f"Received from {from_cohort} cohort"
                    elif to_cohort and not from_cohort:
                        candidate_addr = e.get('from_addr', '').lower()
                        route = f"new EOA \u2192 {to_cohort}"
                        reason = f"Sending to {to_cohort} cohort"
                    else:
                        candidate_addr = e.get('to_addr', '').lower()
                        route = "new EOA \u2192 new EOA"
                        reason = "Transfer between unknown addresses"
                    if candidate_addr:
                        candidates.append({
                            'source': 'whale_events',
                            'address': candidate_addr,
                            'received_strk': e.get('amount_strk', 0),
                            'route': route,
                            'reason': reason,
                            'tx_hash': e.get('tx_hash'),
                            'ts': e.get('ts'),
                        })
                except Exception:
                    continue
    except Exception as ex:
        logger.warning(f"Failed to read {WHALE_EVENTS_FILE}: {ex}")
    return candidates


def load_discovery_candidates():
    data = load_json(DISCOVERY_CANDIDATES_FILE)
    if not data:
        return []
    out = []
    for c in data.get('candidates') or []:
        addr = c.get('address', '').lower()
        if not addr:
            continue
        pattern = c.get('pattern', 'unknown')
        if 'SMART' in pattern.upper():
            route = "SMART \u2192 new EOA"
        elif 'CEX' in pattern.upper():
            route = "CEX \u2192 new EOA"
        else:
            route = f"discovery: {pattern}"
        reason = c.get('pattern_reason', pattern)[:120]
        out.append({
            'source': 'auto_discovery',
            'address': addr,
            'received_strk': c.get('received_strk', 0),
            'route': route,
            'reason': reason,
            'tx_hash': None,
        })
    return out


def format_alert(candidate, priority):
    """Short WATCH? format."""
    addr = candidate['address']
    amt_m = candidate.get('received_strk', 0) / 1e6
    route = candidate.get('route', '?')
    reason = candidate.get('reason', '')
    tx_hash = candidate.get('tx_hash')
    text = "<b>WATCH?</b>\n"
    text += f"<code>{addr}</code>\n"
    text += f"{route} \u00b7 <b>{amt_m:.2f}M</b>\n"
    text += f"<b>ADD:</b> {priority}\n"
    if reason:
        text += f"<i>{reason}</i>\n"
    if tx_hash:
        text += f"<a href=\"https://etherscan.io/tx/{tx_hash}\">tx</a>"
    else:
        text += f"<a href=\"https://etherscan.io/address/{addr}\">address</a>"
    return text


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured; would send:")
        logger.warning(text[:400])
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"WATCH? sent \u00b7 message_id={result.get('result', {}).get('message_id')}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")
        return False


def log_history(candidate, priority, sent):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'address': candidate['address'],
        'priority': priority,
        'source': candidate.get('source'),
        'received_strk': candidate.get('received_strk', 0),
        'route': candidate.get('route'),
        'reason': candidate.get('reason'),
        'tx_hash': candidate.get('tx_hash'),
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append history: {e}")


def main():
    logger.info("=" * 60)
    logger.info("WATCHLIST NOTIFIER \u00b7 WATCH? alerts (only source in chat)")
    logger.info("=" * 60)

    whale_cands = load_whale_candidates()
    discovery_cands = load_discovery_candidates()
    all_cands = whale_cands + discovery_cands
    logger.info(f"Loaded: {len(whale_cands)} from whale_events, {len(discovery_cands)} from auto_discovery")

    if not all_cands:
        logger.info("No candidates \u2014 nothing to alert")
        return 0

    # Dedup by address, keep highest received_strk
    by_addr = {}
    for c in all_cands:
        addr = c['address']
        if not addr:
            continue
        if addr not in by_addr or c['received_strk'] > by_addr[addr]['received_strk']:
            by_addr[addr] = c
    unique_cands = list(by_addr.values())
    logger.info(f"Unique candidates (dedup by address): {len(unique_cands)}")

    seeds_addrs = load_seeds_addresses()
    logger.info(f"flow_seeds contains {len(seeds_addrs)} addresses (skip if in seeds)")

    state = load_state()

    alerts_sent = 0
    skipped_in_seeds = 0
    skipped_recently_alerted = 0
    skipped_below_threshold = 0

    for c in unique_cands:
        addr = c['address']
        received = c.get('received_strk', 0)
        if addr in seeds_addrs:
            skipped_in_seeds += 1
            continue
        priority = determine_priority(received)
        if priority is None:
            skipped_below_threshold += 1
            continue
        if is_recently_alerted(addr, state):
            skipped_recently_alerted += 1
            continue
        text = format_alert(c, priority)
        sent = send_telegram(text)
        log_history(c, priority, sent)
        if sent:
            state['address_last_alert'][addr] = datetime.now(timezone.utc).isoformat()
            state['alert_count'] = state.get('alert_count', 0) + 1
            alerts_sent += 1
            time.sleep(1)

    # Cleanup dedup state older than 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    fresh = {}
    for addr, ts_str in state.get('address_last_alert', {}).items():
        try:
            if datetime.fromisoformat(ts_str) > cutoff:
                fresh[addr] = ts_str
        except Exception:
            pass
    state['address_last_alert'] = fresh
    save_state(state)

    logger.info("")
    logger.info("SUMMARY:")
    logger.info(f"  \u00b7 WATCH? alerts sent: {alerts_sent}")
    logger.info(f"  \u00b7 Skipped (already in seeds): {skipped_in_seeds}")
    logger.info(f"  \u00b7 Skipped (dedup 7d): {skipped_recently_alerted}")
    logger.info(f"  \u00b7 Skipped (below threshold): {skipped_below_threshold}")
    logger.info(f"  \u00b7 Total lifetime alerts: {state.get('alert_count', 0)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())