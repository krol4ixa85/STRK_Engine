#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
momentum_stress.py · v1.0 · 22.08.2026
STRK ENGINE · попытка сломать единственную выжившую гипотезу

ЧТО ПРОВЕРЯЕТСЯ
---------------
Стратегия «2/2» из Dobrynskaya · Cryptocurrency Momentum and Reversal:
сортировка по доходности за прошлые 2 недели, держим 2 недели, длинная
верхняя часть против короткой нижней.

На первом прогоне (factor_zoo) это оказалось лучшим, что есть на
горизонте 14 дней: медиана +0.81% за период после издержек, t = +2.04,
IR 0.92 годовых. Важно, что гипотеза названа в статье ЗАРАНЕЕ — её не
искали перебором, поэтому поправка на сто попыток к ней не относится.

ЗАЧЕМ ЭТОТ ФАЙЛ
---------------
Один хороший прогон ничего не значит. За эту неделю трижды повторился
один сценарий: сильная цифра внутри выборки умирала снаружи. MVRV
(DSR 1.00 → 0.74), vol_30 (t −5.3 → DSR 0.00), граф опережения
(73 значимых ребра → ноль вне выборки).

Поэтому здесь гипотезу пытаются СЛОМАТЬ семью способами. Если она
переживёт — это кандидат в первое правило со статусом measured_pass.
Если нет — лучше узнать сейчас.

СЕМЬ ПРОВЕРОК
-------------
  1. Подпериоды          работает ли во все три трети истории
  2. Размер корзины      треть / 30% как в статье / квинтиль
  3. Издержки            5 / 10 / 20 / 30 базисных пунктов
  4. Ноги                длинная и короткая отдельно
  5. Взвешивание         равное против взвешивания по обороту
  6. Смещение старта     не артефакт ли конкретной сетки дат
  7. Без лучших периодов держится ли без двух самых удачных окон

ЗАПУСК
------
  python3 scripts/momentum_stress.py
  python3 scripts/momentum_stress.py --formation 14 --holding 14

ВХОД
----
  data/history/hl/<TOKEN>.json

ВЫХОД
-----
  data/cache/momentum_stress.json
