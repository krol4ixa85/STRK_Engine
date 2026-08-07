#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_gitignore_history.py — allow all_history.jsonl + covert_flow_signal.json в git

Идемпотентно.
"""
import sys, argparse, shutil
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / '.gitignore'

MARKER_BEGIN = '# === STRK Engine · covert flow + history layer ==='
MARKER_END = '# === End covert flow + history block ==='

NEW_SECTION = f'''
{MARKER_BEGIN}
!data/history/all_history.jsonl
!data/cache/covert_flow_signal.json
{MARKER_END}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    text = TARGET.read_text(encoding='utf-8') if TARGET.exists() else ''

    if MARKER_BEGIN in text:
        print('[OK] .gitignore уже содержит covert+history блок. Skip.')
        return 0

    new_text = text.rstrip() + '\n' + NEW_SECTION

    print(f'File: {TARGET}')
    print('  · negation patterns для all_history.jsonl + covert_flow_signal.json')

    if args.dry_run:
        return 0

    if TARGET.exists():
        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        backup = TARGET.with_suffix(f'.bak_hist_{ts}')
        shutil.copy(TARGET, backup)
        print(f'  Backup: {backup}')

    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
