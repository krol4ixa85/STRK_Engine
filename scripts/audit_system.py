#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_system.py — комплексная проверка health всей STRK Engine системы.

Что проверяет:
  1. Все cache files существуют и свежие
  2. Все collectors компилируются
  3. Все detectors компилируются
  4. Data pipeline integrity (правильно ли данные текут)
  5. Методология соблюдается (Confluence Gate, veto rules)
  6. Backtest module состояние
  7. Dashboard-facing data files доступны

Output: понятный отчёт в stdout + JSON summary в data/cache/system_health.json.

Запуск:
  python3 scripts/audit_system.py          # проверить всё
  python3 scripts/audit_system.py --fix    # предложить исправления
"""
import os
import sys
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
SCRIPTS_DIR = SCRIPT_DIR / 'scripts'

# ANSI colors
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
BOLD = '\033[1m'
RESET = '\033[0m'


def _print(msg, level='info'):
    icons = {'ok': f'{GREEN}✓{RESET}', 'warn': f'{YELLOW}⚠{RESET}',
             'err': f'{RED}✗{RESET}', 'info': f'{BLUE}·{RESET}'}
    print(f'  {icons.get(level, " ")} {msg}')


def _header(text):
    print(f'\n{BOLD}{BLUE}{"=" * 60}{RESET}')
    print(f'{BOLD}{text}{RESET}')
    print(f'{BOLD}{BLUE}{"=" * 60}{RESET}')


def check_cache_file(name, path, max_age_hours=24):
    """Проверить файл и его age."""
    if not path.exists():
        return {'status': 'missing', 'age_hours': None, 'size_kb': 0}
    try:
        stat = path.stat()
        size_kb = stat.st_size / 1024
        # Try to read generated_at from JSON
        if path.suffix == '.json':
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            ts = data.get('generated_at') or data.get('collected_at')
            if ts:
                gen = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
                return {
                    'status': 'ok' if age_h < max_age_hours else 'stale',
                    'age_hours': age_h,
                    'size_kb': size_kb,
                }
        # Fallback: mtime
        age_h = (datetime.now().timestamp() - stat.st_mtime) / 3600
        return {
            'status': 'ok' if age_h < max_age_hours else 'stale',
            'age_hours': age_h,
            'size_kb': size_kb,
        }
    except Exception as e:
        return {'status': 'error', 'age_hours': None, 'size_kb': 0, 'error': str(e)}


def check_script_compiles(script_path):
    """Проверить что script compiles."""
    if not script_path.exists():
        return {'status': 'missing'}
    result = subprocess.run(
        ['python3', '-c', f'import py_compile; py_compile.compile("{script_path}", doraise=True)'],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode == 0:
        return {'status': 'ok'}
    return {'status': 'error', 'error': result.stderr[:200]}


def audit_cache_files():
    """Аудит всех cache files."""
    _header('📁 CACHE FILES · свежесть данных')

    files_to_check = [
        # (display name, path, max age hours)
        ('LAB report',            CACHE_DIR / 'strk_lab_report.json',       24),
        ('Prev LAB (для delta)',  CACHE_DIR / 'strk_lab_report_prev.json',  48),
        ('Sector momentum',       CACHE_DIR / 'dune_sector_momentum.json',  24),
        ('Sector netflow',        CACHE_DIR / 'dune_sector_netflow.json',   24),
        ('Backtest summary',      CACHE_DIR / 'lab_signals_summary.json',   48),
        ('Alt-cycle compass',     CACHE_DIR / 'alt_cycle.json',             6),
        ('DeFiLlama TVL',         CACHE_DIR / 'defillama_tvl.json',         12),
        ('Funding rates',         CACHE_DIR / 'funding_signals.json',       2),
        ('Stables signal',        CACHE_DIR / 'stables_signal.json',        12),
        ('Dune extended',         CACHE_DIR / 'dune_extended.json',         48),
        ('Weekly summary',        CACHE_DIR / 'weekly_summary.json',        168),
        ('Rotation state',        CACHE_DIR / 'rotation_tracker_state.json', 48),
    ]

    results = {}
    for name, path, max_age in files_to_check:
        r = check_cache_file(name, path, max_age)
        results[name] = {**r, 'path': str(path.relative_to(SCRIPT_DIR))}

        if r['status'] == 'missing':
            _print(f'{name}: MISSING · {path.name}', 'err')
        elif r['status'] == 'error':
            _print(f'{name}: ERROR — {r.get("error", "?")}', 'err')
        elif r['status'] == 'stale':
            _print(f'{name}: STALE · {r["age_hours"]:.1f}h old (max {max_age}h) · {r["size_kb"]:.1f}KB', 'warn')
        else:
            _print(f'{name}: OK · {r["age_hours"]:.1f}h old · {r["size_kb"]:.1f}KB', 'ok')

    return results


def audit_history_files():
    """Аудит history/append-only files."""
    _header('📜 HISTORY FILES · JSONL append-only')

    files_to_check = [
        ('LAB signals log', HISTORY_DIR / 'lab_signals.jsonl'),
        ('Rotation alerts', HISTORY_DIR / 'rotation_alerts.jsonl'),
        ('Cohort snapshots', HISTORY_DIR / 'cohort_snapshots.jsonl'),
        ('Shadow votes', HISTORY_DIR / 'shadow_votes.jsonl'),
    ]

    results = {}
    for name, path in files_to_check:
        if not path.exists():
            _print(f'{name}: NOT YET · {path.name}', 'warn')
            results[name] = {'status': 'missing', 'lines': 0}
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = sum(1 for line in f if line.strip())
            size_kb = path.stat().st_size / 1024
            _print(f'{name}: {lines} records · {size_kb:.1f}KB', 'ok')
            results[name] = {'status': 'ok', 'lines': lines, 'size_kb': size_kb}
        except Exception as e:
            _print(f'{name}: ERROR — {e}', 'err')
            results[name] = {'status': 'error', 'error': str(e)}
    return results


def audit_scripts_compile():
    """Проверить что все ключевые scripts компилируются."""
    _header('🔧 SCRIPTS · синтаксическая проверка')

    scripts_to_check = [
        ('daily_digest.py', SCRIPTS_DIR / 'daily_digest.py'),
        ('strk_lab.py', SCRIPTS_DIR / 'strk_lab.py'),
        ('weekly_summary.py', SCRIPTS_DIR / 'weekly_summary.py'),
        ('collectors/dune_collector.py', SCRIPTS_DIR / 'collectors' / 'dune_collector.py'),
        ('collectors/dune_sector_collector.py', SCRIPTS_DIR / 'collectors' / 'dune_sector_collector.py'),
        ('collectors/alt_cycle_collector.py', SCRIPTS_DIR / 'collectors' / 'alt_cycle_collector.py'),
        ('collectors/defillama_collector.py', SCRIPTS_DIR / 'collectors' / 'defillama_collector.py'),
        ('collectors/funding_collector.py', SCRIPTS_DIR / 'collectors' / 'funding_collector.py'),
        ('collectors/stables_collector.py', SCRIPTS_DIR / 'collectors' / 'stables_collector.py'),
        ('collectors/dune_extended_collector.py', SCRIPTS_DIR / 'collectors' / 'dune_extended_collector.py'),
        ('detectors/rotation_tracker.py', SCRIPTS_DIR / 'detectors' / 'rotation_tracker.py'),
        ('detectors/lab_signals_recorder.py', SCRIPTS_DIR / 'detectors' / 'lab_signals_recorder.py'),
        ('detectors/lab_signals_verifier.py', SCRIPTS_DIR / 'detectors' / 'lab_signals_verifier.py'),
    ]

    results = {}
    for name, path in scripts_to_check:
        r = check_script_compiles(path)
        results[name] = r
        if r['status'] == 'missing':
            _print(f'{name}: MISSING', 'err')
        elif r['status'] == 'error':
            _print(f'{name}: COMPILE ERROR — {r["error"][:100]}', 'err')
        else:
            _print(f'{name}: OK', 'ok')
    return results


def audit_pipeline_integrity():
    """Проверить что данные правильно текут через pipeline."""
    _header('🔗 PIPELINE INTEGRITY · data flow checks')

    issues = []

    # Check 1: strk_lab_report.json contains expected keys
    lab_path = CACHE_DIR / 'strk_lab_report.json'
    if lab_path.exists():
        try:
            with open(lab_path) as f:
                lab = json.load(f)
            required = ['strk_status', 're_entry_triggers', 'strong_buy']
            missing = [k for k in required if k not in lab]
            if missing:
                _print(f'LAB report missing keys: {missing}', 'err')
                issues.append(f'strk_lab_report missing {missing}')
            else:
                _print(f'LAB report structure: OK · {len(lab.get("strong_buy", []))} strong_buy tokens', 'ok')
        except Exception as e:
            _print(f'LAB report read error: {e}', 'err')
            issues.append(f'strk_lab_report unreadable')

    # Check 2: rotation_tracker_state present (for delta comparison)
    state_path = CACHE_DIR / 'rotation_tracker_state.json'
    if state_path.exists():
        _print('Rotation tracker state: present (deltas work)', 'ok')
    else:
        _print('Rotation tracker state: missing (first run behaviour)', 'warn')

    # Check 3: alt_cycle contains phase info
    alt_path = CACHE_DIR / 'alt_cycle.json'
    if alt_path.exists():
        try:
            with open(alt_path) as f:
                alt = json.load(f)
            phase = alt.get('phase', {}).get('phase')
            if phase:
                _print(f'Alt-cycle phase detected: {phase}', 'ok')
            else:
                _print('Alt-cycle missing phase field', 'err')
                issues.append('alt_cycle no phase')
        except Exception as e:
            _print(f'Alt-cycle read error: {e}', 'err')

    # Check 4: backtest module state
    bt_path = CACHE_DIR / 'lab_signals_summary.json'
    signals_path = HISTORY_DIR / 'lab_signals.jsonl'
    if bt_path.exists():
        try:
            with open(bt_path) as f:
                bt = json.load(f)
            n_actionable = bt.get('overall', {}).get('n_actionable', 0)
            n_closed = bt.get('overall', {}).get('n_closed', 0)
            _print(f'Backtest: {n_actionable} actionable, {n_closed} closed', 'ok')
            if n_actionable < 5:
                _print(f'  → Precision появится когда N >= 5 (сейчас {n_actionable})', 'info')
        except Exception as e:
            _print(f'Backtest read error: {e}', 'warn')
    elif signals_path.exists():
        with open(signals_path) as f:
            signal_count = sum(1 for _ in f if _.strip())
        _print(f'Backtest summary не сгенерирован, но signals записаны: {signal_count}', 'warn')
        _print(f'  → Запусти lab_signals_verifier.py вручную', 'info')

    return {'issues': issues}


def audit_methodology_compliance():
    """Проверить соблюдение методологии."""
    _header('📋 METHODOLOGY COMPLIANCE · rules & guards')

    checks = []

    # Check 1: on-chain veto rule присутствует в daily_digest.py
    digest_path = SCRIPTS_DIR / 'daily_digest.py'
    if digest_path.exists():
        content = digest_path.read_text(encoding='utf-8')
        if 'onchain_veto' in content and 'ON-CHAIN VETO RULE' in content:
            _print('On-chain veto rule: present in daily_digest.py', 'ok')
            checks.append({'check': 'onchain_veto', 'status': 'ok'})
        else:
            _print('On-chain veto rule: NOT FOUND', 'err')
            checks.append({'check': 'onchain_veto', 'status': 'missing'})

    # Check 2: purged walk-forward в verifier
    verifier_path = SCRIPTS_DIR / 'detectors' / 'lab_signals_verifier.py'
    if verifier_path.exists():
        content = verifier_path.read_text(encoding='utf-8')
        if 'purged_walk_forward_precision' in content:
            _print('Purged walk-forward validation: present', 'ok')
            checks.append({'check': 'purged_walk_forward', 'status': 'ok'})
        else:
            _print('Purged walk-forward: NOT FOUND', 'err')
            checks.append({'check': 'purged_walk_forward', 'status': 'missing'})

    # Check 3: liquidity floor = 5000 в rotation_tracker
    tracker_path = SCRIPTS_DIR / 'detectors' / 'rotation_tracker.py'
    if tracker_path.exists():
        content = tracker_path.read_text(encoding='utf-8')
        if 'tx_count' in content and ('5000' in content or '5_000' in content):
            _print('Liquidity floor (5000 tx): configured', 'ok')
            checks.append({'check': 'liquidity_floor', 'status': 'ok'})
        else:
            _print('Liquidity floor: check manually', 'warn')

    # Check 4: dedup by token
    if tracker_path.exists():
        content = tracker_path.read_text(encoding='utf-8')
        if 'seen' in content and 'add' in content:
            _print('Token dedup logic: present', 'ok')
            checks.append({'check': 'dedup', 'status': 'ok'})

    # Check 5: playbook module present
    if tracker_path.exists():
        content = tracker_path.read_text(encoding='utf-8')
        if 'def build_playbook' in content and 'CONSERVATIVE' in content:
            _print('Playbook 3-variant logic: present', 'ok')
            checks.append({'check': 'playbook', 'status': 'ok'})
        else:
            _print('Playbook logic: MISSING', 'err')

    return {'checks': checks}


def audit_data_source_coverage():
    """Проверить какие data sources реально работают."""
    _header('🌐 DATA SOURCES · what really works')

    sources = {
        'Dune (STRK)':        CACHE_DIR / 'dune_daily.json',
        'Dune (sector)':      CACHE_DIR / 'dune_sector_momentum.json',
        'CoinGecko (macro)':  CACHE_DIR / 'alt_cycle.json',
        'CoinGecko (funding)':CACHE_DIR / 'funding_signals.json',
        'CoinGecko (stables)':CACHE_DIR / 'stables_signal.json',
        'DeFiLlama (TVL)':    CACHE_DIR / 'defillama_tvl.json',
        'Dune extended':      CACHE_DIR / 'dune_extended.json',
    }

    working = 0
    for name, path in sources.items():
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                # Check that it has data (not empty stub)
                has_data = bool(
                    data.get('rows') or data.get('metrics') or data.get('strong_buy') or
                    data.get('chains_current') or data.get('funding_rates') or
                    data.get('results') or data.get('signal') or data.get('total_stables_marketcap_usd')
                )
                if has_data:
                    _print(f'{name}: working (has data)', 'ok')
                    working += 1
                else:
                    _print(f'{name}: file exists but empty', 'warn')
            except Exception as e:
                _print(f'{name}: read error ({e})', 'err')
        else:
            _print(f'{name}: not yet collected', 'warn')

    _print(f'\n{working}/{len(sources)} data sources reporting data', 'info')
    return {'working': working, 'total': len(sources)}


def main():
    print(f'\n{BOLD}STRK Engine · System Health Audit{RESET}')
    print(f'{BOLD}Run at: {datetime.now(timezone.utc).isoformat()}{RESET}')

    # Run all audits
    cache = audit_cache_files()
    history = audit_history_files()
    scripts = audit_scripts_compile()
    pipeline = audit_pipeline_integrity()
    methodology = audit_methodology_compliance()
    sources = audit_data_source_coverage()

    # Summary
    _header('📊 SUMMARY · overall health')

    cache_ok = sum(1 for v in cache.values() if v['status'] == 'ok')
    scripts_ok = sum(1 for v in scripts.values() if v['status'] == 'ok')
    method_ok = sum(1 for c in methodology['checks'] if c['status'] == 'ok')

    print(f'  Cache files:       {GREEN}{cache_ok}{RESET}/{len(cache)} healthy')
    print(f'  Scripts:           {GREEN}{scripts_ok}{RESET}/{len(scripts)} compile')
    print(f'  Methodology:       {GREEN}{method_ok}{RESET}/{len(methodology["checks"])} rules present')
    print(f'  Data sources:      {GREEN}{sources["working"]}{RESET}/{sources["total"]} reporting data')

    # Save summary JSON
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'cache_files': cache,
        'history_files': history,
        'scripts_compile': scripts,
        'pipeline_issues': pipeline.get('issues', []),
        'methodology_checks': methodology['checks'],
        'data_sources_working': sources['working'],
        'data_sources_total': sources['total'],
        'summary': {
            'cache_ok': cache_ok,
            'cache_total': len(cache),
            'scripts_ok': scripts_ok,
            'scripts_total': len(scripts),
            'methodology_ok': method_ok,
            'sources_ok': sources['working'],
        },
    }

    output_path = CACHE_DIR / 'system_health.json'
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n  Saved to {output_path.name}')

    return 0


if __name__ == '__main__':
    sys.exit(main())