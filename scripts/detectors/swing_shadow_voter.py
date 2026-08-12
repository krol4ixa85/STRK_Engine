#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swing_shadow_voter.py — SHADOW VOTER для monthly Dune signal.

ЗАЧЕМ: measurement precision monthly signal через 30-60 дней.
       НЕ влияет на Confluence Gate DECISION.

ПОЧЕМУ ЧЕРЕЗ SHADOW: baseline analysis показал что hardcoded voting
на основе 1-month backtest часто fit'ится под режим рынка.
Shadow voter даёт reality check ДО hard wire-in.

ЛОГИКА:
  Читает dune_starknet_monthly.json (query 8286927).
  Извлекает phase_signal + streak + trend.
  Голосует: RALLY / CRASH / NEUTRAL.
  Записывает в data/history/shadow_votes.jsonl.

VOTING RULES:
  BEARISH_BREAKDOWN streak ≥ 3 дней + trend < -30% → vote CRASH
  BULLISH_MOMENTUM streak ≥ 3 дней + trend > +10% → vote RALLY
  Всё остальное → vote NEUTRAL

ЧТО ПОТОМ:
  Через 30-60 дней shadow_postmortem.py закроет 15+ прогнозов.
  weekly_backtest покажет precision monthly voter'а.
  Wire-in В CONFLUENCE ТОЛЬКО если precision >= 55%.
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'

MONTHLY_CACHE = CACHE_DIR / 'dune_starknet_monthly.json'
SHADOW_VOTES = HISTORY_DIR / 'shadow_votes.jsonl'
STATE_FILE = CACHE_DIR / 'swing_voter_state.json'

# Voting thresholds
BEAR_STREAK_MIN = 3    # 3+ дней подряд bearish
BEAR_TREND_MAX = -30.0  # trend хуже -30%
BULL_STREAK_MIN = 3
BULL_TREND_MIN = 10.0
DEDUP_HOURS = 4  # не более 1 vote за 4 часа


def _get(row, name, default=None):
    """Get cell — works with dict rows."""
    if isinstance(row, dict):
        val = row.get(name)
        return val if val is not None else default
    return default


def _monthly_signal(row):
    """Get signal (supports both v1 and v2 SQL versions)."""
    return _get(row, 'phase_signal') or _get(row, 'signal') or 'UNKNOWN'


def _monthly_trend(row):
    """Get trend % — supports w_m_pct (v2) or pct_from_30d_max (v1)."""
    v = _get(row, 'w_m_pct') or _get(row, 'pct_from_30d_max')
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _get_current_price():
    """Read current STRK price from composite/technical cache."""
    for fname in ('composite_signal_v2.json', 'technical_momentum.json', 'wyckoff_phase.json'):
        p = CACHE_DIR / fname
        if not p.exists():
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for key in ('price', 'current_price', 'price_usd'):
                v = data.get(key)
                if isinstance(v, (int, float)) and 0.001 < v < 100:
                    return float(v)
            inputs = data.get('inputs') or {}
            if 'strk_context' in inputs:
                v = inputs['strk_context'].get('price')
                if v and 0.001 < v < 100:
                    return float(v)
        except Exception:
            continue
    return None


def load_state():
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {'last_vote_ts': None, 'total_votes': 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)


def is_within_dedup(state):
    last_ts = state.get('last_vote_ts')
    if not last_ts:
        return False
    try:
        last = datetime.fromisoformat(last_ts)
        age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        return age_h < DEDUP_HOURS
    except Exception:
        return False


