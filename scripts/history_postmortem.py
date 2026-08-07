#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
history_postmortem.py — Закрывает PENDING записи в all_history.jsonl

Для каждой PENDING записи с verify_after_72h ≤ now:
  · fetch D1 close на дату verify_after_72h (OKX)
  · outcome_72h = {price, pct_change, signal RALLY/CRASH/NEUTRAL}

Для каждой PENDING записи с verify_after_7d ≤ now:
  · то же для 7d окна

Когда ОБА (72h и 7d) закрыты — status = CLOSED.
Иначе — PARTIAL (закрыто только одно окно).

Пороги RALLY/CRASH/NEUTRAL берутся из voter_config._meta.outcome_signal_thresholds.

Не трогает shadow_votes.jsonl (это другой postmortem).
Не трогает real forecasts.jsonl (это auto_postmortem).

Идемпотентно.
"""
import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
CONFIG_FILE = SCRIPT_DIR / 'config' / 'voter_config.json'
HISTORY_FILE = HISTORY_DIR / 'all_history.jsonl'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('hist_pm')


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def fetch_d1_close(target_dt):
    """Fetch OKX D1 close covering the day of target_dt."""
    try:
        url = 'https://www.okx.com/api/v5/market/candles?instId=STRK-USDT&bar=1D&limit=300'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        if d.get('code') != '0' or not d.get('data'):
            return None
        target_date = target_dt.date()
        for c in d['data']:
            ts = int(c[0]) / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if dt.date() == target_date:
                return float(c[4])
        # fallback: closest at-or-before
        for c in d['data']:
            ts = int(c[0]) / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if dt.date() <= target_date:
                return float(c[4])
    except Exception as e:
        logger.warning(f'OKX fetch: {e}')
    return None


def classify_outcome(pct_change, rally_min, crash_max):
    if pct_change >= rally_min:
        return 'RALLY'
    if pct_change <= crash_max:
        return 'CRASH'
    return 'NEUTRAL'


def close_window(record, window_key, verify_key, issued_price,
                 rally_min, crash_max, now):
    """Try to close one window (72h or 7d). Returns True if closed, else False."""
    if record.get(window_key):
        return False  # already closed
    verify_at = record.get(verify_key)
    if not verify_at:
        return False
    verify_dt = datetime.fromisoformat(verify_at.replace('Z', '+00:00'))
    if verify_dt > now:
        return False
    if not issued_price:
        record[window_key] = {
            'error': 'no issued_price',
            'closed_at': now.isoformat(),
        }
        return True

    verify_price = fetch_d1_close(verify_dt)
    if verify_price is None:
        return False  # can't close yet

    pct_change = (verify_price - issued_price) / issued_price * 100
    signal = classify_outcome(pct_change, rally_min, crash_max)

    record[window_key] = {
        'verify_price': verify_price,
        'pct_change': round(pct_change, 3),
        'signal': signal,
        'closed_at': now.isoformat(),
    }
    return True


def rewrite_jsonl(records, path):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')
    tmp.replace(path)


def main():
    logger.info('=' * 60)
    logger.info('HISTORY POSTMORTEM · closes PENDING all_history records')
    logger.info('=' * 60)

    if not HISTORY_FILE.exists():
        logger.info('all_history.jsonl not found — nothing to close')
        return 0

    config = load_config()
    th = ((config.get('_meta') or {}).get('outcome_signal_thresholds') or {})
    rally_min = th.get('rally_min_pct_change', 3.0)
    crash_max = th.get('crash_max_pct_change', -3.0)
    logger.info(f'Thresholds: RALLY >= {rally_min}%, CRASH <= {crash_max}%')

    records = []
    with open(HISTORY_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    now = datetime.now(timezone.utc)
    changed = 0
    for i, r in enumerate(records):
        if r.get('status') == 'CLOSED':
            continue
        issued_price = r.get('price_usd')
        c72 = close_window(r, 'outcome_72h', 'verify_after_72h', issued_price,
                           rally_min, crash_max, now)
        c7d = close_window(r, 'outcome_7d', 'verify_after_7d', issued_price,
                           rally_min, crash_max, now)
        if c72 or c7d:
            changed += 1
            # Update status
            if r.get('outcome_72h') and r.get('outcome_7d'):
                r['status'] = 'CLOSED'
            else:
                r['status'] = 'PARTIAL'
            logger.info(f'  {r.get("run_id")}: 72h_closed={bool(r.get("outcome_72h"))} '
                        f'7d_closed={bool(r.get("outcome_7d"))} → {r["status"]}')

    total = len(records)
    n_pending = sum(1 for r in records if r.get('status') == 'PENDING')
    n_partial = sum(1 for r in records if r.get('status') == 'PARTIAL')
    n_closed = sum(1 for r in records if r.get('status') == 'CLOSED')

    logger.info(f'\nTotal: {total} | PENDING: {n_pending} | PARTIAL: {n_partial} | CLOSED: {n_closed}')
    logger.info(f'Windows closed this run: {changed}')

    if changed > 0:
        rewrite_jsonl(records, HISTORY_FILE)
        logger.info(f'Rewrote {HISTORY_FILE}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
