#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cex_flow_alert.py — детектор transition CEX flow signal.

Читает existing cex_flow.json (Etherscan-based, обновляется hourly в main workflow).
Детектит когда signal переходит между категориями DISTRIBUTION ↔ ACCUMULATION.
Отправляет alert в @strk_dynamic3_bot с уровнем severity.

Категории (из cex_flow_backtest.py):
  STRONG_ACCUMULATION → direction = +2  (крупные withdrawals с CEX)
  MILD_ACCUMULATION   → direction = +1
  NEUTRAL / MIXED     → direction =  0
  MILD_DISTRIBUTION   → direction = -1
  STRONG_DISTRIBUTION → direction = -2  (крупные deposits на CEX)

Transition classification:
  - BULLISH_FLIP    — DISTRIBUTION → ACCUMULATION (переход через neutral)
  - BEARISH_FLIP    — ACCUMULATION → DISTRIBUTION
  - BULLISH_SHIFT   — DISTRIBUTION → NEUTRAL / STRONG → MILD (ослабление bear)
  - BEARISH_SHIFT   — ACCUMULATION → NEUTRAL / STRONG → MILD (ослабление bull)
  - INTENSIFICATION — MILD_X → STRONG_X (усиление existing signal)
  - NO_CHANGE       — same category

Alerts отправляются только на:
  - BULLISH_FLIP / BEARISH_FLIP (важный переход)
  - INTENSIFICATION в направлении STRONG_ACCUMULATION / STRONG_DISTRIBUTION

Dedup: не отправлять если alert был < 24h назад с той же transition.

