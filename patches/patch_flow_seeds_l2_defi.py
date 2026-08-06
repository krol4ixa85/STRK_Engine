#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_flow_seeds_l2_defi.py — Идемпотентный мерж категории l2_defi в flow_seeds.json.

Добавляет:
  · ekubo_core         (Ekubo Protocol Core singleton)
  · ekubo_positions    (Ekubo Positions NFT-контракт)
  · avnu_exchange      (AVNU Aggregator Exchange)
  · endur_xstrk        (Endur xSTRK Liquid Staking Token)

Существующие записи в l2_defi не перезаписываются (idempotent).

Usage:
    python3 patch_flow_seeds_l2_defi.py [--dry-run]

Делает бэкап flow_seeds.json перед изменением в data/seeds/backups/.
"""
import os, sys, json, argparse, shutil
from datetime import datetime, timezone
from pathlib import Path

# Определить корень репо: файл лежит в patches/, значит корень на уровень выше
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()

SEEDS_FILE = REPO_ROOT / 'data' / 'seeds' / 'flow_seeds.json'
BACKUP_DIR = REPO_ROOT / 'data' / 'seeds' / 'backups'

L2_DEFI = {
    "_note": "L2 DeFi контракты для отслеживания liquidity shift (Ekubo/AVNU/Endur LST). Добавлено 06.08.2026 в рамках MUST #20.",
    "ekubo_core": {
        "address": "0x00000005dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b",
        "role": "Ekubo Protocol Core (singleton, все STRK-пулы в одном контракте)",
        "importance": "critical",
        "watch_for": "net TVL delta в STRK-парах (через prod-api.ekubo.org /overview/pairs)",
        "docs": "https://docs.ekubo.org/reference/contracts/starknet"
    },
    "ekubo_positions": {
        "address": "0x02e0af29598b407c8716b17f6d2795eca1b471413fa03fb145a5e33722184067",
        "role": "Ekubo Positions NFT-контракт",
        "importance": "medium",
        "watch_for": "LP-позиции (для будущего анализа концентрации LP)"
    },
    "avnu_exchange": {
        "address": "0x04270219d365d6b017231b52e92b3fb5d7c8378b05e9abc97724537a80e93b0f",
        "role": "AVNU Aggregator Exchange",
        "importance": "medium",
        "note": "AVNU роутит через Ekubo/JediSwap/Nostra — отдельного TVL не имеет. Регистрируется для полноты, не отслеживается в liquidity_shift.",
        "watch_for": "объём свопов через AVNU-аггрегатор (будущий модуль)"
    },
    "endur_xstrk": {
        "address": "0x28d709c875c0ceac3dce7065bec5328186dc89fe254527084d1689910954b0a",
        "role": "Endur xSTRK (Liquid Staking Token, ERC-4626 vault)",
        "importance": "critical",
        "watch_for": "net mint/redeem = LST-flow. Источники: api.llama.fi/protocol/endur (STRK-count history), app.endur.fi/api/stats (live tvlStrk, APY)",
        "docs": "https://blog.endur.fi/what-is-staking-on-starknet"
    }
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not SEEDS_FILE.exists():
        print(f'[ERROR] flow_seeds.json не найден: {SEEDS_FILE}')
        return 1

    seeds = json.loads(SEEDS_FILE.read_text(encoding='utf-8'))

    existing = seeds.get('l2_defi', {})
    added = []
    skipped = []

    if 'l2_defi' not in seeds:
        seeds['l2_defi'] = {}

    for key, val in L2_DEFI.items():
        if key in seeds['l2_defi']:
            skipped.append(key)
        else:
            seeds['l2_defi'][key] = val
            added.append(key)

    # обновить _meta
    seeds.setdefault('_meta', {})
    seeds['_meta']['last_modified'] = datetime.now(timezone.utc).isoformat()

    print(f'flow_seeds.json: {SEEDS_FILE}')
    print(f'  Existing l2_defi entries: {len(existing)}')
    print(f'  Added:   {len(added)} → {added}')
    print(f'  Skipped: {len(skipped)} → {skipped}')

    if args.dry_run:
        print('\n[DRY-RUN] Ничего не записано. Убери --dry-run для реального мержа.')
        return 0

    if not added:
        print('\nВсё уже на месте. Файл не тронут.')
        return 0

    # бэкап
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = BACKUP_DIR / f'flow_seeds_{ts}_pre_l2_defi.json'
    shutil.copy(SEEDS_FILE, backup)
    print(f'\n  Backup: {backup}')

    # запись
    SEEDS_FILE.write_text(
        json.dumps(seeds, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f'  Written: {SEEDS_FILE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
