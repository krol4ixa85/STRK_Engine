#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
squeeze_notifier.py — отправляет squeeze alerts в @strk_dynamic3_bot.

Читает data/cache/squeeze_state.json (пишет squeeze_detector.py).
Сравнивает с прошлым состоянием (data/cache/squeeze_notifier_state.json).
Отправляет alert при transitions:
  INACTIVE → ACTIVE  = WATCH alert
  ACTIVE   → STRONG  = STRONG alert
  INACTIVE → STRONG  = STRONG alert
  STRONG   → INACTIVE = COOL_DOWN notification (optional)

Dedup 24h: если alert отправлен, следующий не раньше чем через 24h.
Persistence: data/history/squeeze_alerts.jsonl

ENV variables:
  SQUEEZE_BOT_TOKEN — токен @strk_dynamic3_bot (отдельный от STRK_GUARDIAN)
  SQUEEZE_CHAT_ID   — chat_id (можно тот же что TELEGRAM_CHAT_ID)
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
STATE_FILE = CACHE_DIR / 'squeeze_state.json'
NOTIFIER_STATE_FILE = CACHE_DIR / 'squeeze_notifier_state.json'
HISTORY_FILE = HISTORY_DIR / 'squeeze_alerts.jsonl'

DEDUP_HOURS = 24


def load_json(path):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_notifier_state():
    return load_json(NOTIFIER_STATE_FILE) or {
        'last_level': 'INACTIVE',
        'last_alert_ts': None,
        'alert_count': 0,
    }


def is_within_dedup(last_alert_ts):
    if not last_alert_ts:
        return False
    try:
        last = datetime.fromisoformat(last_alert_ts)
        age = datetime.now(timezone.utc) - last
        return age < timedelta(hours=DEDUP_HOURS)
    except Exception:
        return False


def format_alert(state, transition_type):
    """Build alert message for Telegram."""
    level = state['level']
    active = state['active_categories']

    if transition_type == 'STRONG':
        emoji = '🚨'
        header = f"<b>SQUEEZE SETUP · STRONG</b>"
    elif transition_type == 'ACTIVE':
        emoji = '⚠️'
        header = f"<b>SQUEEZE SETUP · WATCH</b>"
    elif transition_type == 'COOL_DOWN':
        emoji = '❄️'
        header = f"<b>SQUEEZE COOL DOWN</b>"
    else:
        emoji = '📊'
        header = f"<b>SQUEEZE UPDATE</b>"

    text = f"{emoji} {header}\n\n"
    text += f"Active categories: <b>{active}/3</b>\n\n"

    for cat in state['categories']:
        if not cat['active']:
            continue
        text += f"<b>Category {cat['category']} · {cat['name']}</b> ({cat['active_count']}/3):\n"
        for cond in cat['conditions']:
            if cond['active']:
                text += f"  ✓ {cond['name']}\n"
                text += f"    <i>{cond['evidence']}</i>\n"
        text += "\n"

    # Показываем и inactive для context
    inactive_cats = [c for c in state['categories'] if not c['active']]
    if inactive_cats and transition_type != 'COOL_DOWN':
        text += "<b>Not active:</b>\n"
        for cat in inactive_cats:
            text += f"  · {cat['category']} {cat['name']}: {cat['active_count']}/3\n"
        text += "\n"

    # Interpretation footer с baseline-calibrated targets
    # Baseline STRK: avg daily range 10.5% · stop 15% (1.5×) · take 30% (3×) R/R 2:1
    # Пробуем достать current price для конкретных targets
    current_price = _get_current_price()

    if transition_type == 'STRONG':
        text += "<i>💡 Три условия совпали. Squeeze setup самый сильный.</i>\n"
        text += "<i>💡 Baseline STRK: avg daily range 10.5%. Топливо для +15-30% отскока.</i>\n"
        text += "<i>💡 Timeframe: 12-72h.</i>\n\n"
        if current_price:
            # Long setup — большинство squeeze долгих
            entry = current_price
            stop = current_price * 0.85   # -15%
            take = current_price * 1.30   # +30%
            text += "<b>🎯 Suggested Long (baseline-calibrated):</b>\n"
            text += f"  Entry: <code>${entry:.4f}</code>\n"
            text += f"  Stop:  <code>${stop:.4f}</code> (-15%)\n"
            text += f"  Take:  <code>${take:.4f}</code> (+30%, R/R 2:1)\n\n"
        text += "<i>⚠ Setup — не гарантия. Проверь Action в @STRK_GUARDIAN_BOT перед входом.</i>"
    elif transition_type == 'ACTIVE':
        text += "<i>💡 Squeeze setup формируется. Ждём третьей категории.</i>\n"
        if current_price:
            text += f"<i>💡 Текущая цена: ${current_price:.4f}. Baseline stop: 15%, take: 30%.</i>\n"
        text += "<i>⚠ Setup — не сигнал. Не входить пока не STRONG или подтверждение DECISION.</i>"
    elif transition_type == 'COOL_DOWN':
        text += "<i>💡 Условия перестали совпадать. Setup рассыпался.</i>"

    return text


