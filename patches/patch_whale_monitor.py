#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_whale_monitor.py — Добавить 'l2_defi': 'DEFI' в CATEGORY_TO_TYPE.

Иначе whale_monitor не сможет корректно классифицировать transfers
в/из Ekubo/AVNU/Endur контрактов.

Идемпотентно. Делает бэкап.

Usage:
    python3 patch_whale_monitor.py [--dry-run]
"""
import sys, argparse, shutil
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / 'scripts' / 'collectors' / 'whale_monitor.py'

MARKER_OLD = "'l2_native': 'L2',\n}"
MARKER_NEW = "'l2_native': 'L2',\n    'l2_defi': 'DEFI',\n}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] Не найден: {TARGET}')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    if "'l2_defi'" in text and 'CATEGORY_TO_TYPE' in text:
        idx = text.find('CATEGORY_TO_TYPE')
        end = text.find('}', idx)
        if end > idx and 'l2_defi' in text[idx:end]:
            print(f'[OK] l2_defi уже в CATEGORY_TO_TYPE. Ничего не делаю.')
            return 0

    if MARKER_OLD not in text:
        print(f'[ERROR] Не нашёл маркер для замены. Файл изменён?')
        print(f'        Искал: {repr(MARKER_OLD)}')
        return 1

    new_text = text.replace(MARKER_OLD, MARKER_NEW, 1)

    print(f'File: {TARGET}')
    print(f'  Diff: добавлена строка "    \'l2_defi\': \'DEFI\'," в CATEGORY_TO_TYPE')

    if args.dry_run:
        print('\n[DRY-RUN] Ничего не записано.')
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.py.bak_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')
    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
