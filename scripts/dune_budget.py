#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dune_budget.py · v1.0 · 20.08.2026
STRK ENGINE · сторож кредитов Dune

ЗАЧЕМ
-----
При полной автоматизации без сторожа бюджет кончается тихо и в середине
месяца. Дальше каждый Dune-запрос возвращает 402, детекторы получают
пустоту, а движок продолжает считать решения — уже на дырявых данных.
Хуже неработающей системы только система, которая выглядит работающей.

Этот модуль вызывается ПЕРЕД каждым дорогим шагом и отвечает на один
вопрос: хватит ли бюджета. Если нет — шаг пропускается штатно, с записью
в лог, а не падает на 402.

КАК РАБОТАЕТ
------------
Расход не запрашивается у Dune (у API нет дешёвого эндпоинта остатка),
а ведётся локально: каждый прогон регистрирует свою оценочную стоимость
в data/cache/dune_budget.json. Счётчик сбрасывается по календарному месяцу.

Оценки заведомо приблизительные, поэтому заложен резерв: сторож начинает
блокировать при 85% бюджета, а не при 100%.

ПРИОРИТЕТЫ
----------
Когда бюджет на исходе, отключать надо не подряд, а по значимости.
Порядок отключения задан в TIERS: сначала умирает то, без чего решение
всё ещё принимается.

  tier 1 · composite, daily_token_scan   — ядро, отключается последним
  tier 2 · weekly_token_scan, strk_avnu  — важное, но переживёт пропуск
  tier 3 · force_all, token_scan_single  — роскошь, отключается первым

ИСПОЛЬЗОВАНИЕ В WORKFLOW
------------------------
    - name: Budget gate
      id: gate
      run: python3 scripts/dune_budget.py --check force_all_token_scan

    - name: Дорогой шаг
      if: steps.gate.outputs.allowed == 'true'
      run: ...

    - name: Register spend
      if: steps.gate.outputs.allowed == 'true'
      run: python3 scripts/dune_budget.py --spend force_all_token_scan

ЗАПУСК ВРУЧНУЮ
--------------
  python3 scripts/dune_budget.py --status
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

CACHE = "data/cache"
BUDGET_FILE = os.path.join(CACHE, "dune_budget.json")

MONTHLY_BUDGET = int(os.getenv("DUNE_MONTHLY_BUDGET", "4000"))

# Начинаем блокировать заранее — оценки стоимости неточные,
# резерв 15% защищает от того, что реальный расход окажется выше.
SOFT_LIMIT_PCT = 85

# Оценочная стоимость одного прогона и приоритет.
# Цифры взяты из наблюдённых прогонов: composite ~150, полный форс ~700.
TIERS = {
    "composite":            {"cost": 150, "tier": 1},
    "daily_token_scan":     {"cost": 40,  "tier": 1},
    "weekly_token_scan":    {"cost": 150, "tier": 2},
    "strk_avnu_scan":       {"cost": 20,  "tier": 2},
    "force_all_token_scan": {"cost": 700, "tier": 3},
    "token_scan_single":    {"cost": 15,  "tier": 3},
}

# При каком проценте израсходованного бюджета отключается каждый ярус
TIER_CUTOFF = {3: 50, 2: 75, 1: SOFT_LIMIT_PCT}


