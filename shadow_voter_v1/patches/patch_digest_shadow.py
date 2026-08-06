#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_digest_shadow.py — Добавить SHADOW VOTERS блок в daily_digest.py

Вставляет секцию перед "📄 FULL RUN REPORT". Читает последнюю запись
shadow_votes.jsonl (72h окно) и отображает голоса каждого voter'а.

Явная плашка "STATUS: HYPOTHESIS · не влияет на решение".

Идемпотентно.
"""
import sys, argparse, shutil
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / 'scripts' / 'daily_digest.py'

INSERT_BEFORE = '''    # === FULL REPORT LINK ===
    text += "━━━━━━━━━━━━━━━━━━━\\n"
    text += "<b>📄 FULL RUN REPORT</b>\\n"'''

NEW_BLOCK = '''    # === SHADOW VOTERS (STATUS: HYPOTHESIS · not decision-relevant) ===
    try:
        shadow_file = SCRIPT_DIR / 'data' / 'history' / 'shadow_votes.jsonl' if 'SCRIPT_DIR' in dir() else None
        if not shadow_file:
            from pathlib import Path as _P
            shadow_file = _P(__file__).parent.parent / 'data' / 'history' / 'shadow_votes.jsonl'
        if shadow_file and shadow_file.exists():
            # Read last 72h record (most recent)
            last_72h = None
            with open(shadow_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        if r.get('window') == '72h':
                            last_72h = r
                    except json.JSONDecodeError:
                        continue
            if last_72h:
                text += "━━━━━━━━━━━━━━━━━━━\\n"
                text += "<b>🔬 SHADOW VOTERS</b> <i>(HYPOTHESIS)</i>\\n"
                text += "━━━━━━━━━━━━━━━━━━━\\n"
                text += "<i>⚠ Экспериментальные модули. НЕ влияют на решение.\\n"
                text += "Собирают данные для калибровки. См. /calibrate.</i>\\n\\n"
                votes = last_72h.get('shadow_votes', {})
                for name, info in votes.items():
                    vote = info.get('vote', '?')
                    val = info.get('value', '?')
                    emoji = {'RALLY': '🟢', 'CRASH': '🔴', 'NEUTRAL': '⚪', 'UNKNOWN': '❓'}.get(vote, '❓')
                    text += f"  {emoji} {name}: <b>{vote}</b> · {val}\\n"
                agg = last_72h.get('aggregate_shadow', {})
                text += f"\\n<b>Aggregate shadow (72h):</b> {agg.get('shadow_signal', '?')}\\n"
                text += f"  Real confluence signal: {last_72h.get('current_confluence_signal', '?')}\\n"
                text += "\\n<i>Через 15+ closed forecasts (~4 дня при 6h RUN cadence) — первая калибровка.</i>\\n\\n"
    except Exception as _e:
        pass  # shadow block optional — не ломаем digest при ошибке

'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] Не найден: {TARGET}')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    if '🔬 SHADOW VOTERS' in text:
        print('[OK] Shadow блок уже присутствует. Skip.')
        return 0

    if INSERT_BEFORE not in text:
        print('[ERROR] Не нашёл точку вставки (FULL RUN REPORT).')
        return 1

    if text.count(INSERT_BEFORE) > 1:
        print('[WARN] Маркер встречается более раза. Останавливаюсь.')
        return 1

    new_text = text.replace(INSERT_BEFORE, NEW_BLOCK + INSERT_BEFORE, 1)

    try:
        compile(new_text, str(TARGET), 'exec')
    except SyntaxError as e:
        print(f'[ERROR] Патч даёт невалидный Python: {e}')
        return 1

    print(f'File: {TARGET}')
    print('  · SHADOW VOTERS блок добавлен перед FULL RUN REPORT')

    if args.dry_run:
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.py.bak_shadow_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')
    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
