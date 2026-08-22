#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onchain_indicators.py · v1.0 · 22.08.2026
STRK ENGINE · оценочные метрики по длинной истории

ЧТО СЧИТАЕТ
-----------
  MVRV          отношение рыночной капитализации к реализованной.
                Берётся у Coin Metrics напрямую (CapMVRVCur).
                Больше 1 — держатели в среднем в прибыли.

  NUPL          доля нереализованной прибыли в капитализации.
                Выводится из MVRV: NUPL = 1 - 1/MVRV.
                Это тождество, а не приближение: NUPL по определению
                равен (MCap - Realized) / MCap.

  Realized Cap  капитализация / MVRV. Тоже тождество.

  MVRV Z-score  (MCap - Realized) / стандартное отклонение MCap.
                Классический индикатор перегрева: исторические вершины
                приходились на высокие значения, дна — на отрицательные.

  Перцентиль    где текущий MVRV в собственной истории актива.
                Универсального порога «дорого» не существует: у BTC и
                у LINK разные диапазоны. Сравнение с собственным
                прошлым честнее любой общей константы.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ
-----------------------
  SOPR — не считается. Ему нужны данные о потраченных выходах, на
  бесплатном тарифе Coin Metrics их нет (проверено: 403). Заменять
  его похожей формулой из доступных метрик было бы выдумыванием
  числа, которое выглядит как измерение.

ВАЖНО ПРО ГОРИЗОНТ
------------------
MVRV и NUPL — метрики цикла, а не свинга. Они говорят «дорого или
дёшево относительно того, по чём монеты последний раз двигались»,
и разворачиваются месяцами. Для входа на 3-14 дней это фон, а не
триггер. Их ценность в другом: по ним есть годы истории, а значит
их МОЖНО проверить — в отличие от правил на 26 неделях.

ЗАПУСК
------
  python3 scripts/onchain_indicators.py

ВХОД
----
  data/history/coinmetrics/<ASSET>.json   (coinmetrics_history.py)

ВЫХОД
-----
  data/cache/onchain_indicators.json
