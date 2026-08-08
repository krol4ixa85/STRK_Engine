#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watchlist_notifier.py — WATCHLIST_CANDIDATE alerts

Читает scripts/detectors/auto_discovery_candidates.json (свежие кандидаты).
Для каждого:
  - Проверяет что не уже в flow_seeds.json
  - Определяет priority по received_strk threshold
  - Отправляет Telegram alert отдельным стримом (НЕ в digest)
  - Пишет запись в data/history/discovery_candidates.jsonl (append-only)

Не меняет DECISION. Не входит в confluence_gate. Только новые кошельки на watchlist.

Правила приоритета:
  received_strk >= 5_000_000  → HIGH (🚨)
  received_strk >= 2_000_000  → MEDIUM (👀)
  received_strk >= 500_000    → LOW (📌)
  ниже — не алерт

De-duplication: state file запоминает уже отправленные адреса.
Не отправляем один и тот же адрес чаще чем раз в 7 дней.
"""
import os
import sys
import json
import time
import hashlib
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
CANDIDATES_FILE = CACHE_DIR / 'auto_discovery_candidates.json'
STATE_FILE = CACHE_DIR / 'watchlist_notifier_state.json'
HISTORY_FILE = HISTORY_DIR / 'discovery_candidates.jsonl'

# Priority thresholds (STRK)
THRESHOLD_HIGH = 5_000_000
THRESHOLD_MEDIUM = 2_000_000
THRESHOLD_LOW = 500_000

DEDUP_DAYS = 7  # не спамить один address чаще


def load_json(path):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
    return {}


def load_state():
    """{'address_last_alert': {addr: iso_ts}, 'alert_count': int}"""
    return load_json(STATE_FILE) or {'address_last_alert': {}, 'alert_count': 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_seeds_addresses():
    """Return set of all addresses in flow_seeds.json (any category)."""
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
        return 'HIGH', '🚨', THRESHOLD_HIGH
    if received_strk >= THRESHOLD_MEDIUM:
        return 'MEDIUM', '👀', THRESHOLD_MEDIUM
    if received_strk >= THRESHOLD_LOW:
        return 'LOW', '📌', THRESHOLD_LOW
    return None, None, None


def is_recently_alerted(address, state):
    """Check if this address was alerted in last DEDUP_DAYS."""
    last_alerts = state.get('address_last_alert', {})
    if address not in last_alerts:
        return False
    try:
        last_ts = datetime.fromisoformat(last_alerts[address])
        age = datetime.now(timezone.utc) - last_ts
        return age < timedelta(days=DEDUP_DAYS)
    except Exception:
        return False


def format_alert(candidate, priority, emoji, threshold):
    address = candidate['address']
    received = candidate.get('received_strk', 0)
    sent = candidate.get('sent_strk', 0)
    retention = candidate.get('retention_pct', 0)
    n_sources = candidate.get('n_sources', 0)
    pattern = candidate.get('pattern', 'unknown')
    reason = candidate.get('pattern_reason', '')
    balance = candidate.get('current_balance_strk', 0)

    short_addr = f"{address[:8]}...{address[-6:]}"

    text = f"{emoji} <b>WATCHLIST CANDIDATE · {priority}</b>\n\n"
    text += f"<b>Address:</b> <code>{short_addr}</code>\n"
    text += f"<b>Pattern:</b> {pattern}\n"
    if reason:
        text += f"<i>{reason}</i>\n"
    text += "\n"

    text += f"<b>Received (30d):</b> {received/1e6:.2f}M STRK "
    text += f"from {n_sources} source{'s' if n_sources != 1 else ''}\n"
    if sent > 0:
        text += f"<b>Sent:</b> {sent/1e6:.2f}M STRK (retention {retention:.0f}%)\n"
    if balance > 0:
        text += f"<b>Current balance:</b> {balance/1e6:.2f}M STRK\n"

    text += f"\n<b>Suggested threshold:</b> {threshold/1e6:.1f}M STRK (для future whale alerts)\n"
    text += f"\n<a href='https://etherscan.io/address/{address}'>Etherscan</a>"
    text += " · "
    text += f"<a href='https://starkscan.co/contract/{address}'>Starkscan</a>\n"

    text += "\n<i>💡 Действие: если решишь добавить — вручную в flow_seeds.json категорию watchlist. "
    text += "Не меняет DECISION.</i>"

    return text


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured — would send:")
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
                logger.info(f"WATCHLIST alert sent · message_id={result.get('result', {}).get('message_id')}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")
        return False


def log_history(candidate, priority, sent):
    """Append record to data/history/discovery_candidates.jsonl."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'address': candidate['address'],
        'priority': priority,
        'received_strk': candidate.get('received_strk', 0),
        'sent_strk': candidate.get('sent_strk', 0),
        'retention_pct': candidate.get('retention_pct', 0),
        'n_sources': candidate.get('n_sources', 0),
        'pattern': candidate.get('pattern', 'unknown'),
        'pattern_reason': candidate.get('pattern_reason', ''),
        'current_balance_strk': candidate.get('current_balance_strk', 0),
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append history: {e}")


def main():
    logger.info("=" * 60)
    logger.info("WATCHLIST NOTIFIER · auto_discovery → Telegram alerts")
    logger.info("=" * 60)

    candidates_data = load_json(CANDIDATES_FILE)
    if not candidates_data:
        logger.info("No candidates file — auto_discovery has not run or empty. Skipping.")
        return 0

    candidates = candidates_data.get('candidates') or []
    if not candidates:
        logger.info("No candidates in file — nothing to alert.")
        return 0

    logger.info(f"Loaded {len(candidates)} candidates from auto_discovery")

    seeds_addrs = load_seeds_addresses()
    logger.info(f"Loaded {len(seeds_addrs)} addresses from flow_seeds.json")

    state = load_state()

    alerts_sent = 0
    skipped_in_seeds = 0
    skipped_recently_alerted = 0
    skipped_below_threshold = 0

    for c in candidates:
        addr = c.get('address', '').lower()
        if not addr:
            continue

        # Skip if already in seeds
        if addr in seeds_addrs:
            skipped_in_seeds += 1
            continue

        # Determine priority
        received = c.get('received_strk', 0)
        priority, emoji, threshold = determine_priority(received)
        if priority is None:
            skipped_below_threshold += 1
            continue

        # Dedup: skip if alerted in last DEDUP_DAYS
        if is_recently_alerted(addr, state):
            skipped_recently_alerted += 1
            continue

        # Send alert
        text = format_alert(c, priority, emoji, threshold)
        sent = send_telegram(text)

        # Log to history (always, even if send failed)
        log_history(c, priority, sent)

        # Update dedup state
        if sent:
            state['address_last_alert'][addr] = datetime.now(timezone.utc).isoformat()
            state['alert_count'] = state.get('alert_count', 0) + 1
            alerts_sent += 1
            # Rate limit between alerts
            time.sleep(1)

    # Clean up dedup state — remove addresses older than 30 days
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
    logger.info(f"SUMMARY:")
    logger.info(f"  · Alerts sent: {alerts_sent}")
    logger.info(f"  · Skipped (already in seeds): {skipped_in_seeds}")
    logger.info(f"  · Skipped (dedup — alerted recently): {skipped_recently_alerted}")
    logger.info(f"  · Skipped (below threshold): {skipped_below_threshold}")
    logger.info(f"  · Total lifetime alerts: {state.get('alert_count', 0)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())