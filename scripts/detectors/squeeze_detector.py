#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
squeeze_detector.py — определяет squeeze setup через 3 категории условий.

Категории (каждая = ≥2/3 условий):
  A · Positioning / Derivatives  (funding crowded short)
  B · On-chain Accumulation      (SMART cohort + CEX outflow + WATCHLIST)
  C · Technical setup            (oversold + range low + CVD divergence)

Уровни:
  INACTIVE — < 2 категорий active
  ACTIVE   — 2 категории active
  STRONG   — 3 категории active

Не отправляет alerts сам. Пишет только data/cache/squeeze_state.json.
Squeeze_notifier читает state, сравнивает с previous, шлёт alert при transition.
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
OUTPUT_FILE = CACHE_DIR / 'squeeze_state.json'

# =====================================================================
# THRESHOLDS · СТРОГИЕ (можно adjust позже через squeeze_config.json)
# =====================================================================
FUNDING_CROWDED_SHORT_PCT = -15.0      # A1: annualized funding
FUNDING_ACCEL_PCT = -10.0              # A2: falling + APR below
SMART_ACCUM_STRK = 2_000_000           # B1: SMART cohort 24h net
CEX_OUTFLOW_STREAK_DAYS = 3            # B2: consecutive bullish days
WATCHLIST_ACCUM_STRK = 100_000         # B3: watchlist buying
RSI_OVERSOLD = 30                      # C1: RSI level
VOL_SPIKE_RATIO = 2.0                  # C1: volume vs average
PCT_FROM_LOW_MAX = 5.0                 # C2: at range low
CVD_BULL_SIGNALS = ('BULLISH_DIVERGENCE', 'STEALTH_ACCUMULATION', 'BULL_DIV')


def load_json(name):
    path = CACHE_DIR / name
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def check_category_a(funding_signal):
    """A · Positioning / Derivatives — funding crowded short."""
    fm = funding_signal.get('funding_metrics') or {}
    apr = fm.get('current_annualized_pct')
    trend = fm.get('trend', '')
    extreme = fm.get('extreme', '')

    conditions = []
    # A1: funding <= -15%
    if apr is not None and apr <= FUNDING_CROWDED_SHORT_PCT:
        conditions.append({
            'id': 'A1',
            'name': 'Funding crowded short',
            'active': True,
            'evidence': f'Funding APR {apr:+.2f}% (below {FUNDING_CROWDED_SHORT_PCT:+.0f}% threshold)',
        })
    else:
        conditions.append({
            'id': 'A1', 'name': 'Funding crowded short', 'active': False,
            'evidence': f'Funding APR {apr:+.2f}%' if apr is not None else 'no data',
        })
    # A2: falling + APR below -10%
    a2_active = (trend == 'falling' and apr is not None and apr <= FUNDING_ACCEL_PCT)
    conditions.append({
        'id': 'A2', 'name': 'Funding accelerating negative',
        'active': a2_active,
        'evidence': f'trend={trend}, APR={apr}' if apr is not None else 'no data',
    })
    # A3: extreme short flag
    a3_active = (extreme == 'short_extreme')
    conditions.append({
        'id': 'A3', 'name': 'Extreme short flag',
        'active': a3_active,
        'evidence': f'extreme={extreme or "none"}',
    })

    active_count = sum(1 for c in conditions if c['active'])
    return {
        'category': 'A',
        'name': 'Positioning / Derivatives',
        'active': active_count >= 2,
        'active_count': active_count,
        'total': 3,
        'conditions': conditions,
    }


def check_category_b(cohort_tracker, cex_flow):
    """B · On-chain Accumulation."""
    cohorts = cohort_tracker.get('cohorts') or {}
    smart = cohorts.get('SMART') or cohorts.get('smart') or {}
    watchlist = cohorts.get('WATCHLIST') or cohorts.get('watchlist') or {}
    smart_net = smart.get('net_flow_strk') or smart.get('net_24h_strk') or 0
    watchlist_net = watchlist.get('net_flow_strk') or watchlist.get('net_24h_strk') or 0

    cex_stats = ((cex_flow.get('classification') or {}).get('stats') or {})
    bullish_streak = cex_stats.get('consecutive_bullish', 0)

    conditions = []
    # B1: SMART accumulating
    b1_active = smart_net >= SMART_ACCUM_STRK
    conditions.append({
        'id': 'B1', 'name': 'SMART cohort accumulation',
        'active': b1_active,
        'evidence': f'SMART 24h net {smart_net/1e6:+.2f}M STRK (threshold {SMART_ACCUM_STRK/1e6:+.1f}M)',
    })
    # B2: CEX outflow streak
    b2_active = bullish_streak >= CEX_OUTFLOW_STREAK_DAYS
    conditions.append({
        'id': 'B2', 'name': 'CEX outflow streak',
        'active': b2_active,
        'evidence': f'{bullish_streak} consecutive bullish days (threshold {CEX_OUTFLOW_STREAK_DAYS})',
    })
    # B3: WATCHLIST accumulating
    b3_active = watchlist_net >= WATCHLIST_ACCUM_STRK
    conditions.append({
        'id': 'B3', 'name': 'WATCHLIST accumulating',
        'active': b3_active,
        'evidence': f'WATCHLIST 24h net {watchlist_net/1e6:+.2f}M STRK',
    })

    active_count = sum(1 for c in conditions if c['active'])
    return {
        'category': 'B',
        'name': 'On-chain Accumulation',
        'active': active_count >= 2,
        'active_count': active_count,
        'total': 3,
        'conditions': conditions,
    }


