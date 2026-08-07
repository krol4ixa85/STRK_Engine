#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_voter_config.py — Расширить voter_config.json

Добавляет:
  1. В _meta.covert_flow_detector_params — пороги детектора (HYPOTHESIS)
  2. В voters.covert_flow — 6-й shadow voter (auto-подхватится shadow_voter.py)

Идемпотентно.
"""
import sys, argparse, shutil, json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / 'config' / 'voter_config.json'


COVERT_FLOW_DETECTOR_PARAMS = {
    "_status": "HYPOTHESIS",
    "_note": "Пороги детектора covert_flow. Меняются одновременно = calibration reset.",
    "min_inflow_outflow_ratio_accumulation": 1.5,
    "min_retention_pct_accumulation": 70.0,
    "min_unique_counterparties_accumulation": 3,
    "min_outflow_inflow_ratio_distribution": 1.5,
    "min_unique_counterparties_distribution": 3,
    "min_absolute_flow_strk": 100000.0,
    "aggregate_strong_multiplier": 2.0,
    "aggregate_strong_min_count": 3
}


COVERT_FLOW_VOTER = {
    "source_file": "data/cache/covert_flow_signal.json",
    "read_path": "overall_signal",
    "rally_values": ["STRONG_ACCUMULATION", "ACCUMULATION"],
    "crash_values": ["STRONG_DISTRIBUTION", "DISTRIBUTION"],
    "neutral_values": ["NEUTRAL", "UNKNOWN"],
    "rationale": "Скрытые паттерны накопления/распределения по seed-адресам (retention + counterparties). Читает edges CSV из orchestrator."
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] {TARGET} не найден. Сначала установи shadow_voter_v1.')
        return 1

    try:
        cfg = json.loads(TARGET.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'[ERROR] Не могу распарсить {TARGET}: {e}')
        return 1

    meta = cfg.setdefault('_meta', {})
    voters = cfg.setdefault('voters', {})

    changes = []
    already_params = 'covert_flow_detector_params' in meta
    already_voter = 'covert_flow' in voters

    if already_params and already_voter:
        print('[OK] covert_flow уже в voter_config.json. Skip.')
        return 0

    if not already_params:
        meta['covert_flow_detector_params'] = COVERT_FLOW_DETECTOR_PARAMS
        changes.append('_meta.covert_flow_detector_params')

    if not already_voter:
        voters['covert_flow'] = COVERT_FLOW_VOTER
        changes.append('voters.covert_flow')

    print(f'File: {TARGET}')
    for c in changes:
        print(f'  · {c}')

    if args.dry_run:
        print('[DRY-RUN] Не записано.')
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.json.bak_covert_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')

    TARGET.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                      encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
