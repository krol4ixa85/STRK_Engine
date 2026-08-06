#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
classify_flow.py — Flow Classification per flow_playbook
==========================================================

Читает L1 + L2 summaries от collectors, применяет эвристики из
skills/flow_playbook.txt, выдаёт JSON для агента в MUST #6.

КЛАССЫ (из flow_playbook):
  REBALANCE     CEX↔CEX same-owner (пример: Binance14 ↔ BingX)
  INTERNAL      within one exchange (deposit → hot → cold)
  DISTRIBUTION  whale → множество retail-адресов
  ACCUMULATION  множество → один HOLDS (retention >90%, <2 out/30d)
  BRIDGE_IN     → мост (потенциально accumulation intent)
  TRANSIT       high vol in ~ vol out, retention ~0
  REWARDS       staking rewards distribution (не sell pressure)
  UNKNOWN       маршрут не совпадает с шаблоном
  NOT_CHECKED   collector не запускался

TRAPS (запреты):
  ⛔ «отток CEX = накопление» — без маршрута НЕЛЬЗЯ
  ⛔ «приток CEX = дамп» — часто deposit before rebalance
  ⛔ класс по одному net-числу без маршрута
  ⛔ ярлык ≠ класс (Custody Outflow алерт → CEX↔CEX ребаланс)

Usage:
    python3 classify_flow.py
    python3 classify_flow.py --verbose  # print reasoning per seed
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ============================================================
# PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).parent.parent  # .../STRK_Engine
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
LOG_FILE = SCRIPT_DIR / 'logs' / 'classify_flow.log'
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

STRICT_NO_TRADING = os.environ.get('STRICT_NO_TRADING', 'true').lower() == 'true'

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger('classify_flow')

if not STRICT_NO_TRADING:
    logger.error("STRICT_NO_TRADING=false. This script is analysis-only.")
    sys.exit(1)


# ============================================================
# SEED CLASSIFICATION LOGIC
# ============================================================

