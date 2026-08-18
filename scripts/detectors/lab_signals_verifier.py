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
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
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


def compute_precision_pct(signals_list):
    """Обычная precision: HIT / (HIT + MISS). NEUTRAL и SKIP игнорируем."""
    actionable = [s for s in signals_list if s.get('outcome') in ('HIT', 'MISS')]
    if len(actionable) < 5:
        return None
    hits = sum(1 for s in actionable if s['outcome'] == 'HIT')
    return round(100.0 * hits / len(actionable), 1)


def purged_walk_forward_precision(closed_signals, n_folds=4, embargo_days=5,
                                   min_oos_per_fold=8, min_actionable_needed=25):
    """
    Упрощённый purged walk-forward для event-based signals.

    Логика:
    - Сортируем closed signals по issued_at
    - Разбиваем на n_folds по времени
    - Для каждого fold считаем precision ТОЛЬКО на out-of-sample (более поздние) сигналы
    - Embargo: сигналы близкие к границе train/test пропускаются (data leakage protection)

    Returns:
        Dict с overall_precision, oos_precision_mean, oos_precision_std, fold_results, enough_data.

    Note: включается автоматически только при n_actionable ≥ min_actionable_needed (default 25).
    При меньшем N возвращает enough_data=False с reason.
    """
    if not closed_signals:
        return {'enough_data': False, 'reason': 'no closed signals'}

    # Сортируем по времени выпуска
    signals = sorted(
        [s for s in closed_signals if s.get('issued_at')],
        key=lambda x: x['issued_at']
    )

    actionable = [s for s in signals if s.get('outcome') in ('HIT', 'MISS')]
    n_actionable = len(actionable)
    overall = compute_precision_pct(signals)

    if n_actionable < min_actionable_needed:
        return {
            'overall_precision_pct': overall,
            'oos_precision_pct_mean': None,
            'oos_precision_pct_std': None,
            'fold_results': [],
            'n_closed': len(signals),
            'n_actionable': n_actionable,
            'enough_data': False,
            'reason': f'need >= {min_actionable_needed} actionable, have {n_actionable}',
            'method': 'purged_walk_forward',
            'embargo_days': embargo_days,
        }

    fold_size = len(signals) // n_folds
    fold_results = []

    for i in range(1, n_folds):  # начинаем со 2-го fold (первый = train buffer)
        split_idx = i * fold_size
        if split_idx >= len(signals):
            break

        split_ts = signals[split_idx].get('issued_at')
        try:
            split_time = datetime.fromisoformat(str(split_ts).replace('Z', '+00:00'))
        except Exception:
            continue

        # Embargo: сигналы близкие к split_time (за embargo_days ДО) — исключаются
        embargo_end = split_time + timedelta(days=embargo_days)

        # OOS = сигналы выпущенные ПОСЛЕ split_time + embargo (защита от serial correlation)
        oos = []
        for s in signals[split_idx:]:
            issued_str = s.get('issued_at', '')
            try:
                issued = datetime.fromisoformat(str(issued_str).replace('Z', '+00:00'))
            except Exception:
                continue
            if issued >= embargo_end:
                oos.append(s)

        oos_actionable = [x for x in oos if x.get('outcome') in ('HIT', 'MISS')]
        if len(oos_actionable) < min_oos_per_fold:
            continue

        prec = compute_precision_pct(oos)
        if prec is not None:
            fold_results.append({
                'fold': i,
                'oos_n_total': len(oos),
                'oos_n_actionable': len(oos_actionable),
                'precision_pct': prec,
                'split_time': split_time.isoformat(),
            })

    if not fold_results:
        return {
            'overall_precision_pct': overall,
            'oos_precision_pct_mean': None,
            'oos_precision_pct_std': None,
            'fold_results': [],
            'n_closed': len(signals),
            'n_actionable': n_actionable,
            'enough_data': False,
            'reason': f'not enough OOS samples per fold (need >= {min_oos_per_fold})',
            'method': 'purged_walk_forward',
            'embargo_days': embargo_days,
        }

    oos_precs = [f['precision_pct'] for f in fold_results]
    mean_oos = round(statistics.mean(oos_precs), 1)
    std_oos = round(statistics.stdev(oos_precs), 1) if len(oos_precs) > 1 else 0.0

    # Data leakage warning: если разница overall vs OOS > 10%, значит есть leakage
    leakage_warning = None
    if overall is not None and mean_oos is not None:
        diff = abs(overall - mean_oos)
        if diff > 10:
            leakage_warning = (
                f'WARNING: overall {overall}% vs OOS {mean_oos}% differ by {diff:.1f}%. '
                f'Suggests data leakage or overfitting. Trust OOS number more.'
            )

    return {
        'overall_precision_pct': overall,
        'oos_precision_pct_mean': mean_oos,
        'oos_precision_pct_std': std_oos,
        'fold_results': fold_results,
        'n_closed': len(signals),
        'n_actionable': n_actionable,
        'enough_data': True,
        'method': 'purged_walk_forward',
        'embargo_days': embargo_days,
        'n_folds_used': len(fold_results),
        'leakage_warning': leakage_warning,
    }


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

    # Purged walk-forward validation (автоматически "выключается" до N >= 25)
    closed_list = [r for r in all_records if r.get('status') == 'CLOSED']
    purged_result = purged_walk_forward_precision(closed_list)

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
        # Purged walk-forward — более честная OOS оценка. Активна с N_actionable >= 25.
        # Используется для решения о wire-in signal в Confluence Gate.
        'purged_walk_forward': {
            'enough_data': purged_result.get('enough_data', False),
            'oos_precision_pct_mean': purged_result.get('oos_precision_pct_mean'),
            'oos_precision_pct_std': purged_result.get('oos_precision_pct_std'),
            'n_folds_used': purged_result.get('n_folds_used', 0),
            'embargo_days': purged_result.get('embargo_days', 5),
            'reason_if_not_ready': purged_result.get('reason'),
            'leakage_warning': purged_result.get('leakage_warning'),
            'fold_results': purged_result.get('fold_results', []),
        },
        'per_token': {},
        'per_sector': {},
        'note': (
            f'Precision measured on {HIT_THRESHOLD_PCT:+.0f}% / -{HIT_THRESHOLD_PCT:.0f}% threshold after 7d. '
            f'N < {MIN_N_FOR_PRECISION} = hidden. '
            f'N < {MIN_N_HIGH_CONFIDENCE} = wide CI, treat cautiously. '
            f'Purged OOS activates at N >= 25 (more honest estimate).'
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

    # Purged walk-forward status
    pwf = summary.get('purged_walk_forward', {})
    if pwf.get('enough_data'):
        logger.info(f"  --- PURGED WALK-FORWARD (more honest) ---")
        logger.info(f"  OOS precision: {pwf['oos_precision_pct_mean']}% ± {pwf['oos_precision_pct_std']}% "
                    f"({pwf['n_folds_used']} folds, embargo {pwf['embargo_days']}d)")
        if pwf.get('leakage_warning'):
            logger.warning(f"  ⚠ {pwf['leakage_warning']}")
    else:
        reason = pwf.get('reason_if_not_ready', 'not enough data')
        logger.info(f"  Purged walk-forward: {reason}")

    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
