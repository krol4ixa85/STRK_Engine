#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_workflow_history.py — Расширить .github/workflows/main.yml

Добавляет 3 новых step в правильном порядке:
  1. covert_flow_detector.py       — ПОСЛЕ orchestrator (нужны edges CSV)
  2. history_postmortem.py         — ПЕРЕД history_accumulator (закрываем старое ДО записи новых)
  3. history_accumulator.py        — ПОСЛЕ всех детекторов (снимок всего)

Расширяет:
  · commit блок — добавить all_history.jsonl, covert_flow_signal.json
  · upload results — добавить эти же файлы

Порядок в yaml (важен!):
  ...
  Compute Confluence Gate
  Shadow postmortem (v1)                  ← из shadow_voter_v1
  Shadow voter (v1)                       ← из shadow_voter_v1
  [NEW] Covert flow detector              ← пишет covert_flow_signal.json
  [NEW] History postmortem                ← закрывает старые all_history
  [NEW] History accumulator               ← пишет свежий снимок (уже с covert_flow)
  Send unified digest
  Commit + Upload
"""
import sys, argparse, shutil, re
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / '.github' / 'workflows' / 'main.yml'


# ────────────────────────────────────────────────────────────
# 1. Вставить covert_flow ПОСЛЕ shadow_voter step
# (если shadow_voter_v1 установлен)
# ────────────────────────────────────────────────────────────
INSERT_AFTER_SHADOW_END = "      # === END SHADOW LAYER ==="

INSERT_AFTER_SHADOW_END_NEW = """      # === END SHADOW LAYER ===
      
      # === COVERT FLOW + HISTORY LAYER (v1) ===
      - name: Covert flow detector (shadow, reads edges CSV from orchestrator)
        env:
          STRICT_NO_TRADING: 'true'
        run: python3 scripts/detectors/covert_flow_detector.py || true
      
      - name: History postmortem — close PENDING all_history records
        run: python3 scripts/history_postmortem.py || true
      
      - name: History accumulator — write compact snapshot for this RUN
        env:
          STRK_RUN_ID: hist_${{ github.run_id }}_${{ github.run_number }}
        run: python3 scripts/history_accumulator.py || true
      # === END COVERT FLOW + HISTORY LAYER ==="""

# Fallback: если shadow layer НЕ установлен, вставим после Confluence Gate
INSERT_AFTER_CONFLUENCE = """      - name: Compute Confluence Gate (multi-signal decision)
        run: python3 scripts/detectors/confluence_gate.py || true"""

INSERT_AFTER_CONFLUENCE_NEW = """      - name: Compute Confluence Gate (multi-signal decision)
        run: python3 scripts/detectors/confluence_gate.py || true
      
      # === COVERT FLOW + HISTORY LAYER (v1) ===
      - name: Covert flow detector (shadow, reads edges CSV from orchestrator)
        env:
          STRICT_NO_TRADING: 'true'
        run: python3 scripts/detectors/covert_flow_detector.py || true
      
      - name: History postmortem — close PENDING all_history records
        run: python3 scripts/history_postmortem.py || true
      
      - name: History accumulator — write compact snapshot for this RUN
        env:
          STRK_RUN_ID: hist_${{ github.run_id }}_${{ github.run_number }}
        run: python3 scripts/history_accumulator.py || true
      # === END COVERT FLOW + HISTORY LAYER ==="""


# ────────────────────────────────────────────────────────────
# 2. Расширить commit блок — добавить all_history.jsonl,
#    covert_flow_signal.json (last existing marker → append)
# ────────────────────────────────────────────────────────────
COMMIT_MARKERS = [
    ('data/history/postmortems.jsonl data/history/shadow_votes.jsonl',
     'data/history/postmortems.jsonl data/history/shadow_votes.jsonl data/history/all_history.jsonl'),
    ('data/history/postmortems.jsonl 2>/dev/null',  # if shadow_voter_v1 not installed
     'data/history/postmortems.jsonl data/history/all_history.jsonl 2>/dev/null'),
]


# ────────────────────────────────────────────────────────────
# 3. Upload results — добавить covert_flow_signal.json, all_history.jsonl
# ────────────────────────────────────────────────────────────
UPLOAD_MARKERS = [
    ('data/cache/shadow_calibration.json',   # if shadow_voter_v1 installed
     'data/cache/shadow_calibration.json\n            data/cache/covert_flow_signal.json\n            data/history/all_history.jsonl'),
    ('data/cache/decision_log.json',          # fallback
     'data/cache/decision_log.json\n            data/cache/covert_flow_signal.json\n            data/history/all_history.jsonl'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] {TARGET} не найден.')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    already_installed = 'COVERT FLOW + HISTORY LAYER' in text
    already_commit = 'all_history.jsonl' in text
    already_upload = 'covert_flow_signal.json' in text

    if already_installed and already_commit and already_upload:
        print('[OK] main.yml уже пропатчен для covert+history. Skip.')
        return 0

    new_text = text
    changes = []

    # 1. Insert 3 new steps
    if not already_installed:
        if INSERT_AFTER_SHADOW_END in new_text:
            new_text = new_text.replace(INSERT_AFTER_SHADOW_END,
                                        INSERT_AFTER_SHADOW_END_NEW, 1)
            changes.append('3 steps после SHADOW LAYER')
        elif INSERT_AFTER_CONFLUENCE in new_text:
            new_text = new_text.replace(INSERT_AFTER_CONFLUENCE,
                                        INSERT_AFTER_CONFLUENCE_NEW, 1)
            changes.append('3 steps после Confluence Gate (shadow_voter_v1 не найден)')
        else:
            print('[ERROR] Не нашёл маркер для вставки. Ни SHADOW LAYER, ни Confluence Gate.')
            return 1

    # 2. Commit
    if not already_commit:
        for old, new in COMMIT_MARKERS:
            if old in new_text:
                new_text = new_text.replace(old, new, 1)
                changes.append(f'commit блок: added all_history.jsonl')
                break

    # 3. Upload
    if not already_upload:
        for old, new in UPLOAD_MARKERS:
            if old in new_text:
                new_text = new_text.replace(old, new, 1)
                changes.append(f'upload: covert_flow_signal.json + all_history.jsonl')
                break

    print(f'File: {TARGET}')
    for c in changes:
        print(f'  · {c}')

    if args.dry_run:
        return 0

    if not changes:
        print('  Ничего не менялось.')
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.yml.bak_hist_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')
    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
