#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_workflow_shadow.py — Правки .github/workflows/main.yml

Добавляет:
  1. Step: Shadow postmortem (перед shadow_voter — закрывает старые PENDING)
  2. Step: Shadow voter (после confluence_gate — пишет новые votes)
  3. shadow_votes.jsonl в блок commit
  4. shadow_votes.jsonl + shadow_calibration.json в Upload results

Идемпотентно.
"""
import sys, argparse, shutil
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / '.github' / 'workflows' / 'main.yml'


# ────────────────────────────────────────────────────────────
# 1. Найти строку "Compute Confluence Gate" и вставить после неё
#    два новых step: shadow_postmortem + shadow_voter
# ────────────────────────────────────────────────────────────
STEP_CONFLUENCE_MARKER = """      - name: Compute Confluence Gate (multi-signal decision)
        run: python3 scripts/detectors/confluence_gate.py || true"""

STEP_CONFLUENCE_NEW = """      - name: Compute Confluence Gate (multi-signal decision)
        run: python3 scripts/detectors/confluence_gate.py || true
      
      # === SHADOW LAYER (STATUS: HYPOTHESIS, does NOT affect decisions) ===
      - name: Shadow postmortem — close PENDING shadow forecasts
        run: python3 scripts/detectors/shadow_postmortem.py || true
      
      - name: Shadow voter — write new shadow votes (72h + 7d)
        env:
          STRK_RUN_ID: shadow_${{ github.run_id }}
        run: python3 scripts/detectors/shadow_voter.py || true
      # === END SHADOW LAYER ==="""


# ────────────────────────────────────────────────────────────
# 2. Расширить commit блок — добавить shadow_votes.jsonl
# Ищем строку с data/history/postmortems.jsonl и добавляем shadow_votes.jsonl
# ────────────────────────────────────────────────────────────
COMMIT_OLD_A = "data/history/postmortems.jsonl 2>/dev/null; then"
COMMIT_NEW_A = "data/history/postmortems.jsonl data/history/shadow_votes.jsonl 2>/dev/null; then"

COMMIT_OLD_B = "data/history/postmortems.jsonl 2>/dev/null || true"
COMMIT_NEW_B = "data/history/postmortems.jsonl data/history/shadow_votes.jsonl 2>/dev/null || true"


# ────────────────────────────────────────────────────────────
# 3. Расширить Upload results — добавить shadow_calibration.json
# ────────────────────────────────────────────────────────────
UPLOAD_OLD = "data/cache/decision_log.json"
UPLOAD_NEW = "data/cache/decision_log.json\n            data/cache/shadow_calibration.json\n            data/history/shadow_votes.jsonl"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] Не найден: {TARGET}')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    already_shadow_step = 'SHADOW LAYER' in text
    already_commit_a = 'shadow_votes.jsonl' in text
    already_upload = 'data/cache/shadow_calibration.json' in text

    if already_shadow_step and already_commit_a and already_upload:
        print('[OK] main.yml уже пропатчен для shadow. Skip.')
        return 0

    new_text = text
    changes = []

    if not already_shadow_step:
        if STEP_CONFLUENCE_MARKER not in new_text:
            print('[WARN] Не нашёл "Compute Confluence Gate" маркер.')
            print('       Пропускаю вставку shadow steps. Ты можешь добавить вручную.')
        else:
            new_text = new_text.replace(STEP_CONFLUENCE_MARKER, STEP_CONFLUENCE_NEW, 1)
            changes.append('shadow_postmortem + shadow_voter steps')

    if not already_commit_a:
        # Try both A and B replacements
        if COMMIT_OLD_A in new_text:
            new_text = new_text.replace(COMMIT_OLD_A, COMMIT_NEW_A, 1)
            changes.append('shadow_votes.jsonl в git diff check')
        if COMMIT_OLD_B in new_text:
            new_text = new_text.replace(COMMIT_OLD_B, COMMIT_NEW_B, 1)
            changes.append('shadow_votes.jsonl в git add')
        if 'shadow_votes.jsonl' not in new_text:
            print('[WARN] Не удалось добавить shadow_votes.jsonl в commit блок автоматически.')
            print('       Возможно, main.yml имеет другую структуру commit блока.')

    if not already_upload:
        if UPLOAD_OLD not in new_text:
            print('[WARN] Не нашёл маркер decision_log.json для расширения upload.')
        else:
            new_text = new_text.replace(UPLOAD_OLD, UPLOAD_NEW, 1)
            changes.append('shadow_calibration.json + shadow_votes.jsonl в upload')

    if not changes:
        return 0

    print(f'File: {TARGET}')
    for c in changes:
        print(f'  · {c}')

    if args.dry_run:
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.yml.bak_shadow_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')
    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
