#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
covert_flow_detector.py — Скрытые паттерны накопления/распределения

Читает уже собранные rebra:
  · data/cache/flow_eth_edges.csv        (L1, orchestrator step 1)
  · data/cache/flow_starknet_edges.csv   (L2, orchestrator step 2)

Для каждого seed-адреса (из flow_seeds.json, кроме EXPLICIT категорий):
  · vol_in, vol_out
  · retention = (in - out) / in           (доля удержания)
  · unique_cp_in, unique_cp_out
  · avg_size_in, avg_size_out
  · num_tx_in, num_tx_out

Классификация (пороги из voter_config.covert_flow_detector_params):
  · ACCUMULATION   — vol_in > vol_out * ratio AND retention > pct
                     AND unique_cp_in ≥ N AND vol_in > min_absolute
  · DISTRIBUTION   — vol_out > vol_in * ratio AND unique_cp_out ≥ N
                     AND vol_out > min_absolute AND retention < 0
  · NEUTRAL        — всё остальное

Aggregate по всем не-EXPLICIT seeds:
  · STRONG_ACCUMULATION   — n_accum > n_dist * 2 AND n_accum ≥ 3
  · ACCUMULATION           — n_accum > n_dist
  · STRONG_DISTRIBUTION    — n_dist > n_accum * 2 AND n_dist ≥ 3
  · DISTRIBUTION           — n_dist > n_accum
  · NEUTRAL                — иначе

Пишет: data/cache/covert_flow_signal.json

STATUS: HYPOTHESIS · voter автоматически подхватится shadow_voter.py
(добавляется в voter_config.json как 6-й voter).