def _get_current_price():
    """Get current STRK price from any available source (composite/technical/wyckoff)."""
    for cache_file in ['composite_signal_v2.json', 'technical_momentum.json', 'wyckoff_phase.json']:
        try:
            path = CACHE_DIR / cache_file
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Common price fields
                for key in ['price', 'current_price', 'price_usd']:
                    if key in data:
                        val = data[key]
                        if isinstance(val, (int, float)) and 0.01 < val < 100:
                            return float(val)
                # Nested paths
                inputs = data.get('inputs') or {}
                if 'strk_context' in inputs:
                    price = inputs['strk_context'].get('price')
                    if price and 0.01 < price < 100:
                        return float(price)
        except Exception:
            continue
    return None


def send_telegram(text, token, chat_id):
    if not token or not chat_id:
        logger.warning("SQUEEZE bot not configured; would send:")
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
                logger.info(f"Squeeze alert sent · message_id={result.get('result', {}).get('message_id')}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send: {e}")
        return False


def log_history(state, transition_type, sent):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'transition_type': transition_type,
        'level': state['level'],
        'active_categories': state['active_categories'],
        'categories': [
            {
                'category': c['category'],
                'name': c['name'],
                'active': c['active'],
                'active_count': c['active_count'],
                'active_conditions': [
                    {'id': cond['id'], 'evidence': cond['evidence']}
                    for cond in c['conditions'] if cond['active']
                ],
            }
            for c in state['categories']
        ],
        'thresholds': state.get('thresholds'),
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append history: {e}")


def determine_transition(prev_level, current_level):
    """Return transition type or None if no meaningful transition."""
    if prev_level == current_level:
        return None
    # Rising transitions — send alert
    if prev_level == 'INACTIVE' and current_level in ('ACTIVE', 'STRONG'):
        return current_level
    if prev_level == 'ACTIVE' and current_level == 'STRONG':
        return 'STRONG'
    # Cooling — optional notification
    if prev_level == 'STRONG' and current_level in ('ACTIVE', 'INACTIVE'):
        return 'COOL_DOWN'
    if prev_level == 'ACTIVE' and current_level == 'INACTIVE':
        return 'COOL_DOWN'
    return None


def main():
    logger.info("=" * 60)
    logger.info("SQUEEZE NOTIFIER · @strk_dynamic3_bot")
    logger.info("=" * 60)

    state = load_json(STATE_FILE)
    if not state:
        logger.info("No squeeze_state.json — squeeze_detector did not run yet")
        return 0

    current_level = state.get('level', 'INACTIVE')
    logger.info(f"Current level: {current_level} · {state.get('active_categories', 0)}/3 categories active")

    notifier_state = load_notifier_state()
    prev_level = notifier_state.get('last_level', 'INACTIVE')

    transition = determine_transition(prev_level, current_level)
    if transition is None:
        logger.info(f"No transition ({prev_level} → {current_level}), skipping")
        # Still update last_level to current
        notifier_state['last_level'] = current_level
        save_json(NOTIFIER_STATE_FILE, notifier_state)
        return 0

    logger.info(f"Transition: {prev_level} → {current_level} · type={transition}")

    # Dedup for rising transitions (не для COOL_DOWN — те редкие)
    if transition in ('ACTIVE', 'STRONG'):
        if is_within_dedup(notifier_state.get('last_alert_ts')):
            logger.info(f"Dedup: last alert within {DEDUP_HOURS}h — skipping")
            notifier_state['last_level'] = current_level
            save_json(NOTIFIER_STATE_FILE, notifier_state)
            return 0

    # Send alert
    token = os.environ.get('SQUEEZE_BOT_TOKEN', '')
    chat_id = os.environ.get('SQUEEZE_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

    text = format_alert(state, transition)
    sent = send_telegram(text, token, chat_id)

    log_history(state, transition, sent)

    if sent:
        notifier_state['last_alert_ts'] = datetime.now(timezone.utc).isoformat()
        notifier_state['alert_count'] = notifier_state.get('alert_count', 0) + 1

    notifier_state['last_level'] = current_level
    save_json(NOTIFIER_STATE_FILE, notifier_state)

    logger.info(f"Alert count total: {notifier_state.get('alert_count', 0)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())