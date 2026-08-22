#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feature_snapshot.py · v1.0 · 22.08.2026
STRK ENGINE · срез значений всех детекторов, дописывается раз в сутки

ЗАЧЕМ · САМОЕ ВАЖНОЕ В ПЛАТФОРМЕ
---------------------------------
Цену и объём можно скачать задним числом за любой год. Потоки Dune,
глубину стакана, фазу Wyckoff, счёт confluence — нельзя. Эти кэши
ПЕРЕЗАПИСЫВАЮТСЯ каждым прогоном. Значение, которое было вчера, вчера
же и исчезло.

Из-за этого сейчас невозможно проверить ни один потоковый детектор:
в `data/history/` лежит только `decision_log.jsonl` — 379 решений.
Чисел, из которых эти решения получились, нет нигде.

Этот скрипт дописывает срез. Через три месяца появится 90 точек по
42 токенам — первая в истории платформы возможность посчитать IC для
собственных детекторов. Без него этой возможности не будет никогда,
и каждый день без него — день, который уже не вернуть.

ЧТО СОХРАНЯЕТСЯ · И ЧТО СОЗНАТЕЛЬНО НЕТ
----------------------------------------
Сохраняются ВХОДЫ — сырые значения фич по контракту config/features.json.

Не сохраняются ВЫВОДЫ — какие правила сработали, какой был вердикт.
Причина: выводы можно пересчитать из входов в любой момент, причём
новой версией логики. Входы пересчитать нельзя. Если через полгода
выяснится, что порог был неверный, — по сохранённым входам можно
переиграть всю историю. По сохранённым вердиктам нельзя ничего.

СТОИМОСТЬ
---------
Ноль. Читает только те кэши, которые уже собраны другими джобами.
Ни одного сетевого запроса, ни одного кредита Dune.

ФОРМАТ ХРАНЕНИЯ
---------------
  data/history/features/YYYY-MM-DD.jsonl

По файлу на сутки, а не один растущий файл. Git хранит каждую версию
файла целиком: дописывание в общий файл означало бы, что к концу года
в репозитории лежит 365 копий всё более толстого файла. Посуточные
файлы — 20 КБ в день, около 7 МБ в год, и каждый записывается один раз.

Строка на токен:
  {"ts": "...", "token": "LINK", "f": {...}, "missing": [...], "stale": [...]}

Значения нет — имя фичи уходит в "missing". Пустых значений в "f" не
бывает, поэтому потом видно разницу между «ноль» и «не измеряли».

Значение ЕСТЬ, но кэш просрочен — оно всё равно записывается, а имя
дополнительно попадает в "stale". Выбрасывать при записи нельзя:
выброшенный поток Dune задним числом не скачать. Фильтровать надо при
анализе. Правила при этом просроченное по-прежнему игнорируют —
у них своя логика, решение на старых данных принимать нельзя.

ЗАПУСК
------
  python3 scripts/feature_snapshot.py
  python3 scripts/feature_snapshot.py --dry-run
  python3 scripts/feature_snapshot.py --stats

