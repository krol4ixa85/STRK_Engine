#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shadow_voter.py — Shadow voting layer (STATUS: HYPOTHESIS)

НЕ влияет на confluence_gate.signal, composite_signal_v2, decision_layer,
scenario_engine или на любое реальное решение.

Только пишет в data/history/shadow_votes.jsonl append-only записи о том,
как проголосовал бы каждый новый voter, ЕСЛИ БЫ он голосовал реально.

Через 15+ closed forecasts (verify_after прошёл, auto_postmortem закрыл)
скрипт calibration_report.py покажет precision каждого voter — и только
после этого можно решать: включать live, менять порог, или отклонить.

Workflow:
  1. Каждый RUN: shadow_voter.py вызывается в конце composite job
     после того как все cache JSON обновлены
  2. Читает config/voter_config.json (пороги)
  3. Читает cache файлы из data/cache/ (уже свежие)
  4. Для каждого voter вычисляет vote: RALLY / CRASH / NEUTRAL / UNKNOWN
  5. Пишет ДВЕ записи в data/history/shadow_votes.jsonl:
     · window_72h · verify_after = issued + 72h
     · window_7d  · verify_after = issued + 7d
     Обе с одинаковым shadow_votes snapshot.
  6. Позднее auto_postmortem закроет каждую запись через fetch OKX price.

Формат записи:
{
  "run_id": "R75",
  "issued_at": "2026-08-06T14:11:00Z",
  "verify_after": "2026-08-09T14:11:00Z",
  "window": "72h",
  "issued_price": 0.0262,
  "current_confluence_signal": "NO_SIGNAL",
  "current_rally_score": 2,
  "current_crash_score": 3,
  "shadow_votes": {
    "liquidity_shift": {
      "vote": "CRASH",
      "value": "LP_REMOVING",
      "threshold_ref": "config.voters.liquidity_shift.crash_values"
    },
    "bridge_activity": {"vote": "UNKNOWN", "value": null},
    "cross_token": {"vote": "NEUTRAL", "value": 2.3, "threshold_ref": "±5.0"},
    "cvd_analysis": {"vote": "CRASH", "value": "BEARISH_LEAN"},
    "effort_result": {"vote": "NEUTRAL", "value": "MIXED"}
  },
  "aggregate_shadow": {
    "rally_votes": 0,
    "crash_votes": 2,
    "neutral_votes": 2,
    "unknown_votes": 1,
    "shadow_signal": "SHADOW_CRASH_WEAK"
  },
  "outcome_price": null,
  "outcome_pct_change": null,
  "outcome_signal": null,
  "status": "PENDING"
}
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
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = SCRIPT_DIR / 'config' / 'voter_config.json'
OUTPUT_FILE = HISTORY_DIR / 'shadow_votes.jsonl'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('shadow')


def load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        logger.error(f'load {path}: {e}')
        return None


def get_nested(d, dotted_path, default=None):
    """Get value by 'a.b.c' path from nested dict."""
    if not d:
        return default
    cur = d
    for k in dotted_path.split('.'):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def classify_categorical(value, voter_cfg):
    """Classify value against rally/crash/neutral value lists."""
    if value is None:
        return 'UNKNOWN'
    if value in voter_cfg.get('rally_values', []):
        return 'RALLY'
    if value in voter_cfg.get('crash_values', []):
        return 'CRASH'
    if value in voter_cfg.get('neutral_values', []):
        return 'NEUTRAL'
    return 'UNKNOWN'


def classify_numeric(value, voter_cfg):
    """Classify numeric value against thresholds."""
    if value is None or not isinstance(value, (int, float)):
        return 'UNKNOWN'
    if value >= voter_cfg.get('rally_threshold_gte', float('inf')):
        return 'RALLY'
    if value <= voter_cfg.get('crash_threshold_lte', float('-inf')):
        return 'CRASH'
    return 'NEUTRAL'


def compute_voter(name, voter_cfg):
    """Load source JSON, extract value, classify vote."""
    src = SCRIPT_DIR / voter_cfg['source_file']
    data = load_json(src)
    if not data:
        return {'vote': 'UNKNOWN', 'value': None, 'reason': 'source file missing'}

    value = get_nested(data, voter_cfg['read_path'])
    if value is None:
        return {'vote': 'UNKNOWN', 'value': None, 'reason': f"path {voter_cfg['read_path']} not found"}

    if voter_cfg.get('type') == 'numeric':
        vote = classify_numeric(value, voter_cfg)
    else:
        vote = classify_categorical(value, voter_cfg)

    return {
        'vote': vote,
        'value': value,
        'source_path': voter_cfg['read_path'],
    }


def get_issued_price():
    """Fetch current STRK-USDT spot price from OKX."""
    import urllib.request
    try:
        url = 'https://www.okx.com/api/v5/market/ticker?instId=STRK-USDT'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        if d.get('code') == '0' and d.get('data'):
            return float(d['data'][0]['last'])
    except Exception as e:
        logger.warning(f'OKX price fetch failed: {e}')
    return None


