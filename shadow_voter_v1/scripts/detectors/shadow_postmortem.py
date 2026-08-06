#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shadow_postmortem.py — Закрывает PENDING shadow forecasts

Отдельный модуль (не патчит существующий auto_postmortem.py), потому что
shadow_votes.jsonl имеет свою схему. Логика простая:

Для каждой PENDING записи в data/history/shadow_votes.jsonl:
  · Если verify_after > now — оставить PENDING
  · Иначе:
     1. Получить D1 close на дату verify_after через OKX (STRK-USDT)
     2. Вычислить outcome_pct_change = (verify_price - issued_price) / issued_price * 100
     3. Классифицировать outcome_signal:
         · RALLY   если change > +rally_min_pct_change (default +3%)
         · CRASH   если change < crash_max_pct_change  (default -3%)
         · NEUTRAL иначе
     4. Для каждого voter в shadow_votes:
         · HIT   если voter.vote == outcome_signal
         · MISS  если voter.vote != NEUTRAL, != UNKNOWN и != outcome_signal
         · SKIP  если voter.vote == NEUTRAL или UNKNOWN (не голосовал)
     5. Записать closed record обратно в тот же файл (rewrite jsonl)

ВАЖНО:
· Идемпотентно — уже CLOSED записи не трогает
· Не редактирует shadow_votes.jsonl задним числом (только PENDING → CLOSED)
· При отсутствии D1 цены (OKX down, дата в будущем) — оставляет PENDING
· Пишет только outcome + per_voter_outcome, всё остальное read-only
"""
import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
CONFIG_FILE = SCRIPT_DIR / 'config' / 'voter_config.json'
SHADOW_FILE = HISTORY_DIR / 'shadow_votes.jsonl'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('shadow_pm')


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def fetch_d1_close(target_dt):
    """Fetch OKX D1 close for STRK-USDT covering the day of target_dt (UTC).

    Returns (close_price, actual_date) or (None, None).
    Uses /candles endpoint (recent 300 days).
    """
    try:
        url = 'https://www.okx.com/api/v5/market/candles?instId=STRK-USDT&bar=1D&limit=300'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        if d.get('code') != '0' or not d.get('data'):
            return (None, None)

        target_date = target_dt.date()
        # OKX candles: [ts_ms, o, h, l, c, ...]
        for c in d['data']:
            ts = int(c[0]) / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if dt.date() == target_date:
                return (float(c[4]), dt.strftime('%Y-%m-%d'))

        # If exact date not found, fall back to closest date at or before target
        for c in d['data']:
            ts = int(c[0]) / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if dt.date() <= target_date:
                return (float(c[4]), dt.strftime('%Y-%m-%d'))

        return (None, None)
    except Exception as e:
        logger.warning(f'OKX fetch error: {e}')
        return (None, None)


def classify_outcome(pct_change, rally_min, crash_max):
    """RALLY / CRASH / NEUTRAL based on % change from issued to verify."""
    if pct_change is None:
        return None
    if pct_change >= rally_min:
        return 'RALLY'
    if pct_change <= crash_max:
        return 'CRASH'
    return 'NEUTRAL'


def evaluate_voter_outcome(vote, outcome_signal):
    """HIT / MISS / SKIP per voter."""
    if vote in ('UNKNOWN',):
        return 'SKIP_UNKNOWN'
    if vote == 'NEUTRAL':
        # Voter did not take a directional position
        if outcome_signal == 'NEUTRAL':
            return 'HIT_NEUTRAL'
        return 'SKIP_NEUTRAL'
    # vote is RALLY or CRASH
    if vote == outcome_signal:
        return 'HIT'
    if outcome_signal == 'NEUTRAL':
        return 'MISS_NO_MOVE'
    return 'MISS'


def close_record(record, rally_min, crash_max):
    """Close one PENDING record. Returns updated record (or same if can't close)."""
    verify_after_iso = record.get('verify_after')
    if not verify_after_iso:
        return record

    verify_dt = datetime.fromisoformat(verify_after_iso.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)

    if verify_dt > now:
        return record  # still pending, verify_after not reached

    issued_price = record.get('issued_price')
    if not issued_price or issued_price <= 0:
        record['status'] = 'CLOSED_NO_ISSUED_PRICE'
        record['evaluated_at'] = now.isoformat()
        return record

    # Fetch D1 close on verify_after date
    verify_price, actual_date = fetch_d1_close(verify_dt)
    if verify_price is None:
        # Cannot close — leave PENDING (will retry next run)
        return record

    pct_change = (verify_price - issued_price) / issued_price * 100
    outcome_signal = classify_outcome(pct_change, rally_min, crash_max)

    # Per-voter HIT/MISS
    per_voter = {}
    for name, vote_info in (record.get('shadow_votes') or {}).items():
        vote = (vote_info or {}).get('vote', 'UNKNOWN')
        per_voter[name] = evaluate_voter_outcome(vote, outcome_signal)

    # Also evaluate aggregate shadow_signal vs outcome
    agg = record.get('aggregate_shadow') or {}
    agg_signal = agg.get('shadow_signal', 'SHADOW_NEUTRAL')
    if 'RALLY' in agg_signal:
        agg_vote = 'RALLY'
    elif 'CRASH' in agg_signal:
        agg_vote = 'CRASH'
    else:
        agg_vote = 'NEUTRAL'
    agg_outcome = evaluate_voter_outcome(agg_vote, outcome_signal)

    record['outcome_price'] = verify_price
    record['outcome_date_used'] = actual_date
    record['outcome_pct_change'] = round(pct_change, 3)
    record['outcome_signal'] = outcome_signal
    record['per_voter_outcome'] = per_voter
    record['aggregate_outcome'] = agg_outcome
    record['status'] = 'CLOSED'
    record['evaluated_at'] = now.isoformat()

    return record


def rewrite_jsonl(records, path):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    tmp.replace(path)


def main():
    logger.info('=' * 60)
    logger.info('SHADOW POSTMORTEM · closes PENDING shadow forecasts')
    logger.info('=' * 60)

    if not SHADOW_FILE.exists():
        logger.info('shadow_votes.jsonl not found yet — nothing to close')
        return 0

    config = load_config()
    thresholds = ((config.get('_meta') or {}).get('outcome_signal_thresholds') or {})
    rally_min = thresholds.get('rally_min_pct_change', 3.0)
    crash_max = thresholds.get('crash_max_pct_change', -3.0)
    logger.info(f'Outcome thresholds: RALLY >= {rally_min}%, CRASH <= {crash_max}% (else NEUTRAL)')

    # Read all records
    records = []
    with open(SHADOW_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f'skipping malformed line: {line[:80]}')

    n_before = len(records)
    n_pending_before = sum(1 for r in records if r.get('status') == 'PENDING')

    # Close PENDING records
    closed_this_run = 0
    for i, r in enumerate(records):
        if r.get('status') != 'PENDING':
            continue
        updated = close_record(r, rally_min, crash_max)
        if updated.get('status') == 'CLOSED':
            records[i] = updated
            closed_this_run += 1
            logger.info(
                f'  CLOSED {r.get("run_id")}·{r.get("window")}: '
                f'{updated["outcome_signal"]} ({updated["outcome_pct_change"]:+.2f}%) · '
                f'aggregate={updated["aggregate_outcome"]}'
            )

    n_pending_after = sum(1 for r in records if r.get('status') == 'PENDING')

    logger.info(f'\nTotal records: {n_before}')
    logger.info(f'PENDING before → after: {n_pending_before} → {n_pending_after}')
    logger.info(f'Closed this run: {closed_this_run}')

    if closed_this_run > 0:
        rewrite_jsonl(records, SHADOW_FILE)
        logger.info(f'Rewrote {SHADOW_FILE} in place')

    return 0


if __name__ == '__main__':
    sys.exit(main())
