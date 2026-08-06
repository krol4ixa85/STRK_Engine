#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
liquidity_shift.py — Aggregated L2 liquidity shift detector

Читает три компонента и синтезирует общий сигнал:
  1. data/cache/ekubo_flow.json          → DEX pool LP shift
  2. data/cache/endur_lst_flow.json      → LST mint/redeem flow
  3. data/cache/native_staking_flow.py   → native stake in/out

Разделяет STRK, застейканный через LST, от нативного стейкинга:
  pure_native_stake = total_stake - endur_tvl_strk

Классификация overall_direction:
  Смотрим на два вектора:
    · stake_vector    = Δ(pure_native_stake + endur_tvl_strk) за 24h
    · lp_vector       = ekubo net_strk_delta_24h

  LOCKING_UP   : stake ↑ ∧ lp ↑            — общий приток в экосистему
  EXTRACTING   : stake ↓ ∧ lp ↓            — общий отток
  ROTATING_TO_STAKE : stake ↑ ∧ lp ↓       — переток DEX → stake (long-term intent)
  ROTATING_TO_DEX   : stake ↓ ∧ lp ↑       — переток stake → DEX (готовятся торговать/выводить)
  STABLE       : оба почти нулевые

Пороги (в STRK):
  |delta| < 0.5% от pure_native OR < 200k STRK → считаем нулевым
  Иначе — направленное движение

Output: data/cache/liquidity_shift.json
{
  "as_of": "...",
  "components": {
    "ekubo": {...},        // сокращённый summary из ekubo_flow.json
    "endur_lst": {...},    // из endur_lst_flow.json
    "native_stake": {...}  // из native_staking_flow.json
  },
  "vectors": {
    "stake_delta_24h_strk": +150000,
    "lp_delta_24h_strk": -80000,
    "stake_signal": "STAKE_INFLOW",
    "lp_signal": "LP_REMOVING"
  },
  "overall_direction": "ROTATING_TO_STAKE",
  "layman": "…"
}
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = CACHE_DIR / 'liquidity_shift.json'

EKUBO_FILE = CACHE_DIR / 'ekubo_flow.json'
ENDUR_FILE = CACHE_DIR / 'endur_lst_flow.json'
NATIVE_FILE = CACHE_DIR / 'native_staking_flow.json'

# Thresholds
ABS_STRK_NOISE = 200_000       # below this = noise
PCT_STAKE_NOISE = 0.5          # % of pure native stake, below = noise

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('liq_shift')


