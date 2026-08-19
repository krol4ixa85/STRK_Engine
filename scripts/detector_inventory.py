#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detector_inventory.py — полная инвентаризация всех detectors.

Что делает:
  1. Сканирует ВСЕ cache файлы (data/cache/*.json)
  2. Для каждого detector — вытаскивает последний signal / verdict
  3. Классифицирует: 
     · SHOWN — сигнал показывается на dashboard
     · HIDDEN — сигнал работает но не отображается
     · STALE — данные старше 24h
     · BROKEN — файл существует но нет полезных данных
  4. Даёт actionable recommendations что добавить в dashboard

Cost: $0 (только чтение локальных файлов).
"""
import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'

# Detectors registry — что известно + мы показываем ли на dashboard
DETECTORS = {
    # === STRK-specific composite ===
    'composite_signal_v2': {
        'file': 'composite_signal_v2.json',
        'signal_keys': ['signal', 'confidence'],
        'dashboard': 'PARTIAL',  # верхняя карта, но не полностью
        'description': 'Overall STRK verdict (BULLISH/BEARISH + confidence)',
    },
    'wyckoff_phase': {
        'file': 'wyckoff_phase.json',
        'signal_keys': ['phase', 'sub_phase', 'confidence'],
        'dashboard': 'SHOWN',
        'description': 'Wyckoff market cycle phase',
    },
    'technical_momentum': {
        'file': 'technical_momentum.json',
        'signal_keys': ['classification.signal'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'TA-based BULLISH/BEARISH momentum',
    },
    'cvd_analysis': {
        'file': 'cvd_analysis.json',
        'signal_keys': ['signal'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'Cumulative Volume Delta order flow',
    },
    'cex_flow': {
        'file': 'cex_flow.json',
        'signal_keys': ['signal'],
        'dashboard': 'SHOWN',  # в детали STRK
        'description': 'Exchange in/out flow trend',
    },
    'effort_result': {
        'file': 'effort_result.json',
        'signal_keys': ['signal'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'Volume effort vs price result (Wyckoff)',
    },
    'concentration_metrics': {
        'file': 'concentration_metrics.json',
        'signal_keys': ['top5_share', 'hhi'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'Whale concentration + Herfindahl index',
    },
    'confluence_gate': {
        'file': 'confluence_gate.json',
        'signal_keys': ['gate_status', 'rally_score', 'crash_score'],
        'dashboard': 'HIDDEN',  # КРИТИЧНО ← не показывается!
        'description': 'Compact BUY/SELL/WAIT gate (9 factors)',
    },
    'cross_window_pattern': {
        'file': 'cross_window_pattern.json',
        'signal_keys': ['pattern', 'confidence'],
        'dashboard': 'HIDDEN',
        'description': 'Multi-timeframe pattern detection',
    },
    'covert_flow_signal': {
        'file': 'covert_flow_signal.json',
        'signal_keys': ['covert_signal', 'confidence'],
        'dashboard': 'HIDDEN',
        'description': 'Detects hidden accumulation/distribution',
    },
    'liquidity_shift': {
        'file': 'liquidity_shift.json',
        'signal_keys': ['signal'],
        'dashboard': 'HIDDEN',
        'description': 'Liquidity distribution changes',
    },
    'cross_token_correlation': {
        'file': 'cross_token_correlation.json',
        'signal_keys': ['cross_token_signal', 'cross_token_alpha_7d_pct'],
        'dashboard': 'HIDDEN',
        'description': 'STRK vs peers correlation (RELATIVE_STRENGTH/IN_LINE/UNDERPERFORM)',
    },
    'scenario_analysis': {
        'file': 'scenario_analysis.json',
        'signal_keys': ['likely_scenario', 'p_bull', 'p_bear'],
        'dashboard': 'HIDDEN',
        'description': 'Bull/bear/neutral scenario probabilities',
    },

    # === Event/News ===
    'event_layer': {
        'file': 'event_layer.json',
        'signal_keys': ['aggregate_signal', 'bull_score', 'bear_score'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'Aggregated news + events + Discord signal',
    },
    'news_aggregator': {
        'file': 'news_aggregator.json',
        'signal_keys': ['overall_signal', 'strk_news_count', 'recent_news'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'RSS news feed (CoinDesk, CoinTelegraph, Decrypt, etc)',
    },
    'twitter_nitter': {
        'file': 'twitter_nitter.json',
        'signal_keys': ['twitter_signal', 'positive_count', 'negative_count'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'Twitter/Nitter sentiment для Starknet',
    },
    'event_calendar': {
        'file': 'event_calendar.json',
        'signal_keys': ['upcoming_events', 'next_event'],
        'dashboard': 'HIDDEN',
        'description': 'Upcoming unlocks, upgrades, announcements',
    },

    # === Flow ===
    'ekubo_flow': {
        'file': 'ekubo_flow.json',
        'signal_keys': ['signal'],
        'dashboard': 'HIDDEN',
        'description': 'Ekubo DEX pool flow (Starknet)',
    },
    'endur_lst_flow': {
        'file': 'endur_lst_flow.json',
        'signal_keys': ['signal'],
        'dashboard': 'HIDDEN',
        'description': 'Endur LST (liquid staking) flow',
    },
    'native_staking_flow': {
        'file': 'native_staking_flow.json',
        'signal_keys': ['signal'],
        'dashboard': 'HIDDEN',
        'description': 'Native STRK staking flow',
    },
    'bridge_activity': {
        'file': 'bridge_activity.json',
        'signal_keys': ['signal', 'net_flow'],
        'dashboard': 'HIDDEN',
        'description': 'L1↔L2 bridge activity',
    },

    # === Sector-level (LAB) ===
    'dune_sector_momentum': {
        'file': 'dune_sector_momentum.json',
        'signal_keys': ['rows'],
        'dashboard': 'SHOWN',  # активные сигналы table
        'description': 'Sector-level momentum по 37 tokens',
    },
    'dune_sector_netflow': {
        'file': 'dune_sector_netflow.json',
        'signal_keys': ['rows'],
        'dashboard': 'HIDDEN',  # ← не показывается!
        'description': 'Aggregated netflow BY SECTOR (L2, DeFi, LST, RWA, etc)',
    },
    'strk_lab_report': {
        'file': 'strk_lab_report.json',
        'signal_keys': ['strk_status.verdict'],
        'dashboard': 'SHOWN',
        'description': 'STRK LAB verdict + triggers',
    },
    'rotation_tracker_state': {
        'file': 'rotation_tracker_state.json',
        'signal_keys': ['current_strong_buy', 'streaks'],
        'dashboard': 'PARTIAL',
        'description': 'Rotation state + streaks',
    },

    # === Free API sources ===
    'alt_cycle': {
        'file': 'alt_cycle.json',
        'signal_keys': ['phase.phase', 'phase.phase_num'],
        'dashboard': 'SHOWN',
        'description': 'Alt-cycle phase (CoinGecko)',
    },
    'defillama_tvl': {
        'file': 'defillama_tvl.json',
        'signal_keys': ['cross_chain_flow.leader'],
        'dashboard': 'SHOWN',
        'description': 'TVL by chains (DeFiLlama)',
    },
    'funding_signals': {
        'file': 'funding_signals.json',
        'signal_keys': ['extreme_funding'],
        'dashboard': 'SHOWN',
        'description': 'Funding rates per token',
    },
    'stables_signal': {
        'file': 'stables_signal.json',
        'signal_keys': ['signal', 'stables_dominance_pct'],
        'dashboard': 'SHOWN',
        'description': 'Stables dry powder',
    },
    'whale_holdings_state': {
        'file': 'whale_holdings_state.json',
        'signal_keys': ['STRK.last_status'],
        'dashboard': 'PARTIAL',
        'description': 'Whale holdings tracking state',
    },

    # === Whale monitor (existing) ===
    'whale_monitor_state': {
        'file': 'whale_monitor_state.json',
        'signal_keys': ['recent_events'],
        'dashboard': 'HIDDEN',
        'description': 'Wallet-level whale monitoring (Etherscan)',
    },
    'squeeze_state': {
        'file': 'squeeze_state.json',
        'signal_keys': ['squeeze_detected'],
        'dashboard': 'HIDDEN',
        'description': 'Squeeze detection (short/long)',
    },
}


def get_nested(d, key):
    """Get value from dict by dot-notation key."""
    keys = key.split('.')
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    return d


def check_detector(det_name, det_meta):
    """Check status of one detector."""
    file_path = CACHE_DIR / det_meta['file']
    if not file_path.exists():
        return {'status': 'MISSING', 'signals': {}, 'age_hours': None}

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return {'status': 'ERROR', 'error': str(e), 'signals': {}, 'age_hours': None}

    # Get age
    ts_str = data.get('generated_at') or data.get('as_of') or data.get('collected_at') or data.get('ts')
    age_h = None
    if ts_str:
        try:
            gen = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
            age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        except Exception:
            pass

    # Extract signals
    signals = {}
    for key in det_meta['signal_keys']:
        val = get_nested(data, key)
        if val is not None:
            # Truncate long values
            if isinstance(val, (list, dict)):
                val = f'{type(val).__name__}({len(val)} items)'
            elif isinstance(val, str) and len(val) > 40:
                val = val[:40] + '...'
            signals[key] = val

    status = 'ACTIVE' if signals else 'BROKEN'
    if age_h and age_h > 48:
        status = 'STALE'

    return {'status': status, 'signals': signals, 'age_hours': age_h}


def main():
    print('=' * 70)
    print('DETECTOR INVENTORY · STRK Engine')
    print('=' * 70)
    print(f'Run at: {datetime.now(timezone.utc).isoformat()}\n')

    # Categorize
    shown = []
    hidden = []
    stale = []
    broken = []
    missing = []

    for det_name, det_meta in DETECTORS.items():
        check = check_detector(det_name, det_meta)
        entry = {**det_meta, 'name': det_name, **check}

        if check['status'] == 'MISSING':
            missing.append(entry)
        elif check['status'] == 'BROKEN' or check['status'] == 'ERROR':
            broken.append(entry)
        elif check['status'] == 'STALE':
            stale.append(entry)
        elif det_meta['dashboard'] in ('HIDDEN', 'PARTIAL'):
            hidden.append(entry)
        else:
            shown.append(entry)

    # Report
    print(f'✅ SHOWN on dashboard ({len(shown)})')
    print('-' * 70)
    for e in shown:
        age = f'{e["age_hours"]:.1f}h' if e["age_hours"] is not None else 'n/a'
        print(f'  {e["name"]:<28} [{age}] {list(e["signals"].values())[:2]}')

    print(f'\n⚠ HIDDEN but WORKING ({len(hidden)}) — что можно добавить на dashboard')
    print('-' * 70)
    for e in hidden:
        age = f'{e["age_hours"]:.1f}h' if e["age_hours"] is not None else 'n/a'
        sig_summary = ' · '.join([f'{k}={v}' for k, v in list(e['signals'].items())[:2]])
        print(f'  {e["name"]:<28} [{age}] {sig_summary}')
        print(f'    └─ {e["description"]}')

    if stale:
        print(f'\n💤 STALE ({len(stale)}) — данные >48h old')
        print('-' * 70)
        for e in stale:
            print(f'  {e["name"]:<28} [{e["age_hours"]:.1f}h] {e["description"]}')

    if broken:
        print(f'\n🔴 BROKEN ({len(broken)}) — файл есть но данных нет')
        print('-' * 70)
        for e in broken:
            print(f'  {e["name"]:<28} {e["description"]}')

    if missing:
        print(f'\n❓ MISSING ({len(missing)}) — файл не создан')
        print('-' * 70)
        for e in missing:
            print(f'  {e["name"]:<28} {e["description"]}')

    # Summary
    print('\n' + '=' * 70)
    print('RECOMMENDATIONS')
    print('=' * 70)
    print(f'  Total detectors: {len(DETECTORS)}')
    print(f'  Working: {len(shown) + len(hidden)}')
    print(f'  Shown on dashboard: {len(shown)}')
    print(f'  Hidden (data available но не показывается): {len(hidden)}')
    print(f'  Coverage: {len(shown) / len(DETECTORS) * 100:.0f}%')
    print()
    print(f'  🎯 Приоритет добавить на dashboard:')
    priority_add = [e for e in hidden if any(
        kw in e['description'].lower()
        for kw in ['confluence', 'sector', 'news', 'twitter', 'event', 'scenario']
    )]
    for e in priority_add[:5]:
        print(f'    · {e["name"]}: {e["description"]}')

    # Save to JSON
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total': len(DETECTORS),
        'shown': [e['name'] for e in shown],
        'hidden': [{'name': e['name'], 'signals': e['signals']} for e in hidden],
        'stale': [e['name'] for e in stale],
        'broken': [e['name'] for e in broken],
        'missing': [e['name'] for e in missing],
        'summary': {
            'coverage_pct': round(len(shown) / len(DETECTORS) * 100),
            'working_total': len(shown) + len(hidden),
            'hidden_count': len(hidden),
        },
    }
    output_path = CACHE_DIR / 'detector_inventory.json'
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n  Saved: {output_path.name}')

    return 0


if __name__ == '__main__':
    sys.exit(main())