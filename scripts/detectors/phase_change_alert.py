#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase_change_alert.py — детектор изменений структурных фаз/сигналов.

ЗАЧЕМ: Ты торгуешь руками. Не хочешь смотреть каждый digest.
       Хочешь alert КОГДА реально что-то изменилось.

ЧТО ОТСЛЕЖИВАЕТ (по приоритету):
  1. CURRENT PHASE change — структурная фаза system-wide
     (STRUCTURAL_BEAR → INFLECTION_POINT → EARLY_ACCUMULATION → MARKUP → ...)
  2. Wyckoff phase change
     (CONSOLIDATION → ACCUMULATION → MARKUP → DISTRIBUTION → MARKDOWN)
  3. Dune monthly signal change (последний столбец из monthly query)
     (BEARISH_BREAKDOWN → MIXED_SIGNAL → NEUTRAL_CONSOLIDATION → BULLISH_MOMENTUM)

Каждое изменение = alert в @strk_dynamic3_bot.
Dedup 24h per transition type.

ЗАПУСК: каждый run основного workflow (после Dune collector + daily_digest).
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

STATE_FILE = CACHE_DIR / 'phase_change_alert_state.json'
HISTORY_FILE = HISTORY_DIR / 'phase_change_alerts.jsonl'

DEDUP_HOURS = 24


def load_json(path, default=None):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def get_current_price():
    for fname in ('composite_signal_v2.json', 'technical_momentum.json'):
        d = load_json(CACHE_DIR / fname, {})
        for key in ('price', 'current_price', 'price_usd'):
            v = d.get(key)
            if isinstance(v, (int, float)) and 0.001 < v < 100:
                return float(v)
        inputs = d.get('inputs') or {}
        strk = inputs.get('strk_context') or {}
        v = strk.get('price')
        if v and 0.001 < v < 100:
            return float(v)
    return None


def get_current_phase():
    """Извлекает текущую CURRENT PHASE.
    Смотрим существующий cache файл если есть, иначе computeем on-the-fly."""
    # Пробуем сохранённый snapshot phase (если daily_digest пишет)
    phase_cache = CACHE_DIR / 'current_phase.json'
    if phase_cache.exists():
        d = load_json(phase_cache, {})
        return d.get('phase'), d.get('confidence'), d.get('description')

    # Иначе recompute — импортируем helper
    try:
        # Adds scripts/ to path
        sys.path.insert(0, str(SCRIPT_DIR / 'scripts'))
        import daily_digest as dd
        wyckoff = load_json(CACHE_DIR / 'wyckoff_phase.json', {})
        tech = load_json(CACHE_DIR / 'technical_momentum.json', {})
        squeeze = load_json(CACHE_DIR / 'squeeze_state.json', {})
        dune = dd._load_dune_starknet()
        phase_info = dd._compute_current_phase(wyckoff, tech, dune, squeeze)
        return phase_info.get('phase'), phase_info.get('confidence'), phase_info.get('description')
    except Exception as e:
        logger.warning(f"Cannot compute phase: {e}")
        return None, None, None


def get_wyckoff_phase():
    d = load_json(CACHE_DIR / 'wyckoff_phase.json', {})
    return d.get('phase'), d.get('confidence')


def get_dune_monthly_signal():
    d = load_json(CACHE_DIR / 'dune_starknet_monthly.json', {})
    rows = d.get('rows') or []
    if not rows:
        return None
    latest = rows[0]
    if isinstance(latest, dict):
        return latest.get('phase_signal') or latest.get('signal')
    return None


def is_dedup(state, key):
    """Check if same transition was alerted recently."""
    last_ts = state.get('last_alerts', {}).get(key)
    if not last_ts:
        return False
    try:
        last = datetime.fromisoformat(last_ts)
        age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        return age_h < DEDUP_HOURS
    except Exception:
        return False


def format_phase_alert(kind, prev, curr, extra=''):
    """Format alert text based on transition kind."""
    emoji_map = {
        'CURRENT_PHASE': '📍',
        'WYCKOFF': '🎯',
        'DUNE_MONTHLY': '🌐',
    }
    title_map = {
        'CURRENT_PHASE': 'STRUCTURAL PHASE CHANGE',
        'WYCKOFF': 'WYCKOFF PHASE CHANGE',
        'DUNE_MONTHLY': 'DUNE MONTHLY SIGNAL CHANGE',
    }
    emoji = emoji_map.get(kind, '⚪')
    title = title_map.get(kind, kind)

    # Determine direction/interpretation
    interp = ''
    if kind == 'CURRENT_PHASE':
        bullish_targets = ['EARLY_ACCUMULATION', 'MARKUP', 'INFLECTION_POINT']
        bearish_targets = ['STRUCTURAL_BEAR', 'BEAR_CONSOLIDATION', 'DISTRIBUTION']
        if curr in bullish_targets and prev in bearish_targets:
            interp = '🟢 <b>Structural shift к bullish</b> — возможен разворот. Ждать подтверждения 5-7 дней.'
        elif curr in bearish_targets and prev in bullish_targets:
            interp = '🔴 <b>Structural shift к bearish</b> — приготовиться к scale-out если в лонге.'
        elif curr == 'SQUEEZE_SETUP':
            interp = '🟢 <b>Technical squeeze активирован</b> — 4-24h potential bounce. НЕ путать со сменой фазы.'
        elif curr == 'INFLECTION_POINT':
            interp = '🟡 <b>Inflection point</b> — bear phase может заканчиваться. Слишком рано входить.'

    text = f"{emoji} <b>{title}</b>\n\n"
    text += f"<code>{prev or '?'}</code> → <code>{curr}</code>\n\n"
    if extra:
        text += f"{extra}\n\n"
    if interp:
        text += f"{interp}\n\n"
    text += "<i>⚠ Alert = context change. Проверьте DECISION в @STRK_GUARDIAN_BOT перед действием.</i>"
    return text