def load(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        logger.error(f'load {path.name}: {e}')
        return None


def classify_vector(delta, reference):
    """
    Return one of: POSITIVE / NEGATIVE / STABLE, given delta and reference size.
    delta,reference in STRK.
    """
    if delta is None:
        return 'UNKNOWN'
    if abs(delta) < ABS_STRK_NOISE:
        return 'STABLE'
    if reference and reference > 0:
        pct = abs(delta) / reference * 100
        if pct < PCT_STAKE_NOISE:
            return 'STABLE'
    return 'POSITIVE' if delta > 0 else 'NEGATIVE'


def overall_signal(stake_v, lp_v):
    """Map (stake_vector, lp_vector) → overall direction."""
    if 'UNKNOWN' in (stake_v, lp_v):
        return 'PARTIAL'
    if stake_v == 'STABLE' and lp_v == 'STABLE':
        return 'STABLE'
    if stake_v == 'POSITIVE' and lp_v == 'POSITIVE':
        return 'LOCKING_UP'
    if stake_v == 'NEGATIVE' and lp_v == 'NEGATIVE':
        return 'EXTRACTING'
    if stake_v == 'POSITIVE' and lp_v == 'NEGATIVE':
        return 'ROTATING_TO_STAKE'
    if stake_v == 'NEGATIVE' and lp_v == 'POSITIVE':
        return 'ROTATING_TO_DEX'
    # One STABLE, one directional
    if stake_v != 'STABLE':
        return 'STAKE_INFLOW' if stake_v == 'POSITIVE' else 'STAKE_OUTFLOW'
    if lp_v != 'STABLE':
        return 'LP_ADDING' if lp_v == 'POSITIVE' else 'LP_REMOVING'
    return 'STABLE'


LAYMAN = {
    'LOCKING_UP': 'Приток и в стейкинг, и в DEX-пулы. Ликвидность заякоривается в экосистеме — накопительный сигнал.',
    'EXTRACTING': 'Отток и из стейкинга, и из DEX-пулов. Ликвидность уходит из экосистемы — распределительный сигнал.',
    'ROTATING_TO_STAKE': 'Ликвидность перетекает из DEX в стейкинг. Долгосрочные держатели фиксируют позиции (стейкают вместо торговли).',
    'ROTATING_TO_DEX': 'Ликвидность перетекает из стейкинга в DEX-пулы. Стейкеры расстейкиваются и уходят в LP или готовятся продавать.',
    'STAKE_INFLOW': 'Приток в стейкинг, DEX-пулы без движения. Слабый бычий сигнал.',
    'STAKE_OUTFLOW': 'Отток из стейкинга, DEX-пулы без движения. Слабый медвежий сигнал.',
    'LP_ADDING': 'Приток в DEX-пулы, стейкинг без движения. Готовится торговая ликвидность.',
    'LP_REMOVING': 'Отток из DEX-пулов, стейкинг без движения. LP выводят капитал — тонкие книги ближе.',
    'STABLE': 'Ликвидность в экосистеме стабильна за 24ч. Нет сильных движений.',
    'PARTIAL': 'Часть данных недоступна. Смотрим по видимым компонентам, полная картина не собрана.',
}


def main():
    logger.info('=' * 60)
    logger.info('LIQUIDITY SHIFT · aggregate (Ekubo + Endur LST + Native stake)')
    logger.info('=' * 60)

    ekubo = load(EKUBO_FILE)
    endur = load(ENDUR_FILE)
    native = load(NATIVE_FILE)

    # --- extract vectors ---
    lp_delta_strk = None
    if ekubo and ekubo.get('aggregate'):
        lp_delta_strk = ekubo['aggregate'].get('net_strk_delta_24h')

    endur_delta_strk = None
    endur_tvl_strk = None
    if endur and endur.get('deltas') and endur.get('tvl_strk_now'):
        endur_tvl_strk = endur['tvl_strk_now']
        # Endur теперь даёт strk_delta_24h напрямую (в STRK-count из DefiLlama tokens series)
        endur_delta_strk = endur['deltas'].get('strk_delta_24h')

    native_total = None
    native_delta_strk = None
    if native and native.get('total_stake_strk_now') is not None:
        native_total = native['total_stake_strk_now']
        native_delta_strk = native.get('deltas', {}).get('delta_24h')

    # Pure native = total_stake − endur_tvl_strk
    pure_native = None
    if native_total is not None and endur_tvl_strk is not None:
        pure_native = native_total - endur_tvl_strk

    # Combined stake vector = pure native delta + LST STRK delta
    # (both stake ways contribute to "STRK locked out of circulation")
    stake_delta_combined = None
    if native_delta_strk is not None or endur_delta_strk is not None:
        stake_delta_combined = (native_delta_strk or 0) + (endur_delta_strk or 0)

    stake_ref = pure_native if pure_native and pure_native > 0 else native_total
    stake_v = classify_vector(stake_delta_combined, stake_ref)
    lp_v = classify_vector(lp_delta_strk, endur_tvl_strk)
    direction = overall_signal(stake_v, lp_v)

    # ---- build result ----
    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'components': {
            'ekubo': _sum_ekubo(ekubo),
            'endur_lst': _sum_endur(endur),
            'native_staking': _sum_native(native),
        },
        'derived': {
            'total_stake_strk': native_total,
            'endur_lst_tvl_strk': endur_tvl_strk,
            'pure_native_stake_strk': round(pure_native, 0) if pure_native is not None else None,
            'lst_share_of_stake_pct': round(endur_tvl_strk / native_total * 100, 2)
                                       if endur_tvl_strk and native_total else None,
        },
        'vectors': {
            'stake_delta_24h_strk': round(stake_delta_combined, 0) if stake_delta_combined is not None else None,
            'lp_delta_24h_strk': round(lp_delta_strk, 0) if lp_delta_strk is not None else None,
            'stake_signal': stake_v,
            'lp_signal': lp_v,
        },
        'overall_direction': direction,
        'layman': LAYMAN.get(direction, ''),
        'thresholds': {
            'abs_strk_noise': ABS_STRK_NOISE,
            'pct_stake_noise': PCT_STAKE_NOISE,
        },
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(f'\n=== VECTORS ===')
    logger.info(f'  stake_delta_24h_strk: {result["vectors"]["stake_delta_24h_strk"]}   → {stake_v}')
    logger.info(f'  lp_delta_24h_strk:    {result["vectors"]["lp_delta_24h_strk"]}   → {lp_v}')
    logger.info(f'\n>>> {direction} <<<')
    logger.info(f'    {LAYMAN.get(direction, "")}')
    logger.info(f'\nSaved: {OUTPUT_FILE}')
    return 0


def _sum_ekubo(e):
    if not e:
        return {'status': 'NOT_CHECKED'}
    a = e.get('aggregate', {})
    return {
        'status': 'OK',
        'pool_count': e.get('pool_count'),
        'total_usd': a.get('total_usd_in_pools'),
        'net_usd_delta_24h': a.get('net_usd_delta_24h'),
        'net_strk_delta_24h': a.get('net_strk_delta_24h'),
        'signal': a.get('signal'),
    }


def _sum_endur(e):
    if not e:
        return {'status': 'NOT_CHECKED'}
    if e.get('status') == 'NOT_CHECKED':
        return e
    return {
        'status': 'OK',
        'tvl_strk_now': e.get('tvl_strk_now'),
        'tvl_usd_now': e.get('tvl_usd_now'),
        'apy_pct': e.get('apy_pct'),
        'strk_delta_24h': e.get('deltas', {}).get('strk_delta_24h'),
        'strk_delta_7d': e.get('deltas', {}).get('strk_delta_7d'),
        'pct_24h': e.get('deltas', {}).get('pct_24h'),
        'pct_7d': e.get('deltas', {}).get('pct_7d'),
        'signal': e.get('signal'),
    }


def _sum_native(n):
    if not n:
        return {'status': 'NOT_CHECKED'}
    if n.get('status') == 'NOT_CHECKED':
        return n
    return {
        'status': 'OK',
        'total_stake_strk': n.get('total_stake_strk_now'),
        'delta_24h': n.get('deltas', {}).get('delta_24h'),
        'delta_7d': n.get('deltas', {}).get('delta_7d'),
        'signal': n.get('signal'),
    }


if __name__ == '__main__':
    sys.exit(main())
