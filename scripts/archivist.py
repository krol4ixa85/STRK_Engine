#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archivist.py · v1.0 · 20.08.2026
STRK ENGINE · агент-архивариус

ЧТО ДЕЛАЕТ
----------
Три задачи, все по расписанию, без единого ручного действия:

  1. СНИМОК   — копирует текущие token_scan в датированную папку истории
  2. ДЕЛЬТЫ   — сравнивает свежий скан с предыдущим снимком и пишет,
                что именно изменилось по каждому токену
  3. РЕТЕНШН  — сжимает снимки старше 60 дней в помесячные архивы,
                удаляет архивы старше 180 дней

ЗАЧЕМ ДЕЛЬТЫ ВАЖНЕЕ САМИХ ДАННЫХ
--------------------------------
Скан показывает состояние. Решение принимается по ИЗМЕНЕНИЮ состояния:
фаза сменилась, серия притока прервалась, появился сигнал распределения.
Без предыдущего снимка эти переходы невидимы — движок каждый раз читает
картину как будто впервые.

ПОЧЕМУ РЕТЕНШН НУЖЕН
--------------------
30 токенов × 4 КБ × 52 недели = ~6 МБ в год сырыми файлами, и это только
token_scan. Репозиторий и так растёт от коммитов кэшей. Сжатие даёт
примерно 10-кратную экономию, а данные старше полугода для свинг-решений
не нужны — рынок за это время меняет режим дважды.

СХЕМА ХРАНЕНИЯ
--------------
    data/history/token_scan/2026-08-20/LINK.json     свежие, как есть
    data/history/token_scan/archive/2026-06.tar.gz   старше 60 дней
    (старше 180 дней — удалено)

ЗАПУСК
------
  python3 scripts/archivist.py              # снимок + дельты + ретеншн
  python3 scripts/archivist.py --dry-run    # показать, ничего не трогая