def classify_seed(seed_summary: Dict, verbose: bool = False) -> Dict:
    """
    Классифицирует flow одного seed на основе паттернов.
    
    Возвращает dict с полями:
      - flow_class: один из классов выше
      - route: строка описания маршрута
      - reasoning: список причин (для transparency)
      - confidence: high/medium/low
    """
    reasoning = []
    edges = seed_summary.get('edges_count', 0)
    vol_in = seed_summary.get('vol_in_strk', 0)
    vol_out = seed_summary.get('vol_out_strk', 0)
    net = seed_summary.get('net_flow_strk', 0)
    cp_in = seed_summary.get('unique_counterparties_in', 0)
    cp_out = seed_summary.get('unique_counterparties_out', 0)
    category = seed_summary.get('category', '')
    role = seed_summary.get('role', '')
    seed_name = seed_summary.get('seed_name', '')
    
    # ========================================
    # CASE 0: Тишина
    # ========================================
    if edges == 0:
        return {
            'flow_class': 'NO_ACTIVITY',
            'route': 'no transfers in window',
            'reasoning': ['0 edges in lookback window'],
            'confidence': 'high',
        }
    
    # Retention ratio
    if vol_in > 0:
        retention = 1 - (vol_out / vol_in)
        retention_pct = retention * 100
    else:
        retention = None
        retention_pct = None
    
    if retention is not None:
        reasoning.append(f"retention = {retention_pct:.1f}% (in={vol_in:,.0f}, out={vol_out:,.0f})")
    else:
        reasoning.append(f"vol_out={vol_out:,.0f}, no inflow")
    
    reasoning.append(f"cp_in={cp_in}, cp_out={cp_out}")
    
    # ========================================
    # CASE 1: STAKING REWARDS (специальная категория)
    # ========================================
    if 'staking' in seed_name.lower() or 'staking' in role.lower():
        # Staking pattern: aggregated in from few validators,
        # distributed out to many stakers
        if cp_in <= 10 and cp_out >= 50:
            reasoning.append(f"staking pattern: {cp_in} inflow sources → {cp_out} outflow destinations")
            reasoning.append("classified as REWARDS distribution (not sell pressure)")
            return {
                'flow_class': 'REWARDS',
                'route': f"validators({cp_in}) → staking_contract → stakers({cp_out})",
                'reasoning': reasoning,
                'confidence': 'high',
            }
    
    # ========================================
    # CASE 2: BRIDGE
    # ========================================
    if 'bridge' in seed_name.lower() or 'bridge' in role.lower():
        if net > 100_000:
            reasoning.append(f"bridge net INFLOW +{net:,.0f} STRK — potential accumulation intent (needs L2 TVL check)")
            reasoning.append("⚠ NE trust as accumulation without L2 destination check")
            return {
                'flow_class': 'BRIDGE_IN',
                'route': f"multiple_l1 ({cp_in}) → bridge → L2",
                'reasoning': reasoning,
                'confidence': 'medium',  # low until L2 dest is verified
            }
        elif net < -100_000:
            reasoning.append(f"bridge net OUTFLOW {net:,.0f} STRK — L2 exiting to L1")
            return {
                'flow_class': 'BRIDGE_OUT',
                'route': f"L2 → bridge → L1 ({cp_out} destinations)",
                'reasoning': reasoning,
                'confidence': 'medium',
            }
        else:
            reasoning.append(f"bridge two-way balanced (net {net:+,.0f})")
            return {
                'flow_class': 'BRIDGE_BALANCED',
                'route': f"symmetric bridge activity",
                'reasoning': reasoning,
                'confidence': 'medium',
            }
    
    # ========================================
    # CASE 3: TRANSIT (retention ~0, vol_in ~ vol_out, few counterparties)
    # ========================================
    if retention is not None and abs(retention) < 0.1 and cp_in <= 3 and cp_out <= 3:
        reasoning.append(f"TRANSIT signature: retention {retention_pct:.1f}%, cp_in={cp_in}, cp_out={cp_out}")
        reasoning.append("this address passes through, doesn't hold — NOT accumulation")
        return {
            'flow_class': 'TRANSIT',
            'route': f"{cp_in} source(s) → this address → {cp_out} destination(s)",
            'reasoning': reasoning,
            'confidence': 'high',
        }
    
    # ========================================
    # CASE 4: CUSTODY / SILENT HOLDING
    # ========================================
    if 'custody' in seed_name.lower() or 'multisig' in seed_name.lower():
        if edges == 0:
            reasoning.append("custody address at rest — no movements")
            return {
                'flow_class': 'CUSTODY_IDLE',
                'route': 'no activity',
                'reasoning': reasoning,
                'confidence': 'high',
            }
        # Custody with outflows to CEX = potential distribution signal
        if vol_out > vol_in * 5:  # significant net outflow
            reasoning.append(f"custody with LARGE net outflow {net:,.0f} STRK — check destinations")
            reasoning.append("⚠ if destinations are CEX → DISTRIBUTION SIGNAL")
            reasoning.append("⚠ requires counterparty labels (Nansen/Etherscan) to confirm")
            return {
                'flow_class': 'CUSTODY_OUTFLOW',
                'route': f"custody → {cp_out} destinations",
                'reasoning': reasoning,
                'confidence': 'low',  # needs label check
            }
    
    # ========================================
    # CASE 5: ACCUMULATION signature
    # (many → one, high retention, low outflow count)
    # ========================================
    if retention is not None and retention > 0.9 and cp_in >= 5 and cp_out <= 2:
        reasoning.append(f"ACCUMULATION signature: {cp_in} sources → 1 holder, retention {retention_pct:.1f}%")
        return {
            'flow_class': 'ACCUMULATION',
            'route': f"many({cp_in}) → this seed → few({cp_out})",
            'reasoning': reasoning,
            'confidence': 'medium',
        }
    
    # ========================================
    # CASE 6: DISTRIBUTION signature
    # (one → many, negative retention, high outflow count)
    # ========================================
    if cp_in <= 3 and cp_out >= 10 and net < -100_000:
        reasoning.append(f"DISTRIBUTION signature: {cp_in} sources → {cp_out} recipients, net {net:+,.0f}")
        reasoning.append("⚠ check if recipients are retail (DISTRIBUTION) vs CEX (potential sell pressure)")
        return {
            'flow_class': 'DISTRIBUTION',
            'route': f"few({cp_in}) → this seed → many({cp_out})",
            'reasoning': reasoning,
            'confidence': 'medium',
        }
    
    # ========================================
    # CASE 7: REBALANCE candidate
    # (few in, few out, ~balanced, likely CEX↔CEX)
    # ========================================
    if cp_in <= 5 and cp_out <= 5 and abs(net) < max(vol_in, vol_out) * 0.3:
        reasoning.append(f"REBALANCE candidate: cp_in={cp_in}, cp_out={cp_out}, near-balanced")
        reasoning.append("⚠ requires CEX labels to confirm (Binance↔BingX, Binance↔Bybit etc.)")
        return {
            'flow_class': 'REBALANCE',
            'route': f"few({cp_in}) ↔ few({cp_out})",
            'reasoning': reasoning,
            'confidence': 'low',  # needs label verification
        }
    
    # ========================================
    # CASE 8: UNKNOWN (fallback)
    # ========================================
    reasoning.append("no clear pattern matched")
    reasoning.append(f"pattern: cp_in={cp_in}, cp_out={cp_out}, net={net:+,.0f}, retention={retention_pct if retention is not None else 'N/A'}")
    return {
        'flow_class': 'UNKNOWN',
        'route': f"unclassified pattern",
        'reasoning': reasoning,
        'confidence': 'low',
    }