Не влияет на confluence_gate / composite / decision. Наблюдение.
"""
import os
import sys
import csv
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
CONFIG_FILE = SCRIPT_DIR / 'config' / 'voter_config.json'
EDGES_L1 = CACHE_DIR / 'flow_eth_edges.csv'
EDGES_L2 = CACHE_DIR / 'flow_starknet_edges.csv'
OUTPUT_FILE = CACHE_DIR / 'covert_flow_signal.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('covert')

# Fallback thresholds (used if voter_config.json missing or key absent).
# Все помечены как HYPOTHESIS, real values living in config/voter_config.json.
DEFAULT_PARAMS = {
    'min_inflow_outflow_ratio_accumulation': 1.5,
    'min_retention_pct_accumulation': 70.0,
    'min_unique_counterparties_accumulation': 3,
    'min_outflow_inflow_ratio_distribution': 1.5,
    'min_unique_counterparties_distribution': 3,
    'min_absolute_flow_strk': 100000.0,
    'aggregate_strong_multiplier': 2.0,
    'aggregate_strong_min_count': 3,
}

# Категории, которые НЕ анализируем как «скрытые» — их поведение
# известно/легитимно и не является «накоплением/распределением».
EXPLICIT_CATEGORIES = {
    'cex_hot_wallets_known_dynamic',
    'l1_infrastructure',
    'l2_native',
    'team_and_foundation',
    'custody_and_transit',   # это transit, а не accumulator
}


def load_params():
    """Load thresholds from voter_config.json → covert_flow_detector_params."""
    if not CONFIG_FILE.exists():
        return DEFAULT_PARAMS.copy()
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        params = ((cfg.get('_meta') or {}).get('covert_flow_detector_params') or {})
        merged = DEFAULT_PARAMS.copy()
        merged.update(params)
        return merged
    except Exception as e:
        logger.warning(f'load config: {e}')
        return DEFAULT_PARAMS.copy()


def load_seeds():
    """Load flow_seeds.json → {address_lower: {name, category, is_explicit}}."""
    if not SEEDS_FILE.exists():
        logger.warning(f'flow_seeds.json not found: {SEEDS_FILE}')
        return {}
    try:
        data = json.loads(SEEDS_FILE.read_text(encoding='utf-8'))
    except Exception as e:
        logger.error(f'load seeds: {e}')
        return {}

    seeds = {}
    for cat, entries in data.items():
        if cat.startswith('_'):
            continue
        if not isinstance(entries, dict):
            continue
        for name, info in entries.items():
            if name.startswith('_'):
                continue
            if not isinstance(info, dict):
                continue
            addr = (info.get('address') or '').lower().strip()
            if not addr:
                continue
            seeds[addr] = {
                'name': name,
                'category': cat,
                'is_explicit': cat in EXPLICIT_CATEGORIES,
                'role': info.get('role', ''),
            }
    return seeds


def read_edges_csv(path):
    """Yield edge dicts from CSV. Empty list if missing."""
    if not path.exists():
        return []
    edges = []
    try:
        with open(path, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Normalize
                try:
                    row['amount_strk'] = float(row.get('amount_strk', 0) or 0)
                except (ValueError, TypeError):
                    row['amount_strk'] = 0.0
                row['seed_address'] = (row.get('seed_address') or '').lower()
                row['counterparty'] = (row.get('counterparty') or '').lower()
                edges.append(row)
    except Exception as e:
        logger.warning(f'read {path}: {e}')
    return edges


def analyze_seed(seed_addr, seed_info, edges):
    """Aggregate all edges for one seed address, compute metrics + classification."""
    in_edges = [e for e in edges
                if e['seed_address'] == seed_addr and e.get('direction') == 'in']
    out_edges = [e for e in edges
                 if e['seed_address'] == seed_addr and e.get('direction') == 'out']

    vol_in = sum(e['amount_strk'] for e in in_edges)
    vol_out = sum(e['amount_strk'] for e in out_edges)
    cp_in = len({e['counterparty'] for e in in_edges if e['counterparty']})
    cp_out = len({e['counterparty'] for e in out_edges if e['counterparty']})
    n_in = len(in_edges)
    n_out = len(out_edges)
    avg_in = vol_in / n_in if n_in else 0
    avg_out = vol_out / n_out if n_out else 0
    net = vol_in - vol_out
    retention_pct = (net / vol_in * 100) if vol_in > 0 else 0.0

    return {
        'address': seed_addr,
        'seed_name': seed_info.get('name', ''),
        'category': seed_info.get('category', ''),
        'metrics': {
            'vol_in': round(vol_in, 2),
            'vol_out': round(vol_out, 2),
            'net': round(net, 2),
            'retention_pct': round(retention_pct, 2),
            'unique_cp_in': cp_in,
            'unique_cp_out': cp_out,
            'num_tx_in': n_in,
            'num_tx_out': n_out,
            'avg_size_in': round(avg_in, 2),
            'avg_size_out': round(avg_out, 2),
        },
    }


def classify(analysis, params):
    """Classify seed as ACCUMULATION / DISTRIBUTION / NEUTRAL."""
    m = analysis['metrics']
    vol_in = m['vol_in']
    vol_out = m['vol_out']

    # Skip if no activity above absolute floor
    if max(vol_in, vol_out) < params['min_absolute_flow_strk']:
        return 'INACTIVE'

    accum_ratio_ok = vol_out == 0 or vol_in > vol_out * params['min_inflow_outflow_ratio_accumulation']
    accum_retention_ok = m['retention_pct'] > params['min_retention_pct_accumulation']
    accum_cp_ok = m['unique_cp_in'] >= params['min_unique_counterparties_accumulation']

    if vol_in > 0 and accum_ratio_ok and accum_retention_ok and accum_cp_ok:
        return 'ACCUMULATION'

    dist_ratio_ok = vol_in == 0 or vol_out > vol_in * params['min_outflow_inflow_ratio_distribution']
    dist_cp_ok = m['unique_cp_out'] >= params['min_unique_counterparties_distribution']
    dist_retention_ok = m['retention_pct'] < 0

    if vol_out > 0 and dist_ratio_ok and dist_cp_ok and dist_retention_ok:
        return 'DISTRIBUTION'

    return 'NEUTRAL'


def aggregate(results, params):
    """Compute overall signal + counts."""
    n_accum = sum(1 for r in results if r['classification'] == 'ACCUMULATION')
    n_dist = sum(1 for r in results if r['classification'] == 'DISTRIBUTION')
    n_neutral = sum(1 for r in results if r['classification'] == 'NEUTRAL')
    n_inactive = sum(1 for r in results if r['classification'] == 'INACTIVE')

    mult = params['aggregate_strong_multiplier']
    strong_n = params['aggregate_strong_min_count']

    if n_accum >= strong_n and n_accum > n_dist * mult:
        overall = 'STRONG_ACCUMULATION'
    elif n_dist >= strong_n and n_dist > n_accum * mult:
        overall = 'STRONG_DISTRIBUTION'
    elif n_accum > n_dist:
        overall = 'ACCUMULATION'
    elif n_dist > n_accum:
        overall = 'DISTRIBUTION'
    else:
        overall = 'NEUTRAL'

    return {
        'overall_signal': overall,
        'counts': {
            'accumulation': n_accum,
            'distribution': n_dist,
            'neutral': n_neutral,
            'inactive': n_inactive,
            'total_analyzed': len(results),
        },
    }


def build_layman(overall, counts):
    """Human-language explanation."""
    n_a, n_d = counts['accumulation'], counts['distribution']
    total = counts['total_analyzed']

    if overall == 'STRONG_ACCUMULATION':
        return (f"Скрытая аккумуляция: {n_a} из {total} seed-адресов "
                f"удерживают, {n_d} распределяют. Соотношение > 2x. "
                f"Smart money buying signal.")
    if overall == 'STRONG_DISTRIBUTION':
        return (f"Скрытое распределение: {n_d} из {total} seed-адресов "
                f"раздают, {n_a} удерживают. Соотношение > 2x. "
                f"Smart money selling signal.")
    if overall == 'ACCUMULATION':
        return f"Мягкая аккумуляция: {n_a} держателей vs {n_d} раздающих."
    if overall == 'DISTRIBUTION':
        return f"Мягкое распределение: {n_d} раздающих vs {n_a} держателей."
    return f"Нейтрально: {n_a} accum, {n_d} dist, {counts['neutral']} neutral."


def main():
    logger.info('=' * 60)
    logger.info('COVERT FLOW DETECTOR · STATUS: HYPOTHESIS (shadow)')
    logger.info('=' * 60)

    params = load_params()
    logger.info(f'Params: retention>{params["min_retention_pct_accumulation"]}%, '
                f'cp≥{params["min_unique_counterparties_accumulation"]}, '
                f'min_flow={params["min_absolute_flow_strk"]:,.0f} STRK')

    seeds = load_seeds()
    logger.info(f'Loaded {len(seeds)} seed addresses from flow_seeds.json')

    edges_l1 = read_edges_csv(EDGES_L1)
    edges_l2 = read_edges_csv(EDGES_L2)
    all_edges = edges_l1 + edges_l2
    logger.info(f'Edges: L1={len(edges_l1)}, L2={len(edges_l2)}, total={len(all_edges)}')

    if not all_edges:
        # Graceful degradation — write NOT_CHECKED marker
        output = {
            'as_of': datetime.now(timezone.utc).isoformat(),
            'status': 'NOT_CHECKED',
            'reason': 'No edges in flow_eth_edges.csv or flow_starknet_edges.csv',
            'hint': 'Run orchestrator.py first',
            'overall_signal': 'UNKNOWN',
            'counts': {'accumulation': 0, 'distribution': 0, 'neutral': 0,
                       'inactive': 0, 'total_analyzed': 0},
        }
        OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                               encoding='utf-8')
        logger.info(f'NOT_CHECKED written to {OUTPUT_FILE}')
        return 0

    # Analyze each non-explicit seed
    results = []
    for addr, info in seeds.items():
        if info['is_explicit']:
            continue
        analysis = analyze_seed(addr, info, all_edges)
        analysis['classification'] = classify(analysis, params)
        results.append(analysis)

    # Sort by activity for top-N display
    results.sort(key=lambda r: r['metrics']['vol_in'] + r['metrics']['vol_out'],
                 reverse=True)

    # Aggregate
    agg = aggregate(results, params)
    layman = build_layman(agg['overall_signal'], agg['counts'])

    # Top accumulators / distributors
    top_accum = [r for r in results if r['classification'] == 'ACCUMULATION'][:5]
    top_dist = [r for r in results if r['classification'] == 'DISTRIBUTION'][:5]

    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'status': 'OK',
        'overall_signal': agg['overall_signal'],
        'layman': layman,
        'counts': agg['counts'],
        'params_used': params,
        'params_status': 'HYPOTHESIS',
        'top_accumulators': top_accum,
        'top_distributors': top_dist,
        'inactive_addresses': sum(1 for r in results
                                   if r['classification'] == 'INACTIVE'),
        'note': 'Shadow signal · NOT decision-relevant. Registered as voter '
                'in shadow_voter.py; calibration через ≥15 closed shadow forecasts.',
    }

    OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                           encoding='utf-8')
    logger.info(f'Signal: {agg["overall_signal"]}')
    logger.info(f'  ACC={agg["counts"]["accumulation"]} '
                f'DIST={agg["counts"]["distribution"]} '
                f'NEUTRAL={agg["counts"]["neutral"]} '
                f'INACTIVE={agg["counts"]["inactive"]}')
    logger.info(f'  {layman}')
    logger.info(f'Written to {OUTPUT_FILE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