ВЫХОД
-----
  data/history/token_scan/<дата>/*.json
  data/history/token_scan/archive/<месяц>.tar.gz
  data/cache/token_scan_deltas.json
  data/cache/archive_status.json
"""

import os
import re
import sys
import json
import glob
import shutil
import tarfile
import argparse
from datetime import datetime, timezone, timedelta

CACHE = "data/cache"
SCAN_DIR = os.path.join(CACHE, "token_scan")
HIST_ROOT = os.path.join("data", "history", "token_scan")
ARCHIVE_DIR = os.path.join(HIST_ROOT, "archive")

DELTAS_FILE = os.path.join(CACHE, "token_scan_deltas.json")
STATUS_FILE = os.path.join(CACHE, "archive_status.json")

# Сырые снимки старше этого возраста уезжают в помесячный tar.gz
COMPRESS_AFTER_DAYS = 60

# Архивы старше этого возраста удаляются
DELETE_AFTER_DAYS = 180

# Поля, изменение которых меняет решение. Остальное в дельты не пишем,
# чтобы файл не распухал шумом.
TRACKED = [
    "phase_verdict",
    "current_positive_streak_weeks",
    "pct_positive_weeks_180d",
    "recent_sos_count",
    "recent_dist_count",
    "netflow_30d_usd",
    "netflow_90d_usd",
    "netflow_180d_usd",
    "price_now",
]

# Насколько должно измениться числовое поле, чтобы считаться значимым.
# Ниже этого — шум пересчёта, а не событие.
SIGNIFICANT_PCT = 5.0

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНОЕ
# ─────────────────────────────────────────────────────────────

def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def snapshot_dirs():
    """Все датированные папки снимков, от старых к новым."""
    if not os.path.isdir(HIST_ROOT):
        return []
    out = []
    for name in os.listdir(HIST_ROOT):
        p = os.path.join(HIST_ROOT, name)
        if os.path.isdir(p) and DATE_RE.match(name):
            out.append(name)
    return sorted(out)


def day_age(date_str, today):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (today - d).days


# ─────────────────────────────────────────────────────────────
# 1 · СНИМОК
# ─────────────────────────────────────────────────────────────

def make_snapshot(today_str, dry=False):
    files = sorted(glob.glob(os.path.join(SCAN_DIR, "*.json")))
    if not files:
        print("  Снимок: в data/cache/token_scan/ пусто — пропускаю")
        return None, 0

    dest = os.path.join(HIST_ROOT, today_str)
    if dry:
        print(f"  Снимок: {len(files)} файлов → {dest} (dry-run)")
        return dest, len(files)

    os.makedirs(dest, exist_ok=True)
    n = 0
    for src in files:
        try:
            shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
            n += 1
        except Exception as e:
            print(f"  ⚠ не скопировал {os.path.basename(src)}: {e}")

    print(f"  Снимок: {n} файлов → {dest}")
    return dest, n


# ─────────────────────────────────────────────────────────────
# 2 · ДЕЛЬТЫ
# ─────────────────────────────────────────────────────────────

def find_previous_snapshot(today_str):
    """Ближайший предыдущий снимок, не считая сегодняшнего."""
    dirs = [d for d in snapshot_dirs() if d < today_str]
    return dirs[-1] if dirs else None


def compare_token(cur, prev):
    """Возвращает список значимых изменений по одному токену."""
    changes = []
    for field in TRACKED:
        a = prev.get(field)
        b = cur.get(field)
        if a is None and b is None:
            continue

        # Строковые поля: важен сам факт смены
        if isinstance(a, str) or isinstance(b, str):
            if a != b:
                changes.append({
                    "field": field, "from": a, "to": b,
                    "kind": "categorical", "significant": True,
                })
            continue

        try:
            a_f, b_f = float(a or 0), float(b or 0)
        except (TypeError, ValueError):
            continue

        if a_f == b_f:
            continue

        if a_f != 0:
            pct = (b_f - a_f) / abs(a_f) * 100
        else:
            pct = None

        # Счётчики недель и событий: значимо любое целое изменение
        counter = field in ("current_positive_streak_weeks",
                            "recent_sos_count", "recent_dist_count")
        significant = counter or (pct is not None and abs(pct) >= SIGNIFICANT_PCT)

        changes.append({
            "field": field, "from": a_f, "to": b_f,
            "change_pct": round(pct, 2) if pct is not None else None,
            "kind": "counter" if counter else "numeric",
            "significant": significant,
        })
    return changes


def describe(token, changes):
    """Человеческая формулировка того, что произошло."""
    lines = []
    for c in changes:
        if not c["significant"]:
            continue
        f = c["field"]
        if f == "phase_verdict":
            lines.append(f"фаза сменилась: {c['from']} → {c['to']}")
        elif f == "current_positive_streak_weeks":
            if c["to"] > c["from"]:
                lines.append(f"серия притока выросла до {int(c['to'])} нед")
            else:
                lines.append(f"серия притока прервалась ({int(c['from'])} → {int(c['to'])} нед)")
        elif f == "recent_sos_count":
            if c["to"] > c["from"]:
                lines.append("появился новый сигнал силы (SOS)")
            else:
                lines.append("сигнал силы вышел из 8-недельного окна")
        elif f == "recent_dist_count":
            if c["to"] > c["from"]:
                lines.append("⚠ появился сигнал распределения")
            else:
                lines.append("сигнал распределения вышел из окна")
        elif f == "netflow_30d_usd":
            if c["from"] > 0 >= c["to"]:
                lines.append("⚠ месячный приток стал отрицательным")
            elif c["from"] <= 0 < c["to"]:
                lines.append("месячный приток вышел в плюс")
            elif c.get("change_pct") is not None:
                lines.append(f"месячный приток изменился на {c['change_pct']:+.0f}%")
        elif f == "price_now" and c.get("change_pct") is not None:
            lines.append(f"цена {c['change_pct']:+.1f}%")
        elif f == "pct_positive_weeks_180d":
            lines.append(f"доля недель с притоком {c['from']:.0f}% → {c['to']:.0f}%")
    return lines


def build_deltas(today_str, dry=False):
    prev_date = find_previous_snapshot(today_str)
    if not prev_date:
        print("  Дельты: предыдущего снимка нет — первый прогон, сравнивать не с чем")
        out = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "previous_snapshot": None,
            "current_snapshot": today_str,
            "note": "первый снимок — дельты появятся со следующего прогона",
            "tokens": {},
        }
        if not dry:
            os.makedirs(CACHE, exist_ok=True)
            with open(DELTAS_FILE, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
        return out

    prev_dir = os.path.join(HIST_ROOT, prev_date)
    tokens, n_changed, alerts = {}, 0, []

    for src in sorted(glob.glob(os.path.join(SCAN_DIR, "*.json"))):
        token = os.path.basename(src)[:-5].upper()
        cur = read_json(src)
        prev = read_json(os.path.join(prev_dir, os.path.basename(src)))

        if not cur:
            continue
        if not prev:
            tokens[token] = {"status": "NEW", "note": "токен появился в юниверсе"}
            continue

        changes = compare_token(cur, prev)
        sig = [c for c in changes if c["significant"]]
        summary = describe(token, changes)

        tokens[token] = {
            "status": "CHANGED" if sig else "STABLE",
            "changes_total": len(changes),
            "changes_significant": len(sig),
            "summary_ru": summary,
            "changes": sig,
        }
        if sig:
            n_changed += 1
        for line in summary:
            if line.startswith("⚠") or "фаза сменилась" in line:
                alerts.append(f"{token}: {line}")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "previous_snapshot": prev_date,
        "current_snapshot": today_str,
        "days_between": (datetime.strptime(today_str, "%Y-%m-%d").date()
                         - datetime.strptime(prev_date, "%Y-%m-%d").date()).days,
        "tokens_compared": len(tokens),
        "tokens_changed": n_changed,
        "alerts": alerts,
        "tokens": tokens,
    }

    if not dry:
        os.makedirs(CACHE, exist_ok=True)
        with open(DELTAS_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"  Дельты: {prev_date} → {today_str} · "
          f"изменилось {n_changed} из {len(tokens)}")
    for a in alerts[:8]:
        print(f"     {a}")
    return out


# ─────────────────────────────────────────────────────────────
# 3 · РЕТЕНШН
# ─────────────────────────────────────────────────────────────

def compress_old(today, dry=False):
    """Снимки старше COMPRESS_AFTER_DAYS складываем в помесячные tar.gz."""
    by_month = {}
    for d in snapshot_dirs():
        age = day_age(d, today)
        if age is None or age <= COMPRESS_AFTER_DAYS:
            continue
        by_month.setdefault(d[:7], []).append(d)

    if not by_month:
        print(f"  Сжатие: снимков старше {COMPRESS_AFTER_DAYS} дней нет")
        return []

    done = []
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    for month, dirs in sorted(by_month.items()):
        tar_path = os.path.join(ARCHIVE_DIR, f"{month}.tar.gz")
        if dry:
            print(f"  Сжатие: {len(dirs)} снимков → {tar_path} (dry-run)")
            done.append({"month": month, "snapshots": len(dirs), "dry": True})
            continue

        # Дозапись в существующий tar.gz невозможна — если архив за месяц
        # уже есть, распаковываем во временную папку и пересобираем.
        tmp = os.path.join(ARCHIVE_DIR, f"_tmp_{month}")
        os.makedirs(tmp, exist_ok=True)
        if os.path.exists(tar_path):
            try:
                with tarfile.open(tar_path, "r:gz") as t:
                    t.extractall(tmp)
            except Exception as e:
                print(f"  ⚠ не смог прочитать {tar_path}: {e}")

        for d in dirs:
            shutil.move(os.path.join(HIST_ROOT, d), os.path.join(tmp, d))

        with tarfile.open(tar_path, "w:gz") as t:
            for name in sorted(os.listdir(tmp)):
                t.add(os.path.join(tmp, name), arcname=name)

        shutil.rmtree(tmp, ignore_errors=True)
        size_kb = round(os.path.getsize(tar_path) / 1024, 1)
        print(f"  Сжатие: {len(dirs)} снимков → {month}.tar.gz ({size_kb} КБ)")
        done.append({"month": month, "snapshots": len(dirs), "size_kb": size_kb})

    return done


def delete_expired(today, dry=False):
    """Архивы старше DELETE_AFTER_DAYS удаляем."""
    if not os.path.isdir(ARCHIVE_DIR):
        return []

    removed = []
    for name in sorted(os.listdir(ARCHIVE_DIR)):
        if not name.endswith(".tar.gz"):
            continue
        month = name[:-7]
        try:
            # Возраст считаем по последнему дню месяца — консервативно,
            # чтобы не удалить то, что ещё в пределах срока.
            d = datetime.strptime(month + "-28", "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - d).days
        if age <= DELETE_AFTER_DAYS:
            continue
        path = os.path.join(ARCHIVE_DIR, name)
        if dry:
            print(f"  Удаление: {name} (возраст {age} дн, dry-run)")
        else:
            os.remove(path)
            print(f"  Удаление: {name} (возраст {age} дн)")
        removed.append({"archive": name, "age_days": age})

    if not removed:
        print(f"  Удаление: архивов старше {DELETE_AFTER_DAYS} дней нет")
    return removed


def disk_usage():
    def size_of(path):
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    raw = size_of(HIST_ROOT) if os.path.isdir(HIST_ROOT) else 0
    arch = size_of(ARCHIVE_DIR) if os.path.isdir(ARCHIVE_DIR) else 0
    return round((raw - arch) / 1024, 1), round(arch / 1024, 1)


# ─────────────────────────────────────────────────────────────
# ГЛАВНЫЙ ПРОГОН
# ─────────────────────────────────────────────────────────────

def main(dry=False):
    print("=== Archivist v1.0 ===")
    if dry:
        print("режим dry-run · ничего не меняем\n")
    else:
        print()

    now = datetime.now(timezone.utc)
    today = now.date()
    today_str = today.strftime("%Y-%m-%d")

    # Дельты считаем ДО снимка: иначе сегодняшний снимок станет
    # «предыдущим» для самого себя и все изменения обнулятся.
    deltas = build_deltas(today_str, dry=dry)
    dest, n_files = make_snapshot(today_str, dry=dry)
    compressed = compress_old(today, dry=dry)
    removed = delete_expired(today, dry=dry)

    raw_kb, arch_kb = disk_usage()

    status = {
        "computed_at": now.isoformat(),
        "snapshot_date": today_str,
        "files_in_snapshot": n_files,
        "snapshots_kept": len(snapshot_dirs()),
        "compressed_this_run": compressed,
        "deleted_this_run": removed,
        "policy": {
            "compress_after_days": COMPRESS_AFTER_DAYS,
            "delete_after_days": DELETE_AFTER_DAYS,
            "significant_change_pct": SIGNIFICANT_PCT,
        },
        "disk": {"raw_snapshots_kb": raw_kb, "archives_kb": arch_kb},
        "deltas": {
            "previous_snapshot": deltas.get("previous_snapshot"),
            "tokens_changed": deltas.get("tokens_changed", 0),
            "alerts": deltas.get("alerts", []),
        },
    }

    if not dry:
        os.makedirs(CACHE, exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)

    print(f"\n  Снимков в истории: {len(snapshot_dirs())}")
    print(f"  Занято: сырые {raw_kb} КБ · архивы {arch_kb} КБ")
    print(f"\n✓ {DELTAS_FILE}")
    print(f"✓ {STATUS_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="показать план, ничего не трогая")
    a = ap.parse_args()
    main(dry=a.dry_run)
