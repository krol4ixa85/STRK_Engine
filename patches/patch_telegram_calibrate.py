#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_telegram_calibrate.py — Добавить /calibrate команду в telegram_bot_commands.py

Идемпотентно. Устойчив к отступам (regex). Компилирует перед записью.
"""
import sys, argparse, shutil, re
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'patches' else Path.cwd()
TARGET = REPO_ROOT / 'scripts' / 'telegram_bot_commands.py'

NEW_CMD = '''elif cmd == '/calibrate':
    send_message(chat_id, "📊 Building shadow calibration report...")
    try:
        env = os.environ.copy()
        env['PYTHONUTF8'] = '1'
        env['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        env['TELEGRAM_CHAT_ID'] = os.environ.get('TELEGRAM_CHAT_ID', '')
        script = SCRIPT_DIR / 'scripts' / 'calibration_report.py'
        if not script.exists():
            send_message(chat_id, "❌ calibration_report.py не установлен")
            return
        r = subprocess.run([sys.executable, str(script), '--telegram'],
                           capture_output=True, text=True, encoding='utf-8',
                           timeout=60, env=env)
        if r.returncode != 0:
            send_message(chat_id, f"❌ Error: {r.stderr[:300]}")
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
        print(f'[ERROR] Не найден: {TARGET}')
        return 1

    text = TARGET.read_text(encoding='utf-8')

    if "elif cmd == '/calibrate':" in text:
        print('[OK] Команда /calibrate уже установлена. Skip.')
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
        print(f'[ERROR] Патч даёт невалидный Python: {e}')
        return 1

    print(f'File: {TARGET}')
    print(f'  · команда /calibrate добавлена (отступ {len(indent_else)} пробелов)')

    if args.dry_run:
        print('[DRY-RUN] Не записано.')
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup = TARGET.with_suffix(f'.py.bak_calib_{ts}')
    shutil.copy(TARGET, backup)
    print(f'  Backup: {backup}')
    TARGET.write_text(new_text, encoding='utf-8')
    print(f'  Written: {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
