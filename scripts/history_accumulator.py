#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
history_accumulator.py — Единое хранилище снапшотов всех сигналов

Каждый RUN записывает одну строку в data/history/all_history.jsonl:
  · run_id       (из env STRK_RUN_ID или timestamp fallback)
  · timestamp    (issue time)
  · price_usd    (STRK spot at snapshot, из OKX)
  · live_signals — key signals из data/cache/*.json (компактно, только критичные поля)
  · shadow_ref   — reference на shadow_votes.jsonl (run_id, aggregate_shadow signal)
                   без дублирования полных shadow_votes
  · verify_windows — [72h, 7d] для будущего history_postmortem
  · outcome_72h  — null (заполнит history_postmortem)
  · outcome_7d   — null

ПОЧЕМУ КОМПАКТНО:
При 6h cadence = 1460 записей/год. Если хранить полные JSON каждого модуля
(~15KB) — получим ~22MB/год. При компактном формате (только verdict-поля) —
1-2KB per record, ~2-3MB/год. Более чистая линия для бэктестинга.

ПОЧЕМУ НЕ ДУБЛИРОВАТЬ SHADOW:
shadow_votes.jsonl уже хранит все shadow_votes с полным breakdown.
Дублировать = два источника истины = разъедутся. Reference по run_id =
одна точка правды.

STATUS: LIVE — этот модуль сам не влияет на decision. Пишет observations.
"""
import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = HISTORY_DIR / 'all_history.jsonl'
SHADOW_FILE = HISTORY_DIR / 'shadow_votes.jsonl'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('hist_acc')


def load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        logger.warning(f'load {path.name}: {e}')
        return None


def get_price_usd():
    """Fetch STRK-USDT spot from OKX."""
    try:
        url = 'https://www.okx.com/api/v5/market/ticker?instId=STRK-USDT'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        if d.get('code') == '0' and d.get('data'):
            return float(d['data'][0]['last'])
    except Exception as e:
        logger.warning(f'OKX price: {e}')
    return None


def resolve_run_id(now):
    """
    Deterministic run_id.
    Priority:
      1. env STRK_RUN_ID (set by workflow)
      2. From composite_signal_v2.as_of (rounded to hour)
      3. Timestamp fallback
    """
    rid = os.environ.get('STRK_RUN_ID')
    if rid:
        return rid
    return 'H' + now.strftime('%Y%m%d_%H%M')


def get_last_shadow_ref():
    """Read last shadow_votes.jsonl entry (72h window) — that's our reference."""
    if not SHADOW_FILE.exists():
        return None
    try:
        last_72h = None
        with open(SHADOW_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get('window') == '72h':
                        last_72h = r
                except json.JSONDecodeError:
                    continue
        if not last_72h:
            return None
        return {
            'shadow_run_id': last_72h.get('run_id'),
            'shadow_issued_at': last_72h.get('issued_at'),
            'shadow_signal': (last_72h.get('aggregate_shadow') or {}).get('shadow_signal'),
            'shadow_rally_votes': (last_72h.get('aggregate_shadow') or {}).get('rally_votes'),
            'shadow_crash_votes': (last_72h.get('aggregate_shadow') or {}).get('crash_votes'),
        }
    except Exception as e:
        logger.warning(f'shadow ref read: {e}')
        return None


def extract_live_signals():
    """Compact extraction of critical fields from live modules."""
    signals = {}

    # composite_signal_v2 — главный сигнал КОНТУР A (старый)
    comp = load_json(CACHE_DIR / 'composite_signal_v2.json') or {}
    if comp:
        signals['composite_v2'] = {
            'direction': comp.get('direction'),
            'strength': comp.get('strength'),
            'confidence': comp.get('confidence'),
            'as_of': comp.get('as_of'),
            'btc_cycle': ((comp.get('inputs') or {}).get('btc_context') or {}).get('cycle'),
        }

    # confluence_gate — главный сигнал КОНТУР A (новый)
    conf = load_json(CACHE_DIR / 'confluence_gate.json') or {}
    if conf:
        signals['confluence_gate'] = {
            'signal': conf.get('signal'),
            'confidence': conf.get('confidence'),
            'rally_score': conf.get('rally_score'),
            'crash_score': conf.get('crash_score'),
        }

    # wyckoff_phase
    wy = load_json(CACHE_DIR / 'wyckoff_phase.json') or {}
    if wy:
        signals['wyckoff'] = {
            'phase': wy.get('phase'),
            'sub_phase': wy.get('sub_phase'),
            'confidence': wy.get('confidence'),
        }

    # scenario_analysis
    sc = load_json(CACHE_DIR / 'scenario_analysis.json') or {}
    if sc:
        scenarios = sc.get('scenarios') or {}
        signals['scenarios'] = {
            'bull_prob': (scenarios.get('bull') or {}).get('probability'),
            'base_prob': (scenarios.get('base') or {}).get('probability'),
            'bear_prob': (scenarios.get('bear') or {}).get('probability'),
        }

    # technical_momentum
    tech = load_json(CACHE_DIR / 'technical_momentum.json') or {}
    if tech:
        f = tech.get('features') or {}
        signals['technical'] = {
            'price': f.get('price'),
            'rsi': f.get('rsi'),
            'slope_3d_pct': f.get('slope_3d_pct'),
            'vol_ratio_3d_vs_30d': f.get('vol_ratio_3d_vs_30d'),
            'high_7d': f.get('high_7d'),
            'low_7d': f.get('low_7d'),
        }

    # funding
    fund = load_json(CACHE_DIR / 'funding_signal.json') or {}
    if fund:
        m = fund.get('funding_metrics') or {}
        signals['funding'] = {
            'signal': fund.get('signal'),
            'current_annualized_pct': m.get('current_annualized_pct'),
            'avg_7d_pct': m.get('avg_7d_pct'),
            'short_crowded': m.get('short_crowded'),
        }

    # cex_flow
    cex = load_json(CACHE_DIR / 'cex_flow.json') or {}
    if cex:
        signals['cex_flow'] = {
            'signal': cex.get('cex_signal') or cex.get('signal'),
            'net_7d_strk': cex.get('net_7d_strk'),
        }

    # event_layer
    ev = load_json(CACHE_DIR / 'event_layer.json') or {}
    if ev:
        signals['event_layer'] = {
            'signal': ev.get('event_signal') or ev.get('signal'),
            'bullish': ev.get('event_bullish'),
            'bearish': ev.get('event_bearish'),
        }

    # unlock signal
    unl = load_json(CACHE_DIR / 'unlock_signal.json') or {}
    if unl:
        signals['unlock'] = {
            'signal': unl.get('signal'),
            'days_to_next': unl.get('days_to_next'),
            'next_unlock_strk': unl.get('next_unlock_amount'),
        }

    return signals


def append_jsonl(record, path):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def main():
    logger.info('=' * 60)
    logger.info('HISTORY ACCUMULATOR · compact snapshot per RUN')
    logger.info('=' * 60)

    now = datetime.now(timezone.utc)
    run_id = resolve_run_id(now)
    price = get_price_usd()

    live_signals = extract_live_signals()
    shadow_ref = get_last_shadow_ref()

    logger.info(f'run_id: {run_id}')
    logger.info(f'price: ${price}' if price else 'price: unavailable')
    logger.info(f'live signals extracted: {len(live_signals)} modules')
    logger.info(f'shadow ref: {"OK" if shadow_ref else "none"}')

    record = {
        'run_id': run_id,
        'timestamp': now.isoformat(),
        'price_usd': price,
        'live_signals': live_signals,
        'shadow_ref': shadow_ref,
        'verify_windows': ['72h', '7d'],
        'verify_after_72h': (now + timedelta(hours=72)).isoformat(),
        'verify_after_7d': (now + timedelta(days=7)).isoformat(),
        'outcome_72h': None,
        'outcome_7d': None,
        'status': 'PENDING',
    }

    append_jsonl(record, HISTORY_FILE)

    try:
        total = sum(1 for _ in open(HISTORY_FILE, encoding='utf-8'))
        logger.info(f'Total all_history.jsonl records: {total}')
    except Exception:
        pass

    return 0


if __name__ == '__main__':
    sys.exit(main())