ВХОД
----
  config/features.json  · контракт фич
  data/cache/*.json     · то, что уже собрано

ВЫХОД
-----
  data/history/features/<дата>.jsonl
  data/cache/feature_snapshot_status.json
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import rule_registry
    from rule_registry import FeatureStore, load_json, tokens_from_config
except ImportError:
    raise SystemExit("ERROR: рядом должен лежать scripts/rule_registry.py")

# Проверка совместимости. Эти два файла работают в паре, и залить можно
# только один из них — через веб-интерфейс файлы загружаются по одному.
# Именно так и вышло 22.08: снимок упал с
#   TypeError: FeatureStore.__init__() got an unexpected keyword 'include_globals'
# По такому сообщению непонятно, что делать. Здесь сказано прямо.
NEED_API = 2
_have = getattr(rule_registry, "FEATURE_STORE_API", 1)
if _have < NEED_API:
    raise SystemExit(
        "\n  ОСТАНОВЛЕНО: рядом лежит СТАРАЯ версия scripts/rule_registry.py\n"
        f"  нужна версия интерфейса {NEED_API}, найдена {_have}\n\n"
        "  Что сделать: перезалить scripts/rule_registry.py — тот же файл,\n"
        "  что и feature_snapshot.py, они работают только в паре.\n"
        "  Заодно проверь config/features.json: в нём должна быть секция\n"
        '  "globals" — если её нет, он тоже старый.\n')

FEATURES_FILE = "config/features.json"
HIST_DIR = "data/history/features"
STATUS_FILE = "data/cache/feature_snapshot_status.json"
GLOBAL_ROW = "_GLOBAL"


def snapshot(store, tokens, global_names, token_names):
    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for t in tokens:
        vals, missing, stale = {}, [], []
        for name in token_names:
            v, state, _ = store.get(name, t, keep_stale=True)
            if v is None:
                missing.append(name)
                continue
            vals[name] = v
            if state == "STALE":
                stale.append(name)
        rows.append({"ts": now, "token": t, "f": vals,
                     "missing": missing, "stale": stale})

    if global_names:
        vals, missing, stale = {}, [], []
        for name in global_names:
            v, state, _ = store.get(name, GLOBAL_ROW, keep_stale=True)
            if v is None:
                missing.append(name)
                continue
            vals[name] = v
            if state == "STALE":
                stale.append(name)
        rows.append({"ts": now, "token": GLOBAL_ROW, "f": vals,
                     "missing": missing, "stale": stale})

    return rows


def stats():
    files = sorted(glob.glob(os.path.join(HIST_DIR, "*.jsonl")))
    if not files:
        print(f"  В {HIST_DIR} пока пусто — ни одного среза не записано.")
        return 1
    total, per_feature, days = 0, {}, []
    for p in files:
        n = 0
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                n += 1
                total += 1
                for k in r.get("f", {}):
                    per_feature[k] = per_feature.get(k, 0) + 1
        days.append((os.path.basename(p)[:-6], n))

    print(f"=== Накоплено срезов ===\n")
    print(f"  Дней: {len(days)} · строк: {total} · "
          f"период: {days[0][0]} → {days[-1][0]}")
    horizon = 14
    obs = len(days) // horizon
    print(f"  При горизонте {horizon} дней это {obs} непересекающихся "
          f"наблюдений на токен")
    if obs < 8:
        need = (8 - obs) * horizon
        print(f"  До минимума для проверки не хватает ещё ~{need} дней\n")
    else:
        print()
    print(f"  {'ФИЧА':28}{'записей':>9}{'покрытие':>10}")
    print("  " + "─" * 47)
    for k in sorted(per_feature, key=lambda z: -per_feature[z]):
        print(f"  {k:28}{per_feature[k]:>9}{per_feature[k] / total * 100:>9.0f}%")
    return 0


def main(dry, show_stats):
    if show_stats:
        return stats()

    spec = load_json(FEATURES_FILE)
    if not spec:
        print(f"  Нет {FEATURES_FILE}")
        return 1

    token_names = sorted(spec.get("features", {}))
    global_names = sorted(k for k, v in (spec.get("globals") or {}).items()
                          if isinstance(v, dict))
    store = FeatureStore(spec, include_globals=True)

    tokens = [t.upper() for t in tokens_from_config() if t]
    if not tokens:
        print("  Нет списка токенов")
        return 1

    rows = snapshot(store, tokens, global_names, token_names)

    filled = sum(len(r["f"]) for r in rows)
    missing = sum(len(r["missing"]) for r in rows)
    stale_n = sum(len(r["stale"]) for r in rows)   # входит в filled
    slots = filled + missing

    irrecoverable = [n for n in token_names + global_names
                     if store.is_irrecoverable(n)]
    irr_filled = sum(1 for r in rows for n in r["f"] if n in irrecoverable)

    print("=== Срез значений детекторов ===\n")
    print(f"  Токенов: {len(tokens)} · фич на токен: {len(token_names)} · "
          f"общих фич: {len(global_names)}")
    print(f"  Записано: {filled} из {slots} "
          f"({filled / max(slots, 1) * 100:.0f}%) · нет данных {missing}")
    print(f"  Из записанных помечено протухшими: {stale_n} — значение "
          f"сохранено, метка стоит")
    print(f"  Из них невосстановимых задним числом: {irr_filled} — "
          f"именно ради них всё и делается\n")

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(HIST_DIR, f"{day}.jsonl")

    if dry:
        print("  --dry-run: файл не тронут. Первая строка была бы такой:\n")
        print("  " + json.dumps(rows[0], ensure_ascii=False)[:400])
        return 0

    os.makedirs(HIST_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    size = os.path.getsize(path)
    existing = len(glob.glob(os.path.join(HIST_DIR, "*.jsonl")))
    print(f"✓ {path} · {size / 1024:.0f} КБ · всего дней накоплено: {existing}")

    os.makedirs("data/cache", exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "file": path,
            "tokens": len(tokens),
            "features_per_token": len(token_names),
            "global_features": len(global_names),
            "filled": filled, "missing": missing, "stale": stale_n,
            "coverage_pct": round(filled / max(slots, 1) * 100, 1),
            "irrecoverable_captured": irr_filled,
            "days_accumulated": existing,
            "why": "входы сохраняются, выводы нет: выводы пересчитываются "
                   "из входов, входы из выводов — нет",
        }, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true",
                    help="сколько уже накоплено и когда хватит на проверку")
    a = ap.parse_args()
    sys.exit(main(a.dry_run, a.stats))