def check_category_c(technical, cvd_analysis):
    """C · Technical setup."""
    features = technical.get('features') or {}
    rsi = features.get('rsi')
    vol_ratio = features.get('vol_ratio_3d_vs_30d')
    pct_from_low = features.get('pct_from_low')
    if pct_from_low is None:
        pct_from_low = features.get('pct_from_14d_low')

    cvd_1h = ((cvd_analysis.get('timeframes') or {}).get('1h') or
              (cvd_analysis.get('timeframes') or {}).get('1H') or {})
    cvd_signal = str(cvd_1h.get('signal', '')).upper()

    conditions = []
    # C1: RSI oversold + volume spike
    c1_active = (rsi is not None and rsi < RSI_OVERSOLD and
                 vol_ratio is not None and vol_ratio >= VOL_SPIKE_RATIO)
    conditions.append({
        'id': 'C1', 'name': 'Oversold + volume spike',
        'active': c1_active,
        'evidence': f'RSI {rsi}, Vol {vol_ratio}x' if rsi is not None else 'no data',
    })
    # C2: at range low
    c2_active = (pct_from_low is not None and pct_from_low <= PCT_FROM_LOW_MAX)
    conditions.append({
        'id': 'C2', 'name': 'At 14d range low',
        'active': c2_active,
        'evidence': f'{pct_from_low:.1f}% from 14d low' if pct_from_low is not None else 'no data',
    })
    # C3: CVD bull divergence
    c3_active = any(sig in cvd_signal for sig in CVD_BULL_SIGNALS)
    conditions.append({
        'id': 'C3', 'name': 'CVD bull divergence',
        'active': c3_active,
        'evidence': f'CVD 1h = {cvd_signal or "none"}',
    })

    active_count = sum(1 for c in conditions if c['active'])
    return {
        'category': 'C',
        'name': 'Technical setup',
        'active': active_count >= 2,
        'active_count': active_count,
        'total': 3,
        'conditions': conditions,
    }


def compute_squeeze_state():
    funding = load_json('funding_signal.json')
    cohort = load_json('cohort_tracker.json')
    cex = load_json('cex_flow.json')
    technical = load_json('technical_momentum.json')
    cvd = load_json('cvd_analysis.json')

    cat_a = check_category_a(funding)
    cat_b = check_category_b(cohort, cex)
    cat_c = check_category_c(technical, cvd)

    active_categories = sum(1 for cat in [cat_a, cat_b, cat_c] if cat['active'])

    if active_categories >= 3:
        level = 'STRONG'
    elif active_categories >= 2:
        level = 'ACTIVE'
    else:
        level = 'INACTIVE'

    return {
        'ts': datetime.now(timezone.utc).isoformat(),
        'level': level,
        'active_categories': active_categories,
        'total_categories': 3,
        'categories': [cat_a, cat_b, cat_c],
        'thresholds': {
            'funding_crowded_short_pct': FUNDING_CROWDED_SHORT_PCT,
            'funding_accel_pct': FUNDING_ACCEL_PCT,
            'smart_accum_strk': SMART_ACCUM_STRK,
            'cex_outflow_streak_days': CEX_OUTFLOW_STREAK_DAYS,
            'watchlist_accum_strk': WATCHLIST_ACCUM_STRK,
            'rsi_oversold': RSI_OVERSOLD,
            'vol_spike_ratio': VOL_SPIKE_RATIO,
            'pct_from_low_max': PCT_FROM_LOW_MAX,
        },
    }


def main():
    logger.info("=" * 60)
    logger.info("SQUEEZE DETECTOR")
    logger.info("=" * 60)

    state = compute_squeeze_state()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    logger.info(f"Level: {state['level']}")
    logger.info(f"Active categories: {state['active_categories']}/{state['total_categories']}")
    for cat in state['categories']:
        mark = '✓' if cat['active'] else '·'
        logger.info(f"  {mark} {cat['category']} · {cat['name']} · {cat['active_count']}/3")
        for cond in cat['conditions']:
            cm = '✓' if cond['active'] else '·'
            logger.info(f"    {cm} {cond['id']} · {cond['name']} · {cond['evidence']}")
    logger.info(f"Saved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())