def load_state():
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        with open(BUDGET_FILE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("month") == month:
            return st
    except Exception:
        pass
    return {"month": month, "used": 0, "budget": MONTHLY_BUDGET, "runs": []}


def save_state(st):
    os.makedirs(CACHE, exist_ok=True)
    st["budget"] = MONTHLY_BUDGET
    st["remaining"] = max(0, MONTHLY_BUDGET - st["used"])
    st["used_pct"] = round(st["used"] / MONTHLY_BUDGET * 100, 1) if MONTHLY_BUDGET else 0
    st["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(BUDGET_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)


def gh_output(key, value):
    """Отдаём результат в GitHub Actions, если запущены там."""
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def check(job):
    st = load_state()
    meta = TIERS.get(job)
    if not meta:
        print(f"⚠ Задача '{job}' не описана в TIERS — пропускаю без блокировки")
        gh_output("allowed", "true")
        return 0

    used_pct = st["used"] / MONTHLY_BUDGET * 100 if MONTHLY_BUDGET else 0
    cutoff = TIER_CUTOFF[meta["tier"]]
    would_be = (st["used"] + meta["cost"]) / MONTHLY_BUDGET * 100

    print(f"Задача:    {job} (ярус {meta['tier']}, ~{meta['cost']} кредитов)")
    print(f"Израсходовано: {st['used']}/{MONTHLY_BUDGET} ({used_pct:.1f}%)")
    print(f"Порог яруса:   {cutoff}%")
    print(f"После прогона: {would_be:.1f}%")

    if used_pct >= cutoff:
        print(f"\n⛔ ЗАБЛОКИРОВАНО · ярус {meta['tier']} отключается при {cutoff}% "
              f"израсходованного бюджета.")
        print("   Это штатный пропуск, а не ошибка. Счётчик сбросится "
              "первого числа следующего месяца.")
        gh_output("allowed", "false")
        gh_output("reason", f"budget {used_pct:.0f}% >= tier cutoff {cutoff}%")
        return 0

    if would_be > SOFT_LIMIT_PCT:
        print(f"\n⛔ ЗАБЛОКИРОВАНО · прогон вывел бы расход на {would_be:.1f}%, "
              f"выше мягкого лимита {SOFT_LIMIT_PCT}%.")
        gh_output("allowed", "false")
        gh_output("reason", f"would exceed soft limit: {would_be:.0f}%")
        return 0

    print("\n✓ РАЗРЕШЕНО")
    gh_output("allowed", "true")
    gh_output("reason", "ok")
    return 0


def spend(job):
    st = load_state()
    meta = TIERS.get(job)
    cost = meta["cost"] if meta else 0
    st["used"] += cost
    st["runs"].append({
        "job": job,
        "cost": cost,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    # Держим последние 200 записей — истории на месяц хватает с запасом
    st["runs"] = st["runs"][-200:]
    save_state(st)
    print(f"Зарегистрирован расход: {job} · {cost} кредитов")
    print(f"Итого за месяц: {st['used']}/{MONTHLY_BUDGET} ({st['used_pct']}%)")
    return 0


def status():
    st = load_state()
    save_state(st)
    used_pct = st["used_pct"]

    print("=== Бюджет Dune ===\n")
    print(f"Месяц:         {st['month']}")
    print(f"Израсходовано: {st['used']}/{MONTHLY_BUDGET} ({used_pct}%)")
    print(f"Осталось:      {st['remaining']}\n")

    print("Статус ярусов:")
    for tier in (1, 2, 3):
        cutoff = TIER_CUTOFF[tier]
        jobs = [j for j, m in TIERS.items() if m["tier"] == tier]
        state = "работает" if used_pct < cutoff else "ОТКЛЮЧЁН"
        print(f"  ярус {tier} (порог {cutoff}%): {state}")
        for j in jobs:
            print(f"      {j} · ~{TIERS[j]['cost']}")

    if st["runs"]:
        print("\nПоследние прогоны:")
        for r in st["runs"][-8:]:
            print(f"  {r['at'][:16]}  {r['job']:24} {r['cost']:>5}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", type=str, help="можно ли запускать эту задачу")
    ap.add_argument("--spend", type=str, help="зарегистрировать расход задачи")
    ap.add_argument("--status", action="store_true", help="показать состояние бюджета")
    a = ap.parse_args()

    if a.check:
        sys.exit(check(a.check))
    elif a.spend:
        sys.exit(spend(a.spend))
    else:
        sys.exit(status())