def send_telegram(text, token, chat_id):
    if not token or not chat_id:
        logger.warning("Telegram bot not configured — would send:")
        logger.warning(text[:300])
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
                logger.info(f"Alert sent · message_id={result.get('result', {}).get('message_id')}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send: {e}")
        return False


def log_history(kind, prev, curr, sent):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'kind': kind,
        'prev': prev,
        'curr': curr,
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append history: {e}")


def main():
    logger.info("=" * 60)
    logger.info("PHASE CHANGE ALERT DETECTOR")
    logger.info("=" * 60)

    state = load_json(STATE_FILE, {
        'current_phase': None,
        'wyckoff_phase': None,
        'dune_monthly_signal': None,
        'last_alerts': {},
        'alert_count': 0,
    })

    # === Collect current values ===
    curr_phase, curr_phase_conf, curr_phase_desc = get_current_phase()
    curr_wyckoff, curr_wyckoff_conf = get_wyckoff_phase()
    curr_dune = get_dune_monthly_signal()

    logger.info(f"CURRENT PHASE: {curr_phase} ({curr_phase_conf})")
    logger.info(f"Wyckoff phase: {curr_wyckoff} ({curr_wyckoff_conf})")
    logger.info(f"Dune monthly:  {curr_dune}")

    # === Get bot credentials — prefer squeeze bot for real-time alerts ===
    token = os.environ.get('SQUEEZE_BOT_TOKEN', '')
    chat_id = os.environ.get('SQUEEZE_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

    alerts_sent = 0

    # === Check 1: CURRENT PHASE change ===
    prev_phase = state.get('current_phase')
    if curr_phase and curr_phase != 'UNKNOWN' and curr_phase != prev_phase and prev_phase is not None:
        key = 'CURRENT_PHASE'
        if is_dedup(state, key):
            logger.info(f"CURRENT PHASE dedup — same alert < {DEDUP_HOURS}h ago")
        else:
            extra = f"<b>Description:</b> {curr_phase_desc}" if curr_phase_desc else ''
            text = format_phase_alert('CURRENT_PHASE', prev_phase, curr_phase, extra)
            sent = send_telegram(text, token, chat_id)
            if sent:
                state.setdefault('last_alerts', {})[key] = datetime.now(timezone.utc).isoformat()
                state['alert_count'] = state.get('alert_count', 0) + 1
                alerts_sent += 1
            log_history('CURRENT_PHASE', prev_phase, curr_phase, sent)

    # === Check 2: Wyckoff phase change ===
    prev_wyckoff = state.get('wyckoff_phase')
    if curr_wyckoff and curr_wyckoff != prev_wyckoff and prev_wyckoff is not None:
        key = 'WYCKOFF'
        if is_dedup(state, key):
            logger.info(f"Wyckoff dedup — same alert < {DEDUP_HOURS}h ago")
        else:
            extra = f"<b>Confidence:</b> <code>{curr_wyckoff_conf}</code>" if curr_wyckoff_conf else ''
            text = format_phase_alert('WYCKOFF', prev_wyckoff, curr_wyckoff, extra)
            sent = send_telegram(text, token, chat_id)
            if sent:
                state.setdefault('last_alerts', {})[key] = datetime.now(timezone.utc).isoformat()
                state['alert_count'] = state.get('alert_count', 0) + 1
                alerts_sent += 1
            log_history('WYCKOFF', prev_wyckoff, curr_wyckoff, sent)

    # === Check 3: Dune monthly signal change ===
    prev_dune = state.get('dune_monthly_signal')
    if curr_dune and curr_dune != 'UNKNOWN' and curr_dune != prev_dune and prev_dune is not None:
        key = 'DUNE_MONTHLY'
        if is_dedup(state, key):
            logger.info(f"Dune monthly dedup — same alert < {DEDUP_HOURS}h ago")
        else:
            text = format_phase_alert('DUNE_MONTHLY', prev_dune, curr_dune)
            sent = send_telegram(text, token, chat_id)
            if sent:
                state.setdefault('last_alerts', {})[key] = datetime.now(timezone.utc).isoformat()
                state['alert_count'] = state.get('alert_count', 0) + 1
                alerts_sent += 1
            log_history('DUNE_MONTHLY', prev_dune, curr_dune, sent)

    # === Save state ===
    state['current_phase'] = curr_phase
    state['wyckoff_phase'] = curr_wyckoff
    state['dune_monthly_signal'] = curr_dune
    state['last_check_ts'] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_FILE, state)

    logger.info(f"Alerts sent this run: {alerts_sent}")
    logger.info(f"Total alerts sent: {state.get('alert_count', 0)}")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())