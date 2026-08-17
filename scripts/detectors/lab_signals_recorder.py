#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab_signals_recorder.py — записывает issued STRONG_BUY signals для backtest.

ЗАЧЕМ: чтобы через 7 дней verifier мог измерить реальную precision.
       Сейчас `/check LINK` пишет "measuring" — вот источник данных для этого.

ЛОГИКА:
  1. Читает strk_lab_report.json
  2. Для каждого STRONG_BUY token — записывает в lab_signals.jsonl:
     {issued_at, token, sector, issued_price, net_flow_m, verify_after}
  3. Dedup — если тот же token+sector уже записан в PENDING < 24h — skip
  4. Позже lab_signals_verifier.py их закроет

ЗАПУСК: после strk_lab.py каждый LAB run (mode=lab или cron 08:30/20:30).

DATA:
  data/history/lab_signals.jsonl — append-only лог всех issued signals
  data/cache/lab_signals_recorder_state.json — dedup state
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'

LAB_SNAPSHOT = CACHE_DIR / 'strk_lab_report.json'
MOMENTUM = CACHE_DIR / 'dune_sector_momentum.json'
SIGNALS_LOG = HISTORY_DIR / 'lab_signals.jsonl'
STATE_FILE = CACHE_DIR / 'lab_signals_recorder_state.json'

VERIFY_AFTER_DAYS = 7  # через сколько дней проверяем outcome
DEDUP_HOURS = 20       # не записываем тот же token дважды за 20h

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def load_json(path, default=None):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {path.name}: {e}")
    return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def get_price_now(momentum_data, token):
    """Извлекает price_now для token из momentum data."""
    if not momentum_data:
        return None
    for r in momentum_data.get('rows', []):
        if not isinstance(r, dict):
            continue
        if str(r.get('token', '')).upper() == token.upper():
            p = r.get('price_now')
            try:
                if p and float(p) > 0:
                    return float(p)
            except (ValueError, TypeError):
                pass
    return None


def main():
    logger.info("=" * 60)
    logger.info("LAB SIGNALS RECORDER · issued STRONG_BUY tokens")
    logger.info("=" * 60)

    snap = load_json(LAB_SNAPSHOT)
    if not snap:
        logger.warning(f"No LAB snapshot at {LAB_SNAPSHOT} — skip")
        return 0

    momentum = load_json(MOMENTUM)  # для получения price_now
    strong_buys = snap.get('strong_buy', [])
    logger.info(f"STRONG_BUY tokens in snapshot: {len(strong_buys)}")

    if not strong_buys:
        logger.info("No STRONG_BUY tokens — nothing to record")
        return 0

    # Load dedup state
    state = load_json(STATE_FILE, {'last_recorded': {}})
    last_recorded = state.get('last_recorded', {})

    now = datetime.now(timezone.utc)
    verify_after = now + timedelta(days=VERIFY_AFTER_DAYS)
    recorded = 0
    skipped = 0

    for sb in strong_buys:
        if not isinstance(sb, dict):
            continue
        token = sb.get('token')
        sector = sb.get('sector', 'unknown')
        if not token:
            continue

        key = f"{token}:{sector}"

        # Dedup check
        prev_ts = last_recorded.get(key)
        if prev_ts:
            try:
                prev = datetime.fromisoformat(prev_ts)
                age_h = (now - prev).total_seconds() / 3600
                if age_h < DEDUP_HOURS:
                    logger.info(f"  SKIP {token} ({sector}) — recorded {age_h:.1f}h ago (dedup < {DEDUP_HOURS}h)")
                    skipped += 1
                    continue
            except Exception:
                pass

        # Get issued price
        issued_price = get_price_now(momentum, token)
        if not issued_price:
            logger.warning(f"  No price for {token} — record without price (verify may skip)")

        record = {
            'issued_at': now.isoformat(),
            'verify_after': verify_after.isoformat(),
            'token': token,
            'sector': sector,
            'signal': 'STRONG_BUY',
            'issued_price': issued_price,
            'net_flow_m_usd': sb.get('net_flow_m_usd'),
            'price_change_7d_pct_at_issue': sb.get('price_change_7d_pct'),
            'tx_count_at_issue': sb.get('tx_count'),
            'status': 'PENDING',
            # Заполняются verifier'ом:
            'outcome_price': None,
            'outcome_return_pct': None,
            'outcome': None,      # HIT / MISS / NEUTRAL / SKIP_NO_PRICE
            'closed_at': None,
        }
        append_jsonl(SIGNALS_LOG, record)
        last_recorded[key] = now.isoformat()
        logger.info(f"  ✓ RECORDED {token} ({sector}) @ ${issued_price if issued_price else 'unknown'}")
        recorded += 1

    # Cleanup state — keep only entries recorded < 48h ago
    cutoff = (now - timedelta(hours=48)).isoformat()
    last_recorded = {k: v for k, v in last_recorded.items() if v > cutoff}
    state['last_recorded'] = last_recorded
    state['last_run'] = now.isoformat()
    save_json(STATE_FILE, state)

    logger.info(f"Recorded: {recorded} · skipped (dedup): {skipped}")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())