"""

import os
import sys
import json
import glob
import argparse
import statistics
from datetime import datetime, timezone

HIST_DIR = "data/history/coinmetrics"
OUT_FILE = "data/cache/onchain_indicators.json"

# Окно для стандартного отклонения в Z-score. Четыре года — примерно
# один цикл халвинга; на более коротком окне Z-score начинает
# реагировать на локальные движения, а он про цикл.
Z_WINDOW_DAYS = 1460

# Ниже этого числа дней истории выводы не делаем: перцентиль по
# полугоду данных — это не перцентиль, а иллюзия.
MIN_DAYS = 400


def load_asset(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def pct_rank(values, x):
    """Доля истории, которая была НИЖЕ текущего значения."""
    if not values:
        return None
    below = sum(1 for v in values if v < x)
    return round(below / len(values) * 100, 1)


def compute(asset_data):
    series = asset_data.get("series") or []
    rows = []
    for r in series:
        mvrv = r.get("CapMVRVCur")
        mcap = r.get("CapMrktCurUSD")
        if mvrv is None or mcap is None or mvrv <= 0:
            continue
        realized = mcap / mvrv
        rows.append({
            "date": r["date"],
            "price": r.get("PriceUSD"),
            "mvrv": mvrv,
            "mcap": mcap,
            "realized_cap": realized,
            # NUPL — тождество, не приближение
            "nupl": 1.0 - 1.0 / mvrv,
        })

    if len(rows) < MIN_DAYS:
        return {
            "status": "NOT_ENOUGH_HISTORY",
            "days": len(rows),
            "min_days_required": MIN_DAYS,
        }

    # Z-score по скользящему окну
    for i, r in enumerate(rows):
        lo = max(0, i - Z_WINDOW_DAYS + 1)
        window = [x["mcap"] for x in rows[lo:i + 1]]
        if len(window) >= 60:
            sd = statistics.pstdev(window)
            r["mvrv_z"] = round((r["mcap"] - r["realized_cap"]) / sd, 3) if sd > 0 else None
        else:
            r["mvrv_z"] = None

    last = rows[-1]
    mvrv_hist = [r["mvrv"] for r in rows]
    nupl_hist = [r["nupl"] for r in rows]

    # Человеческая подпись. Пороги — перцентили собственной истории,
    # а не общие константы: у каждого актива свой диапазон.
    p = pct_rank(mvrv_hist, last["mvrv"])
    if p is None:
        text = "истории мало для сравнения"
    elif p >= 90:
        text = f"MVRV выше, чем в {p:.0f}% собственной истории — исторически дорого"
    elif p >= 70:
        text = f"MVRV выше, чем в {p:.0f}% истории — выше своей нормы"
    elif p <= 10:
        text = f"MVRV ниже, чем в {100 - p:.0f}% истории — исторически дёшево"
    elif p <= 30:
        text = f"MVRV ниже своей нормы (перцентиль {p:.0f})"
    else:
        text = f"MVRV в середине своего диапазона (перцентиль {p:.0f})"

    return {
        "status": "OK",
        "days": len(rows),
        "first_date": rows[0]["date"],
        "last_date": last["date"],
        "current": {
            "date": last["date"],
            "price": last.get("price"),
            "mvrv": round(last["mvrv"], 3),
            "nupl": round(last["nupl"], 4),
            "realized_cap_usd": round(last["realized_cap"]),
            "mcap_usd": round(last["mcap"]),
            "mvrv_z": last.get("mvrv_z"),
            "mvrv_percentile": p,
            "nupl_percentile": pct_rank(nupl_hist, last["nupl"]),
        },
        "text_ru": text,
        "range": {
            "mvrv_min": round(min(mvrv_hist), 3),
            "mvrv_max": round(max(mvrv_hist), 3),
            "mvrv_median": round(statistics.median(mvrv_hist), 3),
        },
        # ряд для графика и для проверки правил — прореженный,
        # чтобы файл не рос до десятков мегабайт
        "history": [
            {"date": r["date"], "mvrv": round(r["mvrv"], 3),
             "nupl": round(r["nupl"], 4), "z": r.get("mvrv_z"),
             "price": r.get("price")}
            for r in rows[::7]
        ],
    }


def main(only=None):
    print("=== Ончейн-индикаторы по длинной истории ===\n")
    files = sorted(glob.glob(os.path.join(HIST_DIR, "*.json")))
    if not files:
        print(f"  Нет данных в {HIST_DIR}")
        print("  Сначала: python3 scripts/collectors/coinmetrics_history.py")
        return 1

    out = {}
    print(f"{'АКТИВ':8}{'ДНЕЙ':>7}{'MVRV':>8}{'NUPL':>8}{'Z':>7}{'ПЕРЦ':>7}  ЧТО ЭТО ЗНАЧИТ")
    print("  " + "─" * 88)

    for path in files:
        asset = os.path.basename(path)[:-5]
        if only and asset.lower() not in only:
            continue
        data = load_asset(path)
        if not data:
            continue
        res = compute(data)
        out[asset] = res
        if res.get("status") != "OK":
            print(f"{asset:8}{res.get('days', 0):>7}   истории мало "
                  f"(нужно {res.get('min_days_required')})")
            continue
        c = res["current"]
        z = c.get("mvrv_z")
        print(f"{asset:8}{res['days']:>7}{c['mvrv']:>8.2f}{c['nupl']:>8.2f}"
              f"{(z if z is not None else 0):>7.2f}{c['mvrv_percentile']:>7.0f}  {res['text_ru']}")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "coinmetrics_community · CC BY-NC 4.0",
            "z_window_days": Z_WINDOW_DAYS,
            "note": "SOPR не считается: нужны данные о потраченных выходах, "
                    "на бесплатном тарифе их нет",
            "horizon_note": "MVRV и NUPL — метрики цикла, разворачиваются "
                            "месяцами. Для свинга 3-14 дней это фон, не триггер",
            "assets": out,
        }, f, indent=2, ensure_ascii=False)

    ok = sum(1 for v in out.values() if v.get("status") == "OK")
    print(f"\n  Посчитано: {ok} из {len(out)}")
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", type=str, default="")
    a = ap.parse_args()
    only = [x.strip().lower() for x in a.asset.split(",") if x.strip()] or None
    sys.exit(main(only))