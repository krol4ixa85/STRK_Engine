#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
false_signals_analyzer.py — обогащает CLOSED forecasts market conditions.

ЦЕЛЬ:
  Собрать honest dataset о том, при каких conditions система ошибалась.
  Через 30-60 дней можно будет искать patterns:
    - "RALLY_HIGH при BTC=DOWN + Wyckoff=MARKDOWN → precision 20%"
    - Тогда dynamic_weights делается ПО ДАННЫМ, не magic числам.

ЧТО ДЕЛАЕТ:
  1. Читает data/history/shadow_votes.jsonl
  2. Находит CLOSED forecasts (уже верifiled) где conditions_at_signal НЕ записаны
  3. Для этих записей читает snapshot conditions из истории кэша
     (если available через issued_at timestamp comparison)
  4. Записывает обогащённые записи в data/history/all_history_enriched.jsonl
  5. Строит summary статистику: precision по conditions за 30d

ЧТО НЕ ДЕЛАЕТ:
  - НЕ создаёт magic weights
  - НЕ трогает confluence_gate.py
  - НЕ модифицирует существующие jsonl (append-only enrichment)
  - НЕ применяет adjustments — только measurement

ВЫВОД:
  1. data/history/all_history_enriched.jsonl — enriched CLOSED forecasts
  2. data/reports/false_signals_summary.json — статистика ошибок по conditions
     (для чтения человеком или weekly_backtest)

ЗАПУСК:
  По расписанию в workflow — раз в сутки (например, 00:00 UTC).
  Дёшево — только чтение + append. Не влияет на runtime.