def compute_vote(rows):
    """Analyze monthly rows → return (vote, confidence, reasoning)."""
    if not rows:
        return 'NEUTRAL', 'LOW', 'No data'

    latest = rows[0]
    signal = _monthly_signal(latest)
    trend = _monthly_trend(latest)

    # Compute current streak
    streak = 1
    for i in range(1, min(len(rows), 30)):
        if _monthly_signal(rows[i]) == signal:
            streak += 1
        else:
            break

    # Total bearish days in 30
    bearish_30d = sum(1 for r in rows[:30] if _monthly_signal(r) == 'BEARISH_BREAKDOWN')

    # === VOTING RULES ===
    if signal == 'BEARISH_BREAKDOWN' and streak >= BEAR_STREAK_MIN and trend <= BEAR_TREND_MAX:
        return ('CRASH', 'MEDIUM',
                f'{streak}d bearish streak · trend {trend:+.0f}% · {bearish_30d}/30d bearish')
    elif signal == 'BULLISH_MOMENTUM' and streak >= BULL_STREAK_MIN and trend >= BULL_TREND_MIN:
        return ('RALLY', 'MEDIUM',
                f'{streak}d bullish streak · trend {trend:+.0f}%')
    elif signal == 'BEARISH_BREAKDOWN' and streak >= 2:
        return ('CRASH', 'LOW', f'{streak}d bearish (weak signal)')
    elif signal == 'BULLISH_MOMENTUM' and streak >= 2:
        return ('RALLY', 'LOW', f'{streak}d bullish (weak signal)')
    else:
        return ('NEUTRAL', 'LOW', f'{signal} · streak {streak}')


def main():
    logger.info("=" * 60)
    logger.info("SWING SHADOW VOTER · Monthly Dune Signal")
    logger.info("=" * 60)

    if not MONTHLY_CACHE.exists():
        logger.warning(f"No monthly data at {MONTHLY_CACHE} — skip")
        return 0

    try:
        with open(MONTHLY_CACHE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        rows = data.get('rows') or []
    except Exception as e:
        logger.error(f"Failed to load monthly cache: {e}")
        return 0

    if not rows:
        logger.warning("Empty rows — skip")
        return 0

    logger.info(f"Loaded {len(rows)} monthly rows")

    state = load_state()
    if is_within_dedup(state):
        logger.info(f"Dedup — last vote < {DEDUP_HOURS}h ago, skip")
        return 0

    vote, confidence, reasoning = compute_vote(rows)
    logger.info(f"Vote: {vote} · confidence {confidence}")
    logger.info(f"Reasoning: {reasoning}")

    # Get current price for post-mortem verification
    price_now = _get_current_price()
    if price_now is None:
        logger.warning("No current price available — using 0")
        price_now = 0.0

    # === Append to shadow_votes.jsonl ===
    now = datetime.now(timezone.utc)
    run_id = now.strftime('%Y%m%dT%H%M%S')

    # Two records: 72h window + 7d window (для measurement)
    for window_name, hours in [('72h', 72), ('7d', 168)]:
        record = {
            'run_id': f'shadow_swing_{run_id}',
            'issued_at': now.isoformat(),
            'verify_after': (now + __import__('datetime').timedelta(hours=hours)).isoformat(),
            'window': window_name,
            'issued_price': price_now,
            'current_confluence_signal': None,  # NOT influencing confluence
            'shadow_votes': {
                'dune_monthly_swing': {
                    'vote': vote,
                    'confidence': confidence,
                    'reasoning': reasoning,
                    'source': 'query_8286927',
                },
            },
            'aggregate_shadow': {
                'shadow_signal': f'SHADOW_{vote}',
                'rally_votes': 1 if vote == 'RALLY' else 0,
                'crash_votes': 1 if vote == 'CRASH' else 0,
                'neutral_votes': 1 if vote == 'NEUTRAL' else 0,
            },
            'status': 'PENDING',
            'outcome_price': None,
            'outcome_signal': None,
            'per_voter_outcome': {},
        }

        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(SHADOW_VOTES, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    logger.info(f"✓ Wrote 2 shadow vote records (72h + 7d windows)")

    state['last_vote_ts'] = now.isoformat()
    state['total_votes'] = state.get('total_votes', 0) + 1
    save_state(state)

    logger.info(f"Total shadow votes: {state['total_votes']}")
    logger.info("=" * 60)
    logger.info("NOTE: Vote в SHADOW only. Confluence Gate NOT affected.")
    logger.info("shadow_postmortem.py измерит precision через 30-60 дней.")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())