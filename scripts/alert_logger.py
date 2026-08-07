#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alert_logger.py — Append-only лог всех Telegram-алертов

Пишет в data/history/alerts.jsonl одну строку JSON на каждое событие
отправки (digest, whale alert, любой другой send).

Формат одной строки:
{
  "ts": "2026-08-07T14:00:00Z",
  "run_id": "digest_1234567890" (env STRK_RUN_ID или fallback),
  "event_type": "digest" | "whale_alert" | "command_reply" | ...,
  "sent_status": "SENT" | "FAILED" | "DRY_RUN",
  "error_msg": "",
  "chat_id_hash": "sha256:8abc...",   // masked, 8 hex chars only
  "text_length_chars": 3872,
  "text_sha256": "sha256:d3adb33f...",  // для дедупликации
  "decision": {
    "signal": "NO_SIGNAL",
    "confidence": "LOW",
    "rally_score": 2,
    "crash_score": 3,
    "summary": "No confluence"
  },
  "composite_2nd_opinion": {
    "direction": "DISTRIBUTION",
    "strength": null,
    "confidence": "MEDIUM"
  },
  "wyckoff": {"phase": "DISTRIBUTION", "sub_phase": "Phase D"},
  "price_usd": 0.0262,
  "why_short": "confluence: No confluence... wyckoff: DISTRIBUTION Phase D",
  "extra": {}
}
"""
import os
import sys
import json
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
_REPO_ROOT = _HERE.parent
_CACHE_DIR = _REPO_ROOT / 'data' / 'cache'
_HISTORY_DIR = _REPO_ROOT / 'data' / 'history'
_ALERTS_FILE = _HISTORY_DIR / 'alerts.jsonl'

logger = logging.getLogger('alert_logger')


def _hash_chat_id(chat_id):
    if not chat_id:
        return ''
    h = hashlib.sha256(chat_id.encode('utf-8')).hexdigest()[:16]
    return f'sha256:{h}'


def _hash_text(text):
    if not text:
        return ''
    h = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    return f'sha256:{h}'


def _load_json_safe(name):
    p = _CACHE_DIR / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        logger.warning(f'load {name}: {e}')
        return {}


def _fetch_price_okx():
    import urllib.request
    try:
        url = 'https://www.okx.com/api/v5/market/ticker?instId=STRK-USDT'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        if d.get('code') == '0' and d.get('data'):
            return float(d['data'][0]['last'])
    except Exception:
        pass
    return 0.0


def _extract_decision_state():
    conf = _load_json_safe('confluence_gate.json')
    comp = _load_json_safe('composite_signal_v2.json')
    wyk = _load_json_safe('wyckoff_phase.json')
    tech = _load_json_safe('technical_momentum.json')

    price = ((tech.get('features') or {}).get('price')) or _fetch_price_okx()

    return {
        'decision': {
            'signal': conf.get('signal', 'NO_DATA'),
            'confidence': conf.get('confidence', 'UNKNOWN'),
            'rally_score': conf.get('rally_score', 0),
            'crash_score': conf.get('crash_score', 0),
            'summary': (conf.get('summary') or '')[:400],
        },
        'composite_2nd_opinion': {
            'direction': comp.get('direction'),
            'strength': comp.get('strength'),
            'confidence': comp.get('confidence'),
        },
        'wyckoff': {
            'phase': wyk.get('phase'),
            'sub_phase': wyk.get('sub_phase'),
            'confidence': wyk.get('confidence'),
        },
        'price_usd': round(price, 6) if price else None,
    }


def _resolve_status(sent):
    if isinstance(sent, dict):
        return sent.get('status', 'UNKNOWN')
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return 'DRY_RUN'
    return 'SENT' if sent else 'FAILED'


def _build_why_short(state, max_len=200):
    parts = []
    dec = state.get('decision', {})
    if dec.get('summary'):
        parts.append(f"confluence: {dec['summary'][:100]}")
    wyk = state.get('wyckoff', {})
    if wyk.get('phase'):
        wpart = f"wyckoff: {wyk['phase']}"
        if wyk.get('sub_phase'):
            wpart += f" {wyk['sub_phase']}"
        parts.append(wpart)
    comp = state.get('composite_2nd_opinion', {})
    if comp.get('direction'):
        parts.append(f"composite: {comp['direction']}")
    return ' · '.join(parts)[:max_len]


def _resolve_run_id():
    rid = os.environ.get('STRK_RUN_ID')
    if rid:
        return rid
    return 'alert_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')


def log_alert(event_type, text='', sent=True, error_msg='', extra=None):
    """
    Append one line to data/history/alerts.jsonl.

    Args:
        event_type: 'digest' | 'whale_alert' | 'command_reply' | 'critical_move'
        text: полный текст сообщения (для длины и хеша, не для хранения)
        sent: bool от send_telegram (True=success, False=failed или not configured)
        error_msg: если send бросил исключение — текст ошибки
        extra: event-type specific fields (whale_amount, cmd_name и т.д.)

    Returns:
        dict записанной строки (для дальнейшего использования)

    Safety:
        · Не бросает исключений вверх (log-only функция не должна ломать send)
        · Создаёт папку data/history/ при отсутствии
    """
    try:
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        state = _extract_decision_state()
        status = _resolve_status(sent)

        record = {
            'ts': now.isoformat(),
            'run_id': _resolve_run_id(),
            'event_type': event_type,
            'sent_status': status,
            'error_msg': str(error_msg or ''),
            'chat_id_hash': _hash_chat_id(chat_id),
            'text_length_chars': len(text or ''),
            'text_sha256': _hash_text(text or ''),
            'decision': state['decision'],
            'composite_2nd_opinion': state['composite_2nd_opinion'],
            'wyckoff': state['wyckoff'],
            'price_usd': state['price_usd'],
            'why_short': _build_why_short(state),
            'extra': extra or {},
        }

        with open(_ALERTS_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

        logger.info(f'alert_logger: appended {event_type} - {status} - '
                    f'signal={state["decision"]["signal"]} - '
                    f'conf={state["decision"]["confidence"]}')
        return record

    except Exception as e:
        sys.stderr.write(f'[alert_logger] ERROR: {e}\n')
        return {}


if __name__ == '__main__':
    if not _ALERTS_FILE.exists():
        print(f'No log yet: {_ALERTS_FILE}')
        sys.exit(0)
    lines = _ALERTS_FILE.read_text(encoding='utf-8').strip().split('\n')
    print(f'Total records in {_ALERTS_FILE}: {len(lines)}')
    print(f'\nLast 5:')
    for line in lines[-5:]:
        try:
            r = json.loads(line)
            print(f"  {r.get('ts', '?')[:19]}  {r.get('event_type', '?'):<15} "
                  f"{r.get('sent_status', '?'):<8} "
                  f"signal={r.get('decision', {}).get('signal', '?')}")
        except Exception:
            continue
