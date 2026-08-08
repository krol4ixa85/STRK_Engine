#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_all_collectors.py — master entry point для внешних collectors.

Не рефакторит detectors. Просто вызывает по списку с try/except.
Каждый collector падает независимо. Log 'ERR <module>: <msg>'.

По задумке:
  · flow_eth.py           → data/cache/flow_eth_edges.csv, flow_eth_summary.json
  · flow_starknet.py      → data/cache/flow_starknet_edges.csv, flow_starknet_summary.json
  · funding_history.py    → data/cache/funding_signal.json (funding_metrics)
  · unlock_calendar.py    → data/cache/event_calendar.json + unlock_signal.json
  · whale_monitor.py      → data/cache/whale_monitor_state.json + Telegram alerts
  · discord_monitor.py    → data/cache/discord_monitor_state.json (Nansen alerts)
  · starknet_discord.py   → data/cache/starknet_discord.json (skip if channel_id empty)

Composite_detector_v2 НЕ вызывается тут — он идёт отдельным step после collectors.

Возвращает всегда 0 — workflow должен продолжать. Логи в stdout GitHub Actions.
"""
import os
import sys
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
COLLECTORS_DIR = REPO_ROOT / 'scripts' / 'collectors'
DETECTORS_DIR = REPO_ROOT / 'scripts' / 'detectors'
CACHE_DIR = REPO_ROOT / 'data' / 'cache'

# Список collectors с аргументами и timeout
JOBS = [
    # (relative_path, args, timeout_sec, description)
    ('collectors/flow_eth.py',        [],                        180, 'L1 STRK flow (Etherscan)'),
    ('collectors/flow_starknet.py',   [],                        180, 'L2 STRK flow (Starkscan)'),
    ('collectors/funding_history.py', [],                        90,  'Perp funding history'),
    ('collectors/unlock_calendar.py', [],                        60,  'Unlock schedule'),
    ('collectors/whale_monitor.py',   ['--once'],                90,  'Whale alerts (single sweep)'),
    ('collectors/discord_monitor.py', ['--once'],                60,  'Nansen Discord alerts'),
]

# starknet_discord — только если channel_id настроен
if os.environ.get('STARKNET_ANNOUNCEMENTS_CHANNEL_ID'):
    JOBS.append(('detectors/starknet_discord.py', [], 60, 'Starknet Discord announcements'))
else:
    print("[skip] starknet_discord — STARKNET_ANNOUNCEMENTS_CHANNEL_ID not set")


def run_one(rel_path, args, timeout, description):
    """Run one collector. Returns True if succeeded (return code 0)."""
    full_path = REPO_ROOT / 'scripts' / rel_path
    module_name = full_path.name
    if not full_path.exists():
        print(f"[skip] {rel_path} · file not found · {description}")
        return False

    print(f"\n{'━' * 60}")
    print(f"▶ {rel_path} · {description}")
    print(f"{'━' * 60}")

    cmd = ['python3', str(full_path)] + args
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        elapsed = time.time() - start
        # Показать последние 5 строк stdout (обычно summary)
        if result.stdout:
            for line in result.stdout.rstrip().split('\n')[-8:]:
                print(f"  {line}")
        if result.returncode == 0:
            print(f"✓ OK ({elapsed:.1f}s) · {module_name}")
            return True
        else:
            print(f"✗ ERR ({elapsed:.1f}s) returncode={result.returncode} · {module_name}")
            if result.stderr:
                for line in result.stderr.rstrip().split('\n')[-5:]:
                    print(f"  ERR: {line}")
            return False
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"✗ TIMEOUT ({elapsed:.1f}s / limit {timeout}s) · {module_name}")
        return False
    except Exception as e:
        elapsed = time.time() - start
        print(f"✗ EXCEPTION ({elapsed:.1f}s) · {module_name}: {e}")
        return False


def check_output_files():
    """After all collectors, list what was written to data/cache."""
    print(f"\n{'━' * 60}")
    print(f"📁 CACHE STATE after collectors run")
    print(f"{'━' * 60}")
    if not CACHE_DIR.exists():
        print(f"  (cache dir missing: {CACHE_DIR})")
        return
    # Show key files that collectors should have written
    key_files = [
        'flow_eth_summary.json',
        'flow_starknet_summary.json',
        'funding_signal.json',
        'unlock_signal.json',
        'event_calendar.json',
        'whale_monitor_state.json',
        'discord_monitor_state.json',
        'starknet_discord.json',
    ]
    for name in key_files:
        p = CACHE_DIR / name
        if p.exists():
            size = p.stat().st_size
            mtime = p.stat().st_mtime
            age_min = (time.time() - mtime) / 60
            marker = '✓' if age_min < 60 else '⚠ stale'
            print(f"  {marker} {name:<35} {size:>7}b · {age_min:.0f}m ago")
        else:
            print(f"  ✗ {name:<35} MISSING")


def main():
    print(f"{'=' * 60}")
    print(f"STRK ENGINE · MASTER COLLECTORS RUN")
    print(f"{'=' * 60}")
    print(f"Working dir: {REPO_ROOT}")
    print(f"Collectors dir: {COLLECTORS_DIR}")
    print(f"Cache dir: {CACHE_DIR}")
    print(f"Jobs queued: {len(JOBS)}")

    ok_count = 0
    fail_count = 0
    for rel_path, args, timeout, description in JOBS:
        succeeded = run_one(rel_path, args, timeout, description)
        if succeeded:
            ok_count += 1
        else:
            fail_count += 1

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {ok_count} OK / {fail_count} FAIL / {len(JOBS)} total")
    print(f"{'=' * 60}")

    check_output_files()

    # Всегда 0 — workflow должен продолжать даже если collectors провалились
    return 0


if __name__ == '__main__':
    sys.exit(main())