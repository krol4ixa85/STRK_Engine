#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab_signals_verifier.py — закрывает PENDING signals + строит precision summary.

ЛОГИКА:
  1. Читает все PENDING records из lab_signals.jsonl
  2. Для каждого у которого verify_after ≤ now:
     - Получает current price для token (из momentum snapshot)
     - Считает return: (curr - issued) / issued * 100
     - Помечает:
        HIT     — return > +3%
        MISS    — return < -3%
        NEUTRAL — между -3% и +3%
        SKIP    — нет price (не считаем в статистику)
  3. Переписывает lab_signals.jsonl с обновлёнными статусами
  4. Строит lab_signals_summary.json:
     {overall: {n_closed, hits, misses, neutrals, precision_pct},
      per_token: {LINK: {...}, MORPHO: {...}},
      per_sector: {RWA: {...}, DeFi: {...}}}

ЗАПУСК: после recorder каждый LAB run.

VERIFICATION THRESHOLDS:
  HIT_THRESHOLD_PCT = +3%  (7-day price up)
  MISS_THRESHOLD_PCT = -3%
"""
import os
import sys
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
REPORTS_DIR = SCRIPT_DIR / 'data' / 'reports'

MOMENTUM = CACHE_DIR / 'dune_sector_momentum.json'
SIGNALS_LOG = HISTORY_DIR / 'lab_signals.jsonl'
SUMMARY = CACHE_DIR / 'lab_signals_summary.json'  # cache, чтобы Worker читал
FULL_SUMMARY = REPORTS_DIR / 'lab_signals_summary_full.json'

HIT_THRESHOLD_PCT = 3.0
MIN_N_FOR_PRECISION = 5   # показываем cifры только с N ≥ 5
MIN_N_HIGH_CONFIDENCE = 15  # для "надёжной" метки

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


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


def read_jsonl(path):
    if not path.exists():
        return []
    records = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"Read failed: {e}")
    return records


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')


def get_price_now(momentum_data, token):
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


def compute_summary(all_records):
    """Builds summary stats: overall + per_token + per_sector."""
    closed = [r for r in all_records if r.get('status') == 'CLOSED']

    overall = defaultdict(int)
    per_token = defaultdict(lambda: {'n': 0, 'hits': 0, 'misses': 0, 'neutrals': 0, 'skips': 0, 'total_return_pct': 0.0})
    per_sector = defaultdict(lambda: {'n': 0, 'hits': 0, 'misses': 0, 'neutrals': 0, 'skips': 0, 'total_return_pct': 0.0})

    for r in closed:
        outcome = r.get('outcome')
        token = r.get('token', 'UNKNOWN')
        sector = r.get('sector', 'unknown')
        ret = r.get('outcome_return_pct') or 0.0

        # Overall
        overall['n_closed'] += 1
        if outcome == 'HIT':
            overall['hits'] += 1
        elif outcome == 'MISS':
            overall['misses'] += 1
        elif outcome == 'NEUTRAL':
            overall['neutrals'] += 1
        elif outcome == 'SKIP_NO_PRICE':
            overall['skips'] += 1

        # Per token
        pt = per_token[token]
        pt['n'] += 1
        if outcome == 'HIT': pt['hits'] += 1
        elif outcome == 'MISS': pt['misses'] += 1
        elif outcome == 'NEUTRAL': pt['neutrals'] += 1
        elif outcome == 'SKIP_NO_PRICE': pt['skips'] += 1
        if outcome != 'SKIP_NO_PRICE':
            pt['total_return_pct'] += ret

        # Per sector
        ps = per_sector[sector]
        ps['n'] += 1
        if outcome == 'HIT': ps['hits'] += 1
        elif outcome == 'MISS': ps['misses'] += 1
        elif outcome == 'NEUTRAL': ps['neutrals'] += 1
        elif outcome == 'SKIP_NO_PRICE': ps['skips'] += 1
        if outcome != 'SKIP_NO_PRICE':
            ps['total_return_pct'] += ret

    # Precision only for records with actual outcome (not SKIP)
    def precision(d):
        actionable = d['hits'] + d['misses'] + d['neutrals']  # исключаем SKIP
        if actionable == 0:
            return None
        return round(d['hits'] / actionable * 100, 1)

    def avg_return(d):
        actionable = d['hits'] + d['misses'] + d['neutrals']
        if actionable == 0:
            return None
        return round(d['total_return_pct'] / actionable, 2)

    total_actionable = overall['hits'] + overall['misses'] + overall['neutrals']

    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'overall': {
            'n_closed': overall['n_closed'],
            'n_actionable': total_actionable,
            'hits': overall['hits'],
            'misses': overall['misses'],
            'neutrals': overall['neutrals'],
            'skips_no_price': overall['skips'],
            'precision_pct': round(overall['hits'] / total_actionable * 100, 1) if total_actionable else None,
            'has_enough_data': total_actionable >= MIN_N_FOR_PRECISION,
            'high_confidence': total_actionable >= MIN_N_HIGH_CONFIDENCE,
        },
        'per_token': {},
        'per_sector': {},
        'note': (
            f'Precision measured on {HIT_THRESHOLD_PCT:+.0f}% / -{HIT_THRESHOLD_PCT:.0f}% threshold after 7d. '
            f'N < {MIN_N_FOR_PRECISION} = hidden. '
            f'N < {MIN_N_HIGH_CONFIDENCE} = wide CI, treat cautiously.'
        ),
    }

    for token, d in per_token.items():
        actionable = d['hits'] + d['misses'] + d['neutrals']
        result['per_token'][token] = {
            'n_total': d['n'],
            'n_actionable': actionable,
            'hits': d['hits'],
            'misses': d['misses'],
            'neutrals': d['neutrals'],
            'skips': d['skips'],
            'precision_pct': precision(d),
            'avg_return_pct': avg_return(d),
            'has_enough_data': actionable >= MIN_N_FOR_PRECISION,
        }

    for sector, d in per_sector.items():
        actionable = d['hits'] + d['misses'] + d['neutrals']
        result['per_sector'][sector] = {
            'n_total': d['n'],
            'n_actionable': actionable,
            'hits': d['hits'],
            'misses': d['misses'],
            'neutrals': d['neutrals'],
            'skips': d['skips'],
            'precision_pct': precision(d),
            'avg_return_pct': avg_return(d),
            'has_enough_data': actionable >= MIN_N_FOR_PRECISION,
        }

    return result


def main():
    logger.info("=" * 60)
    logger.info("LAB SIGNALS VERIFIER · close pending, build summary")
    logger.info("=" * 60)

    all_records = read_jsonl(SIGNALS_LOG)
    logger.info(f"Total records in log: {len(all_records)}")

    pending = [r for r in all_records if r.get('status') == 'PENDING']
    logger.info(f"PENDING records: {len(pending)}")

    now = datetime.now(timezone.utc)
    momentum = load_json(MOMENTUM)

    closed_now = 0
    changed = False

    for r in all_records:
        if r.get('status') != 'PENDING':
            continue
        # Check if verify_after has passed
        verify_after_str = r.get('verify_after')
        if not verify_after_str:
            continue
        try:
            verify_after = datetime.fromisoformat(verify_after_str)
        except Exception:
            continue

        if verify_after > now:
            continue  # too early

        # Ready to close
        token = r.get('token', '')
        issued_price = r.get('issued_price')
        current_price = get_price_now(momentum, token)

        if not current_price or not issued_price:
            r['status'] = 'CLOSED'
            r['outcome'] = 'SKIP_NO_PRICE'
            r['outcome_price'] = current_price
            r['closed_at'] = now.isoformat()
            logger.info(f"  {token}: SKIP (missing price · issued={issued_price} curr={current_price})")
            closed_now += 1
            changed = True
            continue

        try:
            return_pct = (float(current_price) - float(issued_price)) / float(issued_price) * 100
        except Exception:
            r['status'] = 'CLOSED'
            r['outcome'] = 'SKIP_NO_PRICE'
            r['closed_at'] = now.isoformat()
            closed_now += 1
            changed = True
            continue

        if return_pct >= HIT_THRESHOLD_PCT:
            outcome = 'HIT'
            emoji = '✓'
        elif return_pct <= -HIT_THRESHOLD_PCT:
            outcome = 'MISS'
            emoji = '✗'
        else:
            outcome = 'NEUTRAL'
            emoji = '~'

        r['status'] = 'CLOSED'
        r['outcome'] = outcome
        r['outcome_price'] = float(current_price)
        r['outcome_return_pct'] = round(return_pct, 2)
        r['closed_at'] = now.isoformat()

        logger.info(f"  {token}: {emoji} {outcome} · {return_pct:+.2f}% (issued=${issued_price:.4f} → curr=${current_price:.4f})")
        closed_now += 1
        changed = True

    if changed:
        write_jsonl(SIGNALS_LOG, all_records)
        logger.info(f"Rewrote {SIGNALS_LOG.name} with {closed_now} newly closed records")

    # Build summary
    summary = compute_summary(all_records)
    save_json(SUMMARY, summary)
    save_json(FULL_SUMMARY, summary)

    overall = summary['overall']
    logger.info(f"\n--- SUMMARY ---")
    logger.info(f"  N closed: {overall['n_closed']} (actionable: {overall['n_actionable']})")
    logger.info(f"  Hits: {overall['hits']} · Misses: {overall['misses']} · Neutrals: {overall['neutrals']}")
    if overall['precision_pct'] is not None:
        confidence = 'HIGH' if overall['high_confidence'] else 'LOW (wide CI)' if overall['has_enough_data'] else 'INSUFFICIENT'
        logger.info(f"  Precision: {overall['precision_pct']}% (confidence: {confidence})")
    else:
        logger.info(f"  Precision: not enough data (N < {MIN_N_FOR_PRECISION})")

    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())