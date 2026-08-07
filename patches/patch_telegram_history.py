#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_telegram_history.py — Добавить /history команду в telegram_bot_commands.py

Показывает последние 5 записей из data/history/all_history.jsonl:
  · run_id, timestamp, price
  · confluence signal / composite direction
  · shadow signal
  · outcome_72h / outcome_7d (если закрыты)

Устойчив к отступам. Компилирует до записи.
"""
import sys, argparse, shutil, re
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / 'scripts' / 'telegram_bot_commands.py'

NEW_CMD = '''elif cmd == '/history':
    try:
        hist_file = SCRIPT_DIR / 'data' / 'history' / 'all_history.jsonl'
        if not hist_file.exists():
            send_message(chat_id, "📚 No history entries yet.\\n\\nHistory accumulates each RUN. Wait ~6h for first entry.")
            return
        with open(hist_file, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            send_message(chat_id, "📚 No history entries yet.")
            return
        last_5 = lines[-5:]
        msg = f"<b>📚 Last {len(last_5)} history entries</b>\\n<i>Всего: {len(lines)} записей</i>\\n\\n"
        for line in reversed(last_5):
            try:
                r = json.loads(line)
            except Exception:
                continue
            run_id = r.get('run_id', '?')
            ts = (r.get('timestamp') or '')[:16].replace('T', ' ')
            price = r.get('price_usd')
            price_str = f"${price:.4f}" if price else "—"
            live = r.get('live_signals') or {}
            conf = (live.get('confluence_gate') or {}).get('signal') or '—'
            comp = (live.get('composite_v2') or {}).get('direction') or '—'
            shadow = (r.get('shadow_ref') or {}).get('shadow_signal') or '—'
            o72 = (r.get('outcome_72h') or {}).get('signal') or 'PENDING'
            o7d = (r.get('outcome_7d') or {}).get('signal') or 'PENDING'
            status = r.get('status', 'PENDING')

            msg += f"<b>{run_id}</b> · {ts} · {price_str}\\n"
            msg += f"  Confluence: <code>{conf}</code>  Composite: <code>{comp}</code>\\n"
            msg += f"  Shadow: <code>{shadow}</code>  Status: <b>{status}</b>\\n"
            if status != 'PENDING':
                o72_str = o72 if 'signal' in str(r.get('outcome_72h', {})) else 'PENDING'
                o7d_str = o7d if 'signal' in str(r.get('outcome_7d', {})) else 'PENDING'
                msg += f"  72h: {o72}  · 7d: {o7d}\\n"
            msg += "\\n"

        msg += f"<i>Полная история: data/history/all_history.jsonl</i>"
        send_message(chat_id, msg)
    except Exception as e:
        send_message(chat_id, f"❌ Error: {e}")

'''


def find_else_unknown_command(text):
    m = re.search(
        r"^([ \t]*)else:\s*\n([ \t]+)send_message\(chat_id, f\"Unknown command: \{cmd\}",
        text, re.MULTILINE)
    if not m:
        return None
    return m.start(), m.group(1)


def indent_text(txt, prefix):
    return '\n'.join(prefix + l if l.strip() else l for l in txt.split('\n'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not TARGET.exists():
        print(f'[ERROR] {TARGET} не найден.')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    if "elif cmd == '/history':" in text:
        print('[OK] Команда /history уже установлена. Skip.')
        return 0

    found = find_else_unknown_command(text)
    if not found:
        print('[ERROR] Не нашёл "else: Unknown command" маркер.')
        return 1
    pos, indent_else = found
    insertion = indent_text(NEW_CMD, indent_else)
    new_text = text[:pos] + insertion + text[pos:]

    try:
        compile(new_text, str(TARGET), 'exec')
    except SyntaxError as e:
        print(f'[ERROR] Невалидный Python: {e}')
        return 1

    print(f'File: {TARGET}')
    print(f'  · /history (отступ {len(indent_else)} пробелов)')

    if args.dry_run:
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.py.bak_hist_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')
    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
