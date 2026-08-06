#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install_all.py — Установщик пакета shadow_voter_v1

Прогоняет:
  1. Копирование новых файлов (shadow_voter.py, shadow_postmortem.py,
     calibration_report.py, voter_config.json)
  2. 4 патча (telegram_calibrate, digest_shadow, workflow_shadow, gitignore_shadow)
  3. Smoke-test: shadow_voter.py + shadow_postmortem.py + calibration_report.py

ЗАПУСК из корня репо:
  python install_all.py
  python install_all.py --dry-run
  python install_all.py --skip-smoke
"""
import os, sys, shutil, subprocess, argparse
from pathlib import Path

HERE = Path(__file__).parent.resolve()
REPO_ROOT = None
PKG_ROOT = None

if (HERE / '.github').exists() and (HERE / 'scripts').exists():
    REPO_ROOT = HERE
    PKG_ROOT = HERE / 'shadow_voter_v1'
elif (HERE.parent / '.github').exists() and (HERE.parent / 'scripts').exists():
    REPO_ROOT = HERE.parent
    PKG_ROOT = HERE
else:
    for candidate in [HERE, HERE.parent, HERE.parent.parent]:
        if (candidate / '.github' / 'workflows' / 'main.yml').exists():
            REPO_ROOT = candidate
            PKG_ROOT = HERE
            break


def die(msg, code=1):
    print(f'\n❌ {msg}\n')
    sys.exit(code)


def step(n, title):
    print(f'\n' + '=' * 65)
    print(f'STEP {n}. {title}')
    print('=' * 65)


def copy_new_files(dry_run):
    files = [
        ('scripts/detectors/shadow_voter.py',      'scripts/detectors/shadow_voter.py'),
        ('scripts/detectors/shadow_postmortem.py', 'scripts/detectors/shadow_postmortem.py'),
        ('scripts/calibration_report.py',          'scripts/calibration_report.py'),
        ('config/voter_config.json',               'config/voter_config.json'),
    ]
    copied = 0
    skipped = 0
    for src_rel, dst_rel in files:
        src = PKG_ROOT / src_rel
        dst = REPO_ROOT / dst_rel
        if not src.exists():
            print(f'  ⚠  MISSING SOURCE: {src}')
            continue
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            skipped += 1
            print(f'  · {dst_rel} — already up to date')
            continue
        if dry_run:
            print(f'  [DRY] copy → {dst_rel}')
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f'  · {dst_rel}')
        copied += 1
    return copied, skipped


def copy_docs(dry_run):
    src = PKG_ROOT / 'docs' / 'MASTER_SECTION_026.md'
    dst_dir = REPO_ROOT / 'docs'
    dst = dst_dir / 'MASTER_SECTION_026.md'
    if not src.exists():
        return False
    if dry_run:
        print(f'  [DRY] copy → docs/MASTER_SECTION_026.md')
        return True
    dst_dir.mkdir(exist_ok=True)
    shutil.copy2(src, dst)
    print(f'  · docs/MASTER_SECTION_026.md')
    print(f'     ⚠ Вставь секцию §0.26 вручную в /mnt/project/STRK_MASTER_INSTRUCTION.md')
    return True


def run_patch(patch_name, dry_run):
    patch_src = PKG_ROOT / 'patches' / patch_name
    if not patch_src.exists():
        print(f'  ⚠ Патч не найден: {patch_name}')
        return False

    repo_patches = REPO_ROOT / 'patches'
    repo_patches.mkdir(parents=True, exist_ok=True)
    dst = repo_patches / patch_name
    shutil.copy2(patch_src, dst)

    args = [sys.executable, str(dst)]
    if dry_run:
        args.append('--dry-run')

    try:
        r = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        for line in r.stdout.splitlines():
            print(f'  {line}')
        for line in r.stderr.splitlines():
            print(f'  [stderr] {line}')
        return r.returncode == 0
    except Exception as e:
        print(f'  ⚠ Ошибка: {e}')
        return False


def smoke_test():
    """Run shadow_voter → shadow_postmortem → calibration_report."""
    scripts = [
        ('scripts/detectors/shadow_voter.py',      120),
        ('scripts/detectors/shadow_postmortem.py', 60),
        ('scripts/calibration_report.py',          30),
    ]
    for rel, timeout in scripts:
        p = REPO_ROOT / rel
        if not p.exists():
            print(f'  ❌ MISSING: {rel}')
            continue
        try:
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'
            r = subprocess.run([sys.executable, str(p)], cwd=REPO_ROOT,
                               capture_output=True, text=True, timeout=timeout, env=env)
            if r.returncode == 0:
                print(f'  ✅ {rel}')
                # Show last few lines of output for context
                tail = r.stdout.strip().split('\n')[-3:]
                for t in tail:
                    print(f'     {t}')
            else:
                print(f'  ❌ {rel} FAIL')
                print(f'     stderr: {r.stderr[:200]}')
        except subprocess.TimeoutExpired:
            print(f'  ⚠ {rel} TIMEOUT')
        except Exception as e:
            print(f'  ⚠ {rel} ERROR: {e}')

    # Check shadow_votes.jsonl was written
    sv = REPO_ROOT / 'data' / 'history' / 'shadow_votes.jsonl'
    if sv.exists():
        try:
            n = sum(1 for _ in open(sv, encoding='utf-8'))
            print(f'\n  📊 shadow_votes.jsonl: {n} lines total')
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-smoke', action='store_true')
    args = parser.parse_args()

    print('=' * 65)
    print('STRK Engine · Shadow Voter v1 · Installer')
    print('=' * 65)

    if REPO_ROOT is None:
        die('Не могу найти корень репо STRK-ENGINE.\n'
            'Проверь: .github/workflows/main.yml должен существовать.')

    print(f'  REPO_ROOT: {REPO_ROOT}')
    print(f'  PKG_ROOT:  {PKG_ROOT}')

    if args.dry_run:
        print('\n  ⚠ DRY-RUN режим')

    step(1, 'Копирование новых файлов кода')
    copied, skipped = copy_new_files(args.dry_run)
    print(f'  Скопировано: {copied}, уже актуально: {skipped}')

    step(2, 'Копирование документации (MASTER §0.26)')
    copy_docs(args.dry_run)

    step(3, 'Патч telegram_bot_commands.py (/calibrate)')
    run_patch('patch_telegram_calibrate.py', args.dry_run)

    step(4, 'Патч daily_digest.py (SHADOW блок с HYPOTHESIS)')
    run_patch('patch_digest_shadow.py', args.dry_run)

    step(5, 'Патч workflow main.yml (shadow steps + commit + upload)')
    run_patch('patch_workflow_shadow.py', args.dry_run)

    step(6, 'Патч .gitignore (allow shadow files in git)')
    run_patch('patch_gitignore_shadow.py', args.dry_run)

    if not args.dry_run and not args.skip_smoke:
        step(7, 'SMOKE-TEST · shadow_voter + shadow_postmortem + calibration_report')
        smoke_test()

    print('\n' + '=' * 65)
    print('ИТОГ')
    print('=' * 65)

    if args.dry_run:
        print('DRY-RUN завершён. Повтори без --dry-run.')
        return 0

    print('''
Что дальше:

1. Проверь глазами:
   · type config\\voter_config.json
   · type data\\history\\shadow_votes.jsonl
   · findstr /C:"/calibrate" scripts\\telegram_bot_commands.py
   · findstr /C:"SHADOW LAYER" .github\\workflows\\main.yml

2. Вставь секцию §0.26 в STRK_MASTER_INSTRUCTION.md
   (см. docs/MASTER_SECTION_026.md — текст готов к копированию)

3. Закоммить:
   git add scripts/detectors/shadow_voter.py
   git add scripts/detectors/shadow_postmortem.py
   git add scripts/calibration_report.py
   git add config/voter_config.json
   git add scripts/telegram_bot_commands.py
   git add scripts/daily_digest.py
   git add .github/workflows/main.yml
   git add .gitignore
   git add data/history/shadow_votes.jsonl
   git add docs/MASTER_SECTION_026.md
   git commit -m "feat: shadow voter framework v1 (5 shadow voters, 72h+7d windows, HYPOTHESIS)"
   git push

4. После push GitHub Actions начнёт писать shadow_votes каждый RUN.
   · Первая калибровка на 72h окне: через ~4 дня (при 6h cadence, 15 RUN'ов)
   · Первая калибровка на 7d окне: через ~10 дней
   · Проверить в Telegram: /calibrate

5. НЕ включать shadow-модули в decision до N ≥ 15 AND precision ≥ 55%.
''')
    return 0


if __name__ == '__main__':
    sys.exit(main())