"""
import os
import sys
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
REPORTS_DIR = SCRIPT_DIR / 'data' / 'reports'

SHADOW_VOTES = HISTORY_DIR / 'shadow_votes.jsonl'
ALL_HISTORY = HISTORY_DIR / 'all_history.jsonl'
ENRICHED = HISTORY_DIR / 'all_history_enriched.jsonl'
STATE = CACHE_DIR / 'false_signals_state.json'
SUMMARY = REPORTS_DIR / 'false_signals_summary.json'


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
    """Read JSONL file. Returns list of records or empty."""
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
        logger.warning(f"Failed to read {path.name}: {e}")
    return records


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def snapshot_current_conditions():
    """Собрать snapshot текущих conditions из cache files."""
    conditions = {}

    # Wyckoff phase
    wyckoff = load_json(CACHE_DIR / 'wyckoff_phase.json', {})
    conditions['wyckoff_phase'] = wyckoff.get('phase') if wyckoff else None
    conditions['wyckoff_confidence'] = wyckoff.get('confidence') if wyckoff else None

    # Technical
    tech = load_json(CACHE_DIR / 'technical_momentum.json', {})
    features = tech.get('features') or {}
    conditions['rsi'] = features.get('rsi')
    conditions['slope_3d_pct'] = features.get('slope_3d_pct')
    conditions['slope_7d_pct'] = features.get('slope_7d_pct')
    conditions['vol_ratio_3d_vs_30d'] = features.get('vol_ratio_3d_vs_30d')

    # CEX flow
    cex = load_json(CACHE_DIR / 'cex_flow.json', {})
    classification = cex.get('classification') or {}
    conditions['cex_signal'] = classification.get('signal')
    stats = classification.get('stats') or {}
    conditions['cex_total_net_strk'] = stats.get('total_net_strk')
    conditions['cex_consecutive_bearish'] = stats.get('consecutive_bearish')
    conditions['cex_consecutive_bullish'] = stats.get('consecutive_bullish')

    # Cohort
    cohort = load_json(CACHE_DIR / 'cohort_tracker.json', {})
    cohorts = cohort.get('cohorts') or {}
    smart = cohorts.get('SMART') or cohorts.get('smart') or {}
    conditions['smart_flow_24h'] = smart.get('net_flow_strk') or smart.get('net_24h_strk')
    conditions['smart_behavior'] = smart.get('behavior')

    # CVD
    cvd = load_json(CACHE_DIR / 'cvd_analysis.json', {})
    timeframes = cvd.get('timeframes') or {}
    signals = []
    for tf_name in ('1h', '4h', '1d'):
        tf = timeframes.get(tf_name) or {}
        sig = tf.get('signal')
        if sig:
            signals.append(f"{tf_name}={sig}")
    conditions['cvd_signals'] = signals or None
    conditions['cvd_consensus'] = cvd.get('consensus_signal')

    # Funding
    funding = load_json(CACHE_DIR / 'funding_signal.json', {})
    fm = funding.get('funding_metrics') or {}
    conditions['funding_apr'] = fm.get('current_annualized_pct')
    conditions['funding_regime'] = fm.get('regime')

    # BTC / composite
    composite = load_json(CACHE_DIR / 'composite_signal_v2.json', {})
    conditions['btc_cycle'] = composite.get('btc_cycle') or (composite.get('inputs', {}).get('btc_context') or {}).get('cycle')

    # Confluence / decision snapshot
    confluence = load_json(CACHE_DIR / 'confluence_gate.json', {})
    conditions['confluence_signal'] = confluence.get('signal')
    conditions['confluence_strength'] = confluence.get('strength')

    # Dune monthly signal (если available)
    monthly = load_json(CACHE_DIR / 'dune_starknet_monthly.json', {})
    monthly_rows = monthly.get('rows') or []
    if monthly_rows:
        latest = monthly_rows[0]
        if isinstance(latest, dict):
            conditions['dune_monthly_signal'] = (latest.get('phase_signal') or latest.get('signal'))
            conditions['dune_monthly_trend'] = (latest.get('w_m_pct') or latest.get('pct_from_30d_max'))

    return conditions


def enrich_closed_forecasts(state):
    """Обогащаем CLOSED forecasts, для которых ещё не был record conditions."""
    if not SHADOW_VOTES.exists():
        logger.info(f"No shadow_votes.jsonl at {SHADOW_VOTES}")
        return 0

    processed_ids = set(state.get('processed_run_ids', []))
    votes = read_jsonl(SHADOW_VOTES)
    logger.info(f"Loaded {len(votes)} shadow vote records")

    # Snapshot conditions NOW (для новых votes только — они issued сейчас)
    current_conditions = snapshot_current_conditions()

    new_enriched = 0
    for v in votes:
        run_id = v.get('run_id')
        status = v.get('status', 'PENDING')

        # Обогащаем только CLOSED и только те, что ещё не processed
        if status != 'CLOSED':
            continue
        if run_id in processed_ids:
            continue

        # Определяем правильность: signal vs actual outcome
        # Structure varies — support both shadow_votes (per_voter_outcome) и all_history
        expected = None
        actual = v.get('outcome_signal') or v.get('actual_outcome')
        agg = v.get('aggregate_shadow') or {}
        if agg:
            if agg.get('crash_votes', 0) > agg.get('rally_votes', 0):
                expected = 'CRASH'
            elif agg.get('rally_votes', 0) > agg.get('crash_votes', 0):
                expected = 'RALLY'
            else:
                expected = 'NEUTRAL'

        is_correct = (expected == actual) if (expected and actual) else None

        enriched = {
            'run_id': run_id,
            'issued_at': v.get('issued_at'),
            'window': v.get('window'),
            'expected_outcome': expected,
            'actual_outcome': actual,
            'is_correct': is_correct,
            'issued_price': v.get('issued_price') or v.get('price_now'),
            'outcome_price': v.get('outcome_price'),
            'shadow_votes': v.get('shadow_votes') or {},
            'per_voter_outcome': v.get('per_voter_outcome') or {},
            'confluence_signal_at_issue': v.get('current_confluence_signal'),
            # ⚠ current_conditions это TEKUschie condition, не issue-time.
            # Для accurate analysis нужно snapshot @issue — этого пока нет.
            # Это первый шаг: собираем что можем, через N runs добавим issue-time snapshots.
            'conditions_at_analysis': current_conditions,
            'enriched_at': datetime.now(timezone.utc).isoformat(),
        }

        append_jsonl(ENRICHED, enriched)
        processed_ids.add(run_id)
        new_enriched += 1

    state['processed_run_ids'] = list(processed_ids)[-5000:]  # keep last 5000
    state['last_run'] = datetime.now(timezone.utc).isoformat()
    return new_enriched


def build_summary():
    """Aggregate statistics from enriched history."""
    if not ENRICHED.exists():
        logger.info("No enriched history yet")
        return

    records = read_jsonl(ENRICHED)
    if not records:
        return

    total = len(records)
    with_outcome = [r for r in records if r.get('is_correct') is not None]
    correct = sum(1 for r in with_outcome if r.get('is_correct'))
    incorrect = sum(1 for r in with_outcome if r.get('is_correct') is False)

    # Group false signals by conditions
    false_by_wyckoff = defaultdict(lambda: {'correct': 0, 'incorrect': 0, 'total': 0})
    false_by_btc = defaultdict(lambda: {'correct': 0, 'incorrect': 0, 'total': 0})
    false_by_expected = defaultdict(lambda: {'correct': 0, 'incorrect': 0, 'total': 0})

    for r in with_outcome:
        cond = r.get('conditions_at_analysis') or {}
        wyckoff = cond.get('wyckoff_phase') or 'UNKNOWN'
        btc = cond.get('btc_cycle') or 'UNKNOWN'
        expected = r.get('expected_outcome') or 'UNKNOWN'

        for group_dict, key in [(false_by_wyckoff, wyckoff),
                                 (false_by_btc, btc),
                                 (false_by_expected, expected)]:
            group_dict[key]['total'] += 1
            if r.get('is_correct'):
                group_dict[key]['correct'] += 1
            else:
                group_dict[key]['incorrect'] += 1

    # Compute precision per group (только если N >= 5)
    def precision_by(group_dict, min_n=5):
        out = {}
        for k, v in group_dict.items():
            if v['total'] >= min_n:
                out[k] = {
                    'precision_pct': round(v['correct'] / v['total'] * 100, 1) if v['total'] else 0,
                    'n_total': v['total'],
                    'n_correct': v['correct'],
                    'n_incorrect': v['incorrect'],
                    'confidence_interval_note':
                        'Wide CI at N<15. Use for pattern hints only, not weights.'
                        if v['total'] < 15 else
                        'N>=15, could be used for weights with careful stat test.',
                }
            else:
                out[k] = {
                    'precision_pct': None,
                    'n_total': v['total'],
                    'confidence_interval_note': 'N<5, INSUFFICIENT DATA',
                }
        return out

    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_enriched': total,
        'with_outcome': len(with_outcome),
        'correct': correct,
        'incorrect': incorrect,
        'overall_precision_pct': round(correct / len(with_outcome) * 100, 1) if with_outcome else None,
        'by_wyckoff_phase': precision_by(false_by_wyckoff),
        'by_btc_cycle': precision_by(false_by_btc),
        'by_expected_outcome': precision_by(false_by_expected),
        'note': (
            'This summary is INFORMATIONAL only. '
            'Do NOT use precision numbers to build dynamic_weights until N>=30 in each bucket. '
            'At N<30, precision estimates have wide confidence intervals (±15-25%).'
        ),
    }

    save_json(SUMMARY, summary)
    logger.info(f"Summary written: {total} enriched, {len(with_outcome)} with outcome, "
                f"precision {summary['overall_precision_pct']}%")


def main():
    logger.info("=" * 60)
    logger.info("FALSE SIGNALS ANALYZER (data collection · no DECISION impact)")
    logger.info("=" * 60)

    state = load_json(STATE, default={'processed_run_ids': [], 'last_run': None})

    new_count = enrich_closed_forecasts(state)
    logger.info(f"Enriched {new_count} newly closed forecasts")

    save_json(STATE, state)

    build_summary()

    logger.info("=" * 60)
    logger.info("Done. dynamic_weights NOT built — waiting for N>=30 buckets.")
    logger.info("Wire-in ONLY when weekly_backtest confirms precision stability.")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())