# ============================================================
# AGGREGATE CLASSIFICATION
# ============================================================

def aggregate_network_class(l1_summary: Dict, l2_summary: Dict, seed_classifications: List[Dict]) -> Dict:
    """
    Aggregate class across both chains to produce single flow_map_summary
    for MUST #6.
    
    Priority:
    1. Any BRIDGE_IN + L2 accumulation → net accumulation
    2. Any DISTRIBUTION dominant → distribution
    3. Mostly REBALANCE/INTERNAL → rebalance
    4. Mostly IDLE/NO_ACTIVITY → quiet
    """
    classes = [c['flow_class'] for c in seed_classifications]
    counts = {}
    for c in classes:
        counts[c] = counts.get(c, 0) + 1
    
    # Detect bridge activity
    has_bridge_in = 'BRIDGE_IN' in classes
    has_bridge_out = 'BRIDGE_OUT' in classes
    has_transit = 'TRANSIT' in classes
    has_rewards = 'REWARDS' in classes
    has_distribution = 'DISTRIBUTION' in classes
    has_accumulation = 'ACCUMULATION' in classes
    
    if has_distribution and not has_accumulation:
        aggregate = 'DISTRIBUTION_DOMINANT'
    elif has_accumulation and not has_distribution:
        aggregate = 'ACCUMULATION_DOMINANT'
    elif has_bridge_in and not has_bridge_out:
        aggregate = 'BRIDGE_IN_DOMINANT'
    elif has_bridge_out and not has_bridge_in:
        aggregate = 'BRIDGE_OUT_DOMINANT'
    elif has_transit:
        aggregate = 'TRANSIT_ACTIVITY'
    elif has_rewards and len([c for c in classes if c not in ['REWARDS', 'NO_ACTIVITY', 'CUSTODY_IDLE']]) == 0:
        aggregate = 'REWARDS_ONLY'
    elif all(c in ['NO_ACTIVITY', 'CUSTODY_IDLE'] for c in classes):
        aggregate = 'QUIET'
    else:
        aggregate = 'MIXED'
    
    return {
        'aggregate_flow_class': aggregate,
        'class_distribution': counts,
        'has_bridge_activity': has_bridge_in or has_bridge_out,
        'has_transit_activity': has_transit,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', action='store_true', help='Print reasoning for each seed')
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("classify_flow.py starting")
    
    # Load L1 + L2 summaries
    l1_path = CACHE_DIR / 'flow_eth_summary.json'
    l2_path = CACHE_DIR / 'flow_starknet_summary.json'
    
    l1_summary = None
    l2_summary = None
    
    if l1_path.exists():
        with open(l1_path, 'r', encoding='utf-8') as f:
            l1_summary = json.load(f)
        logger.info(f"Loaded L1 summary: {l1_summary['seeds_processed']} seeds, {l1_summary['total_edges']} edges")
    else:
        logger.warning(f"L1 summary not found: {l1_path}")
    
    if l2_path.exists():
        with open(l2_path, 'r', encoding='utf-8') as f:
            l2_summary = json.load(f)
        logger.info(f"Loaded L2 summary: {l2_summary['seeds_processed']} seeds, {l2_summary['total_edges']} edges")
    else:
        logger.warning(f"L2 summary not found: {l2_path}")
    
    if not l1_summary and not l2_summary:
        logger.error("No summaries available. Run flow_eth.py and flow_starknet.py first.")
        return 1
    
    # Classify each seed
    all_classifications = []
    
    if l1_summary:
        for seed_summary in l1_summary.get('seeds_summary', []):
            seed_summary['chain'] = 'ethereum'
            classification = classify_seed(seed_summary, verbose=args.verbose)
            classification['seed_name'] = seed_summary['seed_name']
            classification['chain'] = 'ethereum'
            classification['metrics'] = {
                'edges_count': seed_summary['edges_count'],
                'vol_in_strk': seed_summary['vol_in_strk'],
                'vol_out_strk': seed_summary['vol_out_strk'],
                'net_flow_strk': seed_summary['net_flow_strk'],
                'cp_in': seed_summary['unique_counterparties_in'],
                'cp_out': seed_summary['unique_counterparties_out'],
            }
            all_classifications.append(classification)
    
    if l2_summary:
        for seed_summary in l2_summary.get('seeds_summary', []):
            seed_summary['chain'] = 'starknet'
            classification = classify_seed(seed_summary, verbose=args.verbose)
            classification['seed_name'] = seed_summary['seed_name']
            classification['chain'] = 'starknet'
            classification['metrics'] = {
                'edges_count': seed_summary['edges_count'],
                'vol_in_strk': seed_summary['vol_in_strk'],
                'vol_out_strk': seed_summary['vol_out_strk'],
                'net_flow_strk': seed_summary['net_flow_strk'],
                'cp_in': seed_summary['unique_counterparties_in'],
                'cp_out': seed_summary['unique_counterparties_out'],
            }
            all_classifications.append(classification)
    
    # Print classifications
    logger.info("=" * 60)
    logger.info("CLASSIFICATIONS")
    logger.info("=" * 60)
    for c in all_classifications:
        m = c['metrics']
        logger.info(f"[{c['chain']:9s}] {c['seed_name']:30s} → {c['flow_class']:20s} conf={c['confidence']}")
        logger.info(f"           route: {c['route']}")
        if args.verbose:
            for r in c['reasoning']:
                logger.info(f"           · {r}")
    
    # Aggregate network flow class
    aggregate = aggregate_network_class(l1_summary or {}, l2_summary or {}, all_classifications)
    
    # Write final flow_map_summary.json (MUST #6 input)
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'flow_class': aggregate['aggregate_flow_class'],
        'class_distribution': aggregate['class_distribution'],
        'has_bridge_activity': aggregate['has_bridge_activity'],
        'has_transit_activity': aggregate['has_transit_activity'],
        'per_seed_classification': all_classifications,
        'not_checked': False,
        'source_summaries': {
            'l1_as_of': l1_summary.get('as_of') if l1_summary else None,
            'l2_as_of': l2_summary.get('as_of') if l2_summary else None,
        },
        'caveats': [
            "REBALANCE/DISTRIBUTION classes need counterparty labels for full confirmation",
            "BRIDGE_IN requires L2 destination check for accumulation intent",
            "Confidence 'low' = needs Nansen/Etherscan labels to elevate",
        ],
    }
    
    output_path = CACHE_DIR / 'flow_map_summary.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    logger.info("=" * 60)
    logger.info(f"AGGREGATE NETWORK FLOW: {aggregate['aggregate_flow_class']}")
    logger.info(f"Distribution: {aggregate['class_distribution']}")
    logger.info(f"Written: {output_path}")
    logger.info("This JSON is input for MUST #6 in LIQ/RUN reports.")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
