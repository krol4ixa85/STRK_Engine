#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_wallet_registry.py — Добавить категорию 'l2_defi' в VALID_CATEGORIES.

Иначе wallet_registry.py add ... l2_defi отклонит новую категорию.

Идемпотентно. Делает бэкап рядом с исходным файлом.

Usage:
    python3 patch_wallet_registry.py [--dry-run]
"""
import sys, argparse, shutil
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / 'scripts' / 'wallet_registry.py'

MARKER_OLD = "'watchlist',\n]"
MARKER_NEW = "'watchlist',\n    'l2_defi',\n]"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] Не найден: {TARGET}')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    if "'l2_defi'" in text and 'VALID_CATEGORIES' in text:
        # уже присутствует
        # проверим что именно в VALID_CATEGORIES
        idx = text.find('VALID_CATEGORIES')
        end = text.find(']', idx)
        if end > idx and 'l2_defi' in text[idx:end]:
            print(f'[OK] l2_defi уже в VALID_CATEGORIES. Ничего не делаю.')
            return 0

    if MARKER_OLD not in text:
        print(f'[ERROR] Не нашёл маркер для замены. Файл изменён?')
        print(f'        Искал: {repr(MARKER_OLD[:60])}...')
        return 1

    if text.count(MARKER_OLD) > 1:
        print(f'[WARN] Маркер встречается более одного раза — потенциально небезопасно.')

    new_text = text.replace(MARKER_OLD, MARKER_NEW, 1)

    print(f'File: {TARGET}')
    print(f'  Diff: добавлена строка "    \'l2_defi\'," в VALID_CATEGORIES')

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