def get_current_confluence():
    """Get current confluence_gate signal + scores (for context, NOT for reading vote)."""
    conf = load_json(CACHE_DIR / 'confluence_gate.json') or {}
    return {
        'signal': conf.get('signal', 'NO_DATA'),
        'confidence': conf.get('confidence', 'LOW'),
        'rally_score': conf.get('rally_score', 0),
        'crash_score': conf.get('crash_score', 0),
    }


def aggregate_shadow_votes(votes):
    """Count rally/crash/neutral/unknown across voters, produce shadow_signal."""
    counts = {'RALLY': 0, 'CRASH': 0, 'NEUTRAL': 0, 'UNKNOWN': 0}
    for v in votes.values():
        vote = v.get('vote', 'UNKNOWN')
        counts[vote] = counts.get(vote, 0) + 1

    total_directional = counts['RALLY'] + counts['CRASH']
    if total_directional == 0:
        signal = 'SHADOW_NEUTRAL'
    elif counts['RALLY'] >= 3 and counts['CRASH'] <= 1:
        signal = 'SHADOW_RALLY_STRONG'
    elif counts['CRASH'] >= 3 and counts['RALLY'] <= 1:
        signal = 'SHADOW_CRASH_STRONG'
    elif counts['RALLY'] > counts['CRASH']:
        signal = 'SHADOW_RALLY_WEAK'
    elif counts['CRASH'] > counts['RALLY']:
        signal = 'SHADOW_CRASH_WEAK'
    else:
        signal = 'SHADOW_MIXED'

    return {
        'rally_votes': counts['RALLY'],
        'crash_votes': counts['CRASH'],
        'neutral_votes': counts['NEUTRAL'],
        'unknown_votes': counts['UNKNOWN'],
        'shadow_signal': signal,
    }


def build_run_id(now):
    """Try to infer run_id from environment / composite_signal_v2 / fallback to timestamp."""
    # Prefer explicit env var if workflow sets it
    rid = os.environ.get('STRK_RUN_ID')
    if rid:
        return rid
    # Fallback: read composite version_id if present
    comp = load_json(CACHE_DIR / 'composite_signal_v2.json') or {}
    ver = comp.get('as_of', '') or now.isoformat()
    # Compact: R + YYYYMMDD + HHMM
    return 'shadow_' + now.strftime('%Y%m%d_%H%M')


def append_jsonl(record):
    """Append one line JSON to OUTPUT_FILE."""
    with open(OUTPUT_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def main():
    logger.info('=' * 60)
    logger.info('SHADOW VOTER · STATUS: HYPOTHESIS (does NOT affect decisions)')
    logger.info('=' * 60)

    config = load_json(CONFIG_FILE)
    if not config:
        logger.error(f'voter_config.json не найден: {CONFIG_FILE}')
        return 1

    voters_cfg = config.get('voters', {})
    if not voters_cfg:
        logger.error('No voters in config')
        return 1

    # Compute vote per voter
    votes = {}
    for name, cfg in voters_cfg.items():
        result = compute_voter(name, cfg)
        votes[name] = result
        logger.info(f'  {name:<20} vote={result["vote"]:<8} value={result.get("value")}')

    # Aggregate
    agg = aggregate_shadow_votes(votes)
    logger.info(f'\nAggregate shadow: {agg["shadow_signal"]} '
                f'(R={agg["rally_votes"]} C={agg["crash_votes"]} '
                f'N={agg["neutral_votes"]} U={agg["unknown_votes"]})')

    # Current confluence for comparison
    conf = get_current_confluence()
    logger.info(f'\nCurrent real confluence signal: {conf["signal"]} '
                f'(rally={conf["rally_score"]} crash={conf["crash_score"]})')

    # Issued price
    price = get_issued_price()
    logger.info(f'Issued price (OKX STRK-USDT): ${price}' if price else 'Issued price: unavailable')

    now = datetime.now(timezone.utc)
    run_id = build_run_id(now)

    # Write TWO records — 72h window and 7d window
    written = []
    for window_hours, window_label in [(72, '72h'), (168, '7d')]:
        record = {
            'run_id': run_id,
            'issued_at': now.isoformat(),
            'verify_after': (now + timedelta(hours=window_hours)).isoformat(),
            'window': window_label,
            'issued_price': price,
            'current_confluence_signal': conf['signal'],
            'current_confluence_confidence': conf['confidence'],
            'current_rally_score': conf['rally_score'],
            'current_crash_score': conf['crash_score'],
            'shadow_votes': votes,
            'aggregate_shadow': agg,
            'outcome_price': None,
            'outcome_pct_change': None,
            'outcome_signal': None,
            'per_voter_outcome': None,
            'status': 'PENDING',
            'config_version': (config.get('_meta') or {}).get('version', 'unknown'),
        }
        append_jsonl(record)
        written.append(window_label)

    logger.info(f'\nAppended 2 shadow forecasts ({", ".join(written)}) to {OUTPUT_FILE}')

    # Print total record count
    try:
        total = sum(1 for _ in open(OUTPUT_FILE, encoding='utf-8'))
        logger.info(f'Total shadow_votes.jsonl records: {total}')
    except Exception:
        pass

    return 0


if __name__ == '__main__':
    sys.exit(main())
