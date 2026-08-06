#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibration_report.py — Отчёт о калибровке shadow voters

Запуск: python3 scripts/calibration_report.py
       или через /calibrate в Telegram (см. patch_telegram_commands.py)

Читает: data/history/shadow_votes.jsonl (CLOSED записи)
Считает: precision по каждому voter × window (72h, 7d)
Формат вывода: текстовая таблица + JSON summary + опциональная отправка в Telegram

Precision = HIT / (HIT + MISS)
   где HIT = voter.vote совпал с outcome_signal
        MISS = voter.vote != outcome_signal (и voter != NEUTRAL/UNKNOWN)
        SKIP = voter не голосовал (NEUTRAL/UNKNOWN) — в знаменатель не идёт

Правила для рекомендации к live-inclusion:
  · N (закрытые CLOSED forecasts) ≥ 15 per voter × window
  · precision ≥ 55%
  · нет monotonic degrade (последние 5 — все MISS)
"""
import os
import sys
import json
import logging
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
CONFIG_FILE = SCRIPT_DIR / 'config' / 'voter_config.json'
SHADOW_FILE = HISTORY_DIR / 'shadow_votes.jsonl'
OUTPUT_JSON = SCRIPT_DIR / 'data' / 'cache' / 'shadow_calibration.json'

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('calib')


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def load_closed_records():
    """Yield all CLOSED records from shadow_votes.jsonl."""
    if not SHADOW_FILE.exists():
        return []
    records = []
    with open(SHADOW_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get('status') == 'CLOSED':
                    records.append(r)
            except json.JSONDecodeError:
                continue
    return records


def build_calibration(records, min_n, min_precision):
    """
    Build precision table:
      per_voter[voter_name][window] = {
        'n_total', 'hit', 'miss', 'skip',
        'precision', 'n_directional',
        'status': 'INSUFFICIENT_DATA' | 'BELOW_THRESHOLD' | 'READY_FOR_LIVE',
        'last_5': [...]
      }
    """
    per_voter = defaultdict(lambda: defaultdict(lambda: {
        'hit': 0, 'miss': 0, 'skip': 0,
        'last_outcomes': [],
    }))
    aggregate = defaultdict(lambda: {'hit': 0, 'miss': 0, 'skip': 0, 'last_outcomes': []})

    for r in sorted(records, key=lambda x: x.get('issued_at', '')):
        window = r.get('window', 'unknown')
        per_voter_outcome = r.get('per_voter_outcome') or {}
        for voter, outcome in per_voter_outcome.items():
            bucket = per_voter[voter][window]
            if outcome == 'HIT' or outcome == 'HIT_NEUTRAL':
                bucket['hit'] += 1
                bucket['last_outcomes'].append('H')
            elif outcome == 'MISS' or outcome == 'MISS_NO_MOVE':
                bucket['miss'] += 1
                bucket['last_outcomes'].append('M')
            else:  # SKIP_*
                bucket['skip'] += 1
                bucket['last_outcomes'].append('.')
            # cap last outcomes
            if len(bucket['last_outcomes']) > 10:
                bucket['last_outcomes'] = bucket['last_outcomes'][-10:]

        # Aggregate
        agg_outcome = r.get('aggregate_outcome')
        if agg_outcome:
            b = aggregate[window]
            if agg_outcome == 'HIT' or agg_outcome == 'HIT_NEUTRAL':
                b['hit'] += 1
                b['last_outcomes'].append('H')
            elif agg_outcome == 'MISS' or agg_outcome == 'MISS_NO_MOVE':
                b['miss'] += 1
                b['last_outcomes'].append('M')
            else:
                b['skip'] += 1
                b['last_outcomes'].append('.')
            if len(b['last_outcomes']) > 10:
                b['last_outcomes'] = b['last_outcomes'][-10:]

    # Compute precision + status
    def finalize(bucket):
        directional = bucket['hit'] + bucket['miss']
        precision = bucket['hit'] / directional if directional > 0 else None
        n_total = bucket['hit'] + bucket['miss'] + bucket['skip']

        # Status
        if directional < min_n:
            status = f'INSUFFICIENT_DATA (need {min_n} directional, have {directional})'
        elif precision is not None and precision >= min_precision:
            status = f'READY_FOR_LIVE (precision {precision:.1%} >= {min_precision:.0%})'
        else:
            status = f'BELOW_THRESHOLD (precision {precision:.1%} < {min_precision:.0%})'

        # Degrade check: last 5 all MISS
        last5 = bucket['last_outcomes'][-5:]
        directional_last5 = [o for o in last5 if o in ('H', 'M')]
        if len(directional_last5) >= 5 and all(o == 'M' for o in directional_last5):
            status += ' · DEGRADING (last 5 directional = all MISS)'

        return {
            'hit': bucket['hit'],
            'miss': bucket['miss'],
            'skip': bucket['skip'],
            'n_total': n_total,
            'n_directional': directional,
            'precision': round(precision, 3) if precision is not None else None,
            'recent_10': ''.join(bucket['last_outcomes']),
            'status': status,
        }

    result_voters = {}
    for voter, windows in per_voter.items():
        result_voters[voter] = {w: finalize(b) for w, b in windows.items()}

    result_aggregate = {w: finalize(b) for w, b in aggregate.items()}

    return {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'total_closed_records': len(records),
        'thresholds': {
            'min_n_directional': min_n,
            'min_precision': min_precision,
        },
        'per_voter': result_voters,
        'aggregate': result_aggregate,
    }


def format_text_report(calib):
    """Human-readable text table for console + Telegram."""
    lines = []
    lines.append('=' * 62)
    lines.append('SHADOW VOTER CALIBRATION REPORT')
    lines.append('=' * 62)
    lines.append(f'Generated: {calib["as_of"]}')
    lines.append(f'Closed forecasts: {calib["total_closed_records"]}')
    lines.append(f'Thresholds: N ≥ {calib["thresholds"]["min_n_directional"]}, '
                 f'precision ≥ {calib["thresholds"]["min_precision"]:.0%}')
    lines.append('')

    if not calib['per_voter']:
        lines.append('НЕТ CLOSED FORECASTS. Подожди verify_after (72h/7d) для первых записей.')
        return '\n'.join(lines)

    for voter, windows in calib['per_voter'].items():
        lines.append(f'--- {voter} ---')
        for w in ('72h', '7d'):
            if w not in windows:
                lines.append(f'  {w:5}: no data')
                continue
            b = windows[w]
            prec_str = f'{b["precision"]:.1%}' if b['precision'] is not None else '—'
            lines.append(
                f'  {w:5}: HIT={b["hit"]:3d}  MISS={b["miss"]:3d}  SKIP={b["skip"]:3d}  '
                f'prec={prec_str:>6}  recent=[{b["recent_10"]}]'
            )
            lines.append(f'         {b["status"]}')
        lines.append('')

    lines.append('=== AGGREGATE (shadow_signal vs outcome) ===')
    for w in ('72h', '7d'):
        if w not in calib['aggregate']:
            lines.append(f'  {w:5}: no data')
            continue
        b = calib['aggregate'][w]
        prec_str = f'{b["precision"]:.1%}' if b['precision'] is not None else '—'
        lines.append(
            f'  {w:5}: HIT={b["hit"]:3d}  MISS={b["miss"]:3d}  SKIP={b["skip"]:3d}  '
            f'prec={prec_str:>6}  recent=[{b["recent_10"]}]'
        )
        lines.append(f'         {b["status"]}')

    lines.append('')
    lines.append('=' * 62)
    lines.append('STATUS: HYPOTHESIS · shadow voters DO NOT affect real decisions')
    lines.append('Live-inclusion требует: N_directional ≥ ' + str(calib['thresholds']['min_n_directional']) +
                 ' AND precision ≥ ' + f'{calib["thresholds"]["min_precision"]:.0%}')
    lines.append('=' * 62)
    return '\n'.join(lines)


def send_telegram(text, chunk_prefix=''):
    import urllib.request
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.info('Telegram not configured — printing only to stdout')
        return False
    # Split into <pre> chunks < 4000 chars
    body = f'<b>📊 SHADOW CALIBRATION</b>\n<pre>{text}</pre>'
    if len(body) > 4000:
        # Send as document instead
        try:
            import tempfile
            from pathlib import Path as _P
            with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            url = f'https://api.telegram.org/bot{token}/sendDocument'
            boundary = '----ShadowCalibBoundary9f3a2c'
            data = _P(tmp_path).read_bytes()
            body_parts = (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="caption"\r\n\r\n'
                f'📊 Shadow Calibration Report\r\n'
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="document"; filename="calibration.txt"\r\n'
                f'Content-Type: text/plain\r\n\r\n'
            ).encode('utf-8')
            body_parts += data
            body_parts += f'\r\n--{boundary}--\r\n'.encode()
            req = urllib.request.Request(
                url, data=body_parts,
                headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
            urllib.request.urlopen(req, timeout=30)
            os.unlink(tmp_path)
            return True
        except Exception as e:
            logger.warning(f'sendDocument failed: {e}')
            return False
    else:
        try:
            url = f'https://api.telegram.org/bot{token}/sendMessage'
            data = json.dumps({'chat_id': chat_id, 'text': body, 'parse_mode': 'HTML'}).encode()
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=15)
            return True
        except Exception as e:
            logger.warning(f'sendMessage failed: {e}')
            return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--telegram', action='store_true', help='Send to Telegram after computing')
    parser.add_argument('--min-n', type=int, default=None, help='Override min_n_directional (config default)')
    parser.add_argument('--min-precision', type=float, default=None, help='Override min_precision (0-1)')
    args = parser.parse_args()

    config = load_config()
    rules = ((config.get('_meta') or {}).get('calibration_rules') or {})
    min_n = args.min_n if args.min_n is not None else rules.get('min_closed_forecasts_per_voter', 15)
    min_prec = args.min_precision if args.min_precision is not None else rules.get('min_precision_to_enable_live', 0.55)

    records = load_closed_records()
    calib = build_calibration(records, min_n, min_prec)

    # Persist JSON summary
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(calib, indent=2, ensure_ascii=False), encoding='utf-8')

    # Print text report
    text = format_text_report(calib)
    print(text)

    if args.telegram:
        sent = send_telegram(text)
        if sent:
            print('\n[OK] Report sent to Telegram')
        else:
            print('\n[WARN] Telegram send failed')

    return 0


if __name__ == '__main__':
    sys.exit(main())