"""

import os
import sys
import json
import math
import argparse
import statistics
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from factor_zoo import load_prices, build_panel, WARMUP, MIN_TOKENS
except ImportError:
    raise SystemExit("ERROR: рядом должен лежать scripts/factor_zoo.py")

OUT_FILE = "data/cache/momentum_stress.json"
DEFAULT_COST_BPS = 10.0


def sharpe(xs):
    if len(xs) < 3:
        return 0.0
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) * math.sqrt(len(xs) / (len(xs) - 1))
    return m / sd if sd > 0 else 0.0


def tstat(xs):
    return sharpe(xs) * math.sqrt(len(xs)) if len(xs) > 2 else 0.0


def run(dates, panel, formation, holding, *, frac=3, cost_bps=DEFAULT_COST_BPS,
        weight="equal", offset=0, lo_hi=None):
    """
    Один прогон стратегии. Возвращает списки доходностей по периодам:
    длинно-короткую, только длинную, только короткую и рынок.

    frac=3 — трети, frac=3.33 — верхние и нижние 30% как в статье.
    """
    fresh = dates[-4] if len(dates) > 4 else dates[0]
    ls, lo_r, hi_r, mkt = [], [], [], []
    stamps = []

    start = WARMUP + offset
    for i in range(start, len(dates) - holding, holding):
        rows = []
        for t, d in panel.items():
            if d["last_date"] < fresh:
                continue
            c, v = d["c"], d["v"]
            if i - formation < 0:
                continue
            p0, pf, p1 = c[i], c[i - formation], c[i + holding]
            if p0 is None or pf is None or p1 is None or not p0 or not pf:
                continue
            sig = p0 / pf - 1
            ret = p1 / p0 - 1
            if abs(ret) > 3:
                continue
            w = 1.0
            if weight == "dollar_volume":
                win = [c[k] * v[k] for k in range(max(0, i - 29), i + 1)
                       if c[k] is not None]
                w = statistics.mean(win) if win else 1.0
            rows.append((sig, ret, w))
        if len(rows) < MIN_TOKENS:
            continue
        rows.sort(key=lambda z: z[0])
        q = max(1, int(len(rows) / frac))
        low, high = rows[:q], rows[-q:]

        def agg(part):
            tot = sum(x[2] for x in part) or 1.0
            return sum(x[1] * x[2] for x in part) / tot

        lo_v, hi_v = agg(low), agg(high)
        c_ls = 4 * cost_bps / 10000.0
        c_one = 2 * cost_bps / 10000.0
        ls.append((hi_v - lo_v) - c_ls)
        hi_r.append(hi_v - c_one)
        lo_r.append(lo_v - c_one)
        mkt.append(statistics.mean([x[1] for x in rows]))
        stamps.append(dates[i])

    if lo_hi is not None:
        lo_hi.extend(stamps)
    return ls, hi_r, lo_r, mkt


def line(label, xs, holding, extra=""):
    if len(xs) < 3:
        return f"    {label:34}{len(xs):>5}   мало наблюдений"
    per_year = 365.0 / holding
    return (f"    {label:34}{len(xs):>5}"
            f"{statistics.median(xs) * 100:>+9.2f}%"
            f"{sharpe(xs) * math.sqrt(per_year):>+8.2f}"
            f"{tstat(xs):>+7.2f}"
            f"{sum(1 for x in xs if x > 0) / len(xs) * 100:>7.0f}%  {extra}")


HEAD = (f"    {'ВАРИАНТ':34}{'n':>5}{'медиана':>10}{'IR/год':>8}{'t':>7}"
        f"{'плюс':>7}")


def main(formation, holding, cost_bps):
    prices = load_prices()
    if not prices:
        print("  Нет свечей. Сначала: python3 scripts/collectors/hl_history.py")
        return 1
    dates, panel = build_panel(prices)

    print("=== Попытка сломать стратегию 2/2 ===\n")
    print(f"  Сортировка по прошлым {formation} дням, держим {holding} дней")
    print(f"  Источник гипотезы: Dobrynskaya · Cryptocurrency Momentum and Reversal")
    print(f"  Токенов: {len(prices)} · дней: {len(dates)} · "
          f"издержки {cost_bps / 100:.2f}% с каждой стороны\n")

    res = {}
    base_ls, base_hi, base_lo, base_mkt = run(dates, panel, formation, holding,
                                              cost_bps=cost_bps)
    if len(base_ls) < 12:
        print("  Слишком мало периодов для проверки")
        return 1

    res["base"] = {"n": len(base_ls),
                   "median_pct": round(statistics.median(base_ls) * 100, 2),
                   "ir_annual": round(sharpe(base_ls) * math.sqrt(365 / holding), 2),
                   "t": round(tstat(base_ls), 2)}

    # ── 1 · подпериоды
    print("### 1 · Держится ли во все три трети истории\n")
    print(HEAD)
    print("    " + "─" * 72)
    k = len(base_ls) // 3
    thirds = [base_ls[:k], base_ls[k:2 * k], base_ls[2 * k:]]
    res["thirds"] = []
    for n, part in enumerate(thirds, 1):
        print(line(f"треть {n}", part, holding))
        res["thirds"].append({"part": n, "n": len(part),
                              "median_pct": round(statistics.median(part) * 100, 2)
                              if part else None,
                              "t": round(tstat(part), 2)})
    print(line("вся история", base_ls, holding, "← база"))

    # ── 2 · размер корзины
    print("\n### 2 · Размер корзины\n")
    print(HEAD)
    print("    " + "─" * 72)
    res["buckets"] = {}
    for lbl, fr in (("треть (33%)", 3), ("верх/низ 30% · как в статье", 10 / 3),
                    ("квинтиль (20%)", 5), ("половина (50%)", 2)):
        xs, _, _, _ = run(dates, panel, formation, holding, frac=fr,
                          cost_bps=cost_bps)
        print(line(lbl, xs, holding))
        res["buckets"][lbl] = {"n": len(xs), "t": round(tstat(xs), 2)}

    # ── 3 · издержки
    print("\n### 3 · Чувствительность к издержкам\n")
    print(HEAD)
    print("    " + "─" * 72)
    res["costs"] = {}
    for cb in (0, 5, 10, 20, 30):
        xs, _, _, _ = run(dates, panel, formation, holding, cost_bps=cb)
        note = "← реальность где-то здесь" if cb in (10, 20) else ""
        print(line(f"{cb / 100:.2f}% с каждой стороны", xs, holding, note))
        res["costs"][cb] = {"median_pct": round(statistics.median(xs) * 100, 2),
                            "t": round(tstat(xs), 2)}

    # ── 4 · ноги отдельно
    print("\n### 4 · Откуда берётся результат\n")
    print(HEAD)
    print("    " + "─" * 72)
    print(line("длинная нога (верх)", base_hi, holding))
    print(line("короткая нога (низ)", [-x for x in base_lo], holding, "знак развёрнут"))
    print(line("рынок (все токены)", base_mkt, holding))
    res["legs"] = {
        "long_t": round(tstat(base_hi), 2),
        "short_t": round(tstat([-x for x in base_lo]), 2),
        "market_median_pct": round(statistics.median(base_mkt) * 100, 2),
    }

    # ── 5 · взвешивание
    print("\n### 5 · Взвешивание\n")
    print(HEAD)
    print("    " + "─" * 72)
    print(line("равные веса", base_ls, holding))
    xs, _, _, _ = run(dates, panel, formation, holding, weight="dollar_volume",
                      cost_bps=cost_bps)
    print(line("по обороту · как в статье", xs, holding))
    res["weighting_dollar_t"] = round(tstat(xs), 2)

    # ── 6 · смещение сетки дат
    print("\n### 6 · Не артефакт ли конкретной сетки дат\n")
    print(HEAD)
    print("    " + "─" * 72)
    offs = []
    for off in range(0, holding, max(1, holding // 5)):
        xs, _, _, _ = run(dates, panel, formation, holding, offset=off,
                          cost_bps=cost_bps)
        print(line(f"старт сдвинут на {off} дней", xs, holding))
        offs.append({"offset": off, "n": len(xs), "t": round(tstat(xs), 2),
                     "median_pct": round(statistics.median(xs) * 100, 2)})
    res["offsets"] = offs
    ts = [o["t"] for o in offs]
    print(f"\n    Разброс t по сдвигам: от {min(ts):+.2f} до {max(ts):+.2f}")

    # ── 7 · без лучших периодов
    print("\n### 7 · Держится ли без двух самых удачных окон\n")
    print(HEAD)
    print("    " + "─" * 72)
    trimmed = sorted(base_ls)[:-2]
    print(line("вся история", base_ls, holding))
    print(line("без двух лучших периодов", trimmed, holding))
    res["without_top2_t"] = round(tstat(trimmed), 2)

    # ── итог
    checks = {
        "все три трети положительны": all(
            statistics.median(p) > 0 for p in thirds if len(p) > 2),
        "переживает 20 б.п. издержек": res["costs"][20]["t"] > 1.5,
        "не зависит от сетки дат": min(ts) > 1.0,
        "держится без двух лучших окон": res["without_top2_t"] > 1.5,
        "работает при взвешивании по обороту": res["weighting_dollar_t"] > 1.0,
    }
    print("\n\n### Итог\n")
    for k2, v in checks.items():
        print(f"    {'ПРОЙДЕНО' if v else 'НЕ ПРОЙДЕНО':14} {k2}")
    passed = sum(1 for v in checks.values() if v)
    res["checks"] = checks
    res["passed"] = passed
    print(f"\n    Пройдено {passed} из {len(checks)}")
    if passed == len(checks):
        print("    Гипотеза выдержала все проверки. Это кандидат в первое")
        print("    правило со статусом measured_pass.")
    elif passed >= 3:
        print("    Часть проверок не пройдена. Не правило, но и не шум —")
        print("    держать в testing и наблюдать.")
    else:
        print("    Сломалась. Хорошо, что до денег, а не после.")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "hypothesis": f"момент {formation}д, держим {holding}д, "
                          f"верх минус низ",
            "source": "Dobrynskaya · Cryptocurrency Momentum and Reversal",
            "pre_registered": True,
            "universe": sorted(prices), "days": len(dates),
            "cost_bps_per_side": cost_bps,
            "results": res,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--formation", type=int, default=14)
    ap.add_argument("--holding", type=int, default=14)
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    a = ap.parse_args()
    sys.exit(main(a.formation, a.holding, a.cost_bps))