Persistence: data/cache/cex_flow_alert_state.json
History:     data/history/cex_flow_alerts.jsonl
"""
import os
import sys
import json
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
CEX_FLOW_FILE = CACHE_DIR / 'cex_flow.json'
STATE_FILE = CACHE_DIR / 'cex_flow_alert_state.json'
HISTORY_FILE = HISTORY_DIR / 'cex_flow_alerts.jsonl'

DEDUP_HOURS = 24


def load_json(path):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {path.name}: {e}")
    return None


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def signal_to_direction(signal):
    """Map signal string to numeric direction and strength."""
    if not signal:
        return 0, 'unknown'
    s = signal.upper()
    if s == 'STRONG_ACCUMULATION':
        return 2, 'strong_bull'
    elif s == 'MILD_ACCUMULATION':
        return 1, 'mild_bull'
    elif s in ('NEUTRAL', 'MIXED', 'MIXED_SIGNAL'):
        return 0, 'neutral'
    elif s == 'MILD_DISTRIBUTION':
        return -1, 'mild_bear'
    elif s == 'STRONG_DISTRIBUTION':
        return -2, 'strong_bear'
    return 0, 'unknown'


def classify_transition(prev_signal, curr_signal):
    """Classify what kind of transition happened.
    Returns (transition_type, severity, is_alertable).
    """
    if not prev_signal or prev_signal == curr_signal:
        return 'NO_CHANGE', 'none', False

    prev_dir, prev_str = signal_to_direction(prev_signal)
    curr_dir, curr_str = signal_to_direction(curr_signal)

    # Sign change — самое важное
    if prev_dir < 0 and curr_dir > 0:
        return 'BULLISH_FLIP', 'high', True
    if prev_dir > 0 and curr_dir < 0:
        return 'BEARISH_FLIP', 'high', True

    # Escalation to strong (уровень усиления)
    if curr_signal == 'STRONG_ACCUMULATION' and prev_signal in ('MILD_ACCUMULATION', 'NEUTRAL', 'MIXED', 'MIXED_SIGNAL'):
        return 'BULLISH_INTENSIFICATION', 'medium', True
    if curr_signal == 'STRONG_DISTRIBUTION' and prev_signal in ('MILD_DISTRIBUTION', 'NEUTRAL', 'MIXED', 'MIXED_SIGNAL'):
        return 'BEARISH_INTENSIFICATION', 'medium', True

    # Weakening (bearish → neutral or strong → mild — early bullish shift)
    if prev_dir < 0 and curr_dir == 0:
        return 'BULLISH_SHIFT', 'low', False  # important but frequent — no alert
    if prev_signal == 'STRONG_DISTRIBUTION' and curr_signal == 'MILD_DISTRIBUTION':
        return 'BULLISH_SHIFT', 'low', False
    # ACCUMULATION weakening
    if prev_dir > 0 and curr_dir == 0:
        return 'BEARISH_SHIFT', 'low', False
    if prev_signal == 'STRONG_ACCUMULATION' and curr_signal == 'MILD_ACCUMULATION':
        return 'BEARISH_SHIFT', 'low', False

    return 'MINOR_CHANGE', 'none', False


def is_within_dedup(state, transition_type):
    """Check if same transition was alerted recently (< 24h)."""
    last_alert = state.get('last_alerts', {}).get(transition_type)
    if not last_alert:
        return False
    try:
        last_ts = datetime.fromisoformat(last_alert)
        age = datetime.now(timezone.utc) - last_ts
        return age < timedelta(hours=DEDUP_HOURS)
    except Exception:
        return False


def format_alert(transition_type, prev_signal, curr_signal, stats, interpretation):
    """Build Telegram alert message."""
    emoji_map = {
        'BULLISH_FLIP': '🟢🔄',
        'BEARISH_FLIP': '🔴🔄',
        'BULLISH_INTENSIFICATION': '🟢⚡',
        'BEARISH_INTENSIFICATION': '🔴⚡',
    }
    emoji = emoji_map.get(transition_type, '⚪')

    title_map = {
        'BULLISH_FLIP': 'CEX FLOW · BULLISH FLIP',
        'BEARISH_FLIP': 'CEX FLOW · BEARISH FLIP',
        'BULLISH_INTENSIFICATION': 'CEX FLOW · STRONG ACCUMULATION',
        'BEARISH_INTENSIFICATION': 'CEX FLOW · STRONG DISTRIBUTION',
    }
    title = title_map.get(transition_type, f'CEX FLOW · {transition_type}')

    text = f"{emoji} <b>{title}</b>\n\n"
    text += f"<b>Signal:</b> <code>{prev_signal}</code> → <code>{curr_signal}</code>\n\n"

    total_net = stats.get('total_net_strk', 0)
    consec_bull = stats.get('consecutive_bullish', 0)
    consec_bear = stats.get('consecutive_bearish', 0)
    bull_days = stats.get('bullish_days', 0)
    bear_days = stats.get('bearish_days', 0)

    text += "<b>Stats (7d window):</b>\n"
    text += f"  Total net:   <code>{total_net/1e6:+.2f}M</code> STRK\n"
    text += f"  Bull days:   <code>{bull_days}</code>\n"
    text += f"  Bear days:   <code>{bear_days}</code>\n"
    text += f"  Consecutive: bull <code>{consec_bull}d</code> · bear <code>{consec_bear}d</code>\n\n"

    if transition_type == 'BULLISH_FLIP':
        text += "<i>💡 Withdrawals с бирж превысили deposits. Smart money accumulation start.</i>\n"
        text += "<i>💡 Часто предшествует rally на 3-14 дней. Не гарантия, но strong signal.</i>\n"
    elif transition_type == 'BEARISH_FLIP':
        text += "<i>💡 Deposits на биржи превысили withdrawals. Distribution начинается.</i>\n"
        text += "<i>💡 Часто предшествует sell-off на 3-14 дней. Активные positions — тактически scale-out.</i>\n"
    elif transition_type == 'BULLISH_INTENSIFICATION':
        text += "<i>💡 Signal усилился до STRONG_ACCUMULATION. Крупные withdrawals ускоряются.</i>\n"
    elif transition_type == 'BEARISH_INTENSIFICATION':
        text += "<i>💡 Signal усилился до STRONG_DISTRIBUTION. Крупные deposits ускоряются.</i>\n"

    if interpretation:
        text += f"\n<b>Analytic:</b> <i>{interpretation}</i>\n"

    text += "\n<i>⚠ Signal ≠ decision. Проверьте DECISION в @STRK_GUARDIAN_BOT перед действием.</i>"
    return text


def send_telegram(text, token, chat_id):
    if not token or not chat_id:
        logger.warning("Squeeze bot not configured — would send:")
        logger.warning(text[:400])
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                msg_id = result.get('result', {}).get('message_id')
                logger.info(f"Alert sent · message_id={msg_id}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send: {e}")
        return False


def log_history(prev_signal, curr_signal, transition_type, severity, sent):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'prev_signal': prev_signal,
        'curr_signal': curr_signal,
        'transition_type': transition_type,
        'severity': severity,
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append history: {e}")


def main():
    logger.info("=" * 60)
    logger.info("CEX FLOW ALERT DETECTOR")
    logger.info("=" * 60)

    # Load current CEX flow state
    cex = load_json(CEX_FLOW_FILE)
    if not cex:
        logger.error(f"cex_flow.json not found or empty at {CEX_FLOW_FILE}")
        return 0

    classification = cex.get('classification') or {}
    curr_signal = classification.get('signal')
    if not curr_signal:
        logger.warning("No signal in cex_flow.json/classification — skip")
        return 0

    stats = classification.get('stats') or {}
    interpretation = classification.get('interpretation', '')

    logger.info(f"Current signal: {curr_signal}")
    logger.info(f"  Total net 7d: {stats.get('total_net_strk', 0)/1e6:+.2f}M STRK")
    logger.info(f"  Consecutive: bull={stats.get('consecutive_bullish', 0)}d "
                f"bear={stats.get('consecutive_bearish', 0)}d")

    # Load previous state
    state = load_json(STATE_FILE) or {
        'last_signal': None,
        'last_check_ts': None,
        'last_alerts': {},  # transition_type -> ts
        'alert_count': 0,
    }
    prev_signal = state.get('last_signal')

    # Classify transition
    transition_type, severity, is_alertable = classify_transition(prev_signal, curr_signal)
    logger.info(f"Transition: {prev_signal} → {curr_signal}")
    logger.info(f"  Type: {transition_type} · Severity: {severity} · Alertable: {is_alertable}")

    sent = False
    if is_alertable:
        if is_within_dedup(state, transition_type):
            logger.info(f"Dedup — same transition alerted < {DEDUP_HOURS}h ago, skip")
        else:
            token = os.environ.get('SQUEEZE_BOT_TOKEN', '')
            chat_id = os.environ.get('SQUEEZE_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
            text = format_alert(transition_type, prev_signal, curr_signal, stats, interpretation)
            sent = send_telegram(text, token, chat_id)
            if sent:
                state.setdefault('last_alerts', {})[transition_type] = \
                    datetime.now(timezone.utc).isoformat()
                state['alert_count'] = state.get('alert_count', 0) + 1

    # Save state
    state['last_signal'] = curr_signal
    state['last_check_ts'] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_FILE, state)

    # Log history (each check, sent or not)
    if transition_type != 'NO_CHANGE':
        log_history(prev_signal, curr_signal, transition_type, severity, sent)

    logger.info(f"Total alerts sent: {state.get('alert_count', 0)}")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
