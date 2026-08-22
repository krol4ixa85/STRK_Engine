#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ic_analysis.py · v1.0 · 22.08.2026
STRK ENGINE · информационный коэффициент детекторов

ЗАЧЕМ ЭТО, ЕСЛИ УЖЕ ЕСТЬ ПРОВЕРКА ПРАВИЛ
-----------------------------------------
Проверка правил меряет попадание: сработало / не сработало, угадало /
не угадало. Это выбрасывает почти всю информацию.

Пример. Детектор выдал по десяти токенам числа от 0.1 до 9.8.
Правило «больше 5 — покупать» превращает их в пять единиц и пять
нулей. Различие между 5.1 и 9.8 стёрто, различие между 4.9 и 5.1
превращено в противоположные решения. Из-за этого на 348 прогнозах
почти невозможно ничего доказать — статистике нечем работать.

Квантовый стандарт меряет иначе. Информационный коэффициент (IC) —
это ранговая корреляция между ЧИСЛОМ детектора и будущей доходностью.
Никаких порогов. Число само говорит, несёт ли оно информацию.

ФУНДАМЕНТАЛЬНЫЙ ЗАКОН (Grinold, 1989)
--------------------------------------
    IR ≈ IC × √breadth

IR — отношение доходности к её разбросу (качество стратегии), breadth —
сколько НЕЗАВИСИМЫХ ставок делается за год.

Здесь стоит ловушка, в которую попадают чаще всего, и я в неё сначала
попал сам. Кажется: 42 токена, пересмотр раз в две недели — это
42 × 26 ≈ 1090 ставок в год, значит для IR = 1.0 хватит IC ≈ 0.030.
Три сотых, почти шум — звучит достижимо.

Неправда. Слово «независимых» в определении breadth не украшение.
Криптоактивы ходят вместе. На этих данных средняя парная корреляция
дневных доходностей — около 0.61, и одиннадцать активов дают примерно
ПОЛТОРЫ независимые ставки, а не одиннадцать:

    N_эфф ≈ N / (1 + (N-1)·ρ)

Отсюда честный порог на горизонте 14 дней — IC около 0.16, а не 0.03.
Разница в пять раз, и она решает, есть у тебя стратегия или нет.

Скрипт считает breadth ЧЕСТНО — через измеренную корреляцию. И рядом
с IC печатает то, что получилось бы на самом деле: доходность
портфеля, собранного по этой фиче, с deflated Sharpe.

ЧТО СЧИТАЕТСЯ ЗДЕСЬ
-------------------
Поперечный IC (cross-sectional): на каждую дату активы ранжируются по
фиче, отдельно — по будущей доходности, между рангами берётся
корреляция Спирмена. Получается ряд IC по датам, а из него:

  mean IC   средняя сила сигнала
  IC std    насколько она скачет
  IC-IR     mean/std — устойчивость
  t-stat    IC-IR × √число_периодов
  IR(потолок) mean IC × √breadth — верхняя оценка по закону

А рядом — проверка портфелем: во что это превращается на деле.

Наблюдения берутся ЧЕРЕЗ ГОРИЗОНТ, не каждый день. Ежедневная выборка
при горизонте 28 дней даёт соседние наблюдения, перекрытые на 27/28, и
раздувает t-статистику примерно в пять раз.

ЗАПУСК
------
  python3 scripts/ic_analysis.py
  python3 scripts/ic_analysis.py --horizon 14
  python3 scripts/ic_analysis.py --min-assets 8

ВХОД
----
  data/history/coinmetrics/<ASSET>.json

ВЫХОД
-----
  data/cache/ic_analysis.json
"""

import os
import sys
import json
import glob
import math
import argparse
import statistics
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:
    raise SystemExit("ERROR: pip install numpy")

HIST_DIR = "data/history/coinmetrics"
OUT_FILE = "data/cache/ic_analysis.json"

# Пока не накопится столько дней, перцентиль собственной истории
# считать не по чему.
WARMUP_DAYS = 400

# Сколько активов должно быть на дату, чтобы ранжирование имело смысл.
MIN_ASSETS = 6

HORIZONS = [14, 28, 56]

# Порог осмысленности: |t| >= 2 — примерно 5% уровень значимости.
T_MEANINGFUL = 2.0


def rank(xs):
    """Средние ранги, связки обрабатываются честно."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = rank(a), rank(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def build_features(series):
    """
    Строит по дням словарь фич. Все фичи — только из прошлого на эту дату.
    Возвращает список: {"date":..., "price":..., "f": {имя: число}}
    """
    rows = [r for r in series if r.get("PriceUSD")]
    if len(rows) < WARMUP_DAYS + 120:
        return []

    price = [r["PriceUSD"] for r in rows]
    mvrv = [r.get("CapMVRVCur") for r in rows]
    adr = [r.get("AdrActCnt") for r in rows]
    tx = [r.get("TxCnt") for r in rows]
    mcap = [r.get("CapMrktCurUSD") for r in rows]

    mvrv_clean = [v for v in mvrv if v]
    out = []
    for i in range(WARMUP_DAYS, len(rows)):
        f = {}

        for w in (7, 14, 28, 56, 90):
            if i - w >= 0 and price[i - w]:
                f[f"mom_{w}"] = price[i] / price[i - w] - 1.0

        if mvrv[i]:
            f["mvrv"] = mvrv[i]
            f["nupl"] = 1.0 - 1.0 / mvrv[i]
            hist = [v for v in mvrv[:i + 1] if v]
            if len(hist) > 100:
                f["mvrv_pct"] = sum(1 for v in hist if v < mvrv[i]) / len(hist) * 100

        # активность сети: изменение за 30 дней к среднему за 90
        if adr[i] and i - 30 >= 0 and adr[i - 30]:
            f["adr_30d"] = adr[i] / adr[i - 30] - 1.0
        if tx[i] and i - 30 >= 0 and tx[i - 30]:
            f["tx_30d"] = tx[i] / tx[i - 30] - 1.0

        # оборачиваемость: сколько транзакций на единицу капитализации
        if tx[i] and mcap[i]:
            f["tx_per_mcap"] = tx[i] / mcap[i] * 1e9

        # волатильность 30 дней — классический поперечный фактор
        if i - 30 >= 0:
            rets = [price[k] / price[k - 1] - 1.0
                    for k in range(i - 29, i + 1) if price[k - 1]]
            if len(rets) > 20:
                f["vol_30"] = statistics.pstdev(rets)

        out.append({"date": rows[i]["date"], "price": price[i], "f": f})
    _ = mvrv_clean
    return out


def load_all():
    data = {}
    for p in sorted(glob.glob(os.path.join(HIST_DIR, "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        rows = build_features(d.get("series") or [])
        if rows:
            data[os.path.basename(p)[:-5]] = {r["date"]: r for r in rows}
    return data


def cross_sectional_ic(data, feature, horizon, min_assets):
    """Ряд IC по датам. Наблюдения через горизонт — без перекрытия."""
    dates = sorted({d for a in data.values() for d in a})
    if not dates:
        return []
    idx = {d: i for i, d in enumerate(dates)}

    ics = []
    for i in range(0, len(dates) - horizon, horizon):
        d0, d1 = dates[i], dates[i + horizon]
        xs, ys = [], []
        for asset, byday in data.items():
            a, b = byday.get(d0), byday.get(d1)
            if not a or not b:
                continue
            v = a["f"].get(feature)
            if v is None or not a["price"] or not b["price"]:
                continue
            ret = b["price"] / a["price"] - 1.0
            if abs(ret) > 3.0:
                continue
            xs.append(v)
            ys.append(ret)
        if len(xs) >= min_assets:
            ic = spearman(xs, ys)
            if ic is not None:
                ics.append({"date": d0, "ic": ic, "n_assets": len(xs)})
    _ = idx
    return ics


def avg_pairwise_corr(data, dates):
    """
    Средняя парная корреляция дневных доходностей. Нужна, чтобы честно
    посчитать breadth: коррелированные активы — это не разные ставки.
    """
    series = {}
    for a, byday in data.items():
        series[a] = [(byday.get(d) or {}).get("price") for d in dates]
    names = sorted(series)
    cors = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pa, pb = series[names[i]], series[names[j]]
            ra, rb = [], []
            for k in range(1, len(dates)):
                if pa[k] and pa[k - 1] and pb[k] and pb[k - 1]:
                    ra.append(pa[k] / pa[k - 1] - 1)
                    rb.append(pb[k] / pb[k - 1] - 1)
            if len(ra) > 200:
                c = float(np.corrcoef(ra, rb)[0, 1])
                if not math.isnan(c):
                    cors.append(c)
    if not cors:
        return 0.0, len(names)
    rho = statistics.mean(cors)
    n = len(names)
    n_eff = n / (1 + (n - 1) * rho) if rho > -1 / (n - 1) else n
    return rho, n_eff


def summarize(ics, horizon, n_eff):
    n = len(ics)
    if n < 8:
        return None
    v = [x["ic"] for x in ics]
    mean = statistics.mean(v)
    sd = statistics.pstdev(v) if n > 1 else 0.0
    icir = mean / sd if sd > 0 else 0.0
    t = icir * math.sqrt(n)
    periods_per_year = 365.0 / horizon
    breadth = n_eff * periods_per_year
    return {
        "periods": n,
        "mean_ic": round(mean, 4),
        "ic_std": round(sd, 4),
        "ic_ir": round(icir, 3),
        "t_stat": round(t, 2),
        "hit_rate_pct": round(sum(1 for x in v if x > 0) / n * 100, 1),
        "breadth_per_year_honest": round(breadth, 1),
        "ic_needed_for_ir_1": round(1.0 / math.sqrt(breadth), 3) if breadth > 0 else None,
        "implied_ir_ceiling": round(mean * math.sqrt(breadth), 3),
        "meaningful": abs(t) >= T_MEANINGFUL,
    }


def portfolio_test(data, dates, feature, horizon, n_trials, frac=3):
    """
    Что получилось бы на самом деле. Каждые horizon дней активы делятся
    на трети по фиче; берётся нижняя треть против верхней (маркет-
    нейтрально) и просто нижняя треть в лонг.

    IC говорит «ранжирование несёт информацию». Здесь считается, во
    что это превращается после того, как ставки оказываются
    коррелированными. Числа обычно сильно скромнее — и именно они
    решают.
    """
    lo_r, hi_r, mkt_r = [], [], []
    for i in range(0, len(dates) - horizon, horizon):
        d0, d1 = dates[i], dates[i + horizon]
        rows = []
        for a, byday in data.items():
            x, y = byday.get(d0), byday.get(d1)
            if not x or not y:
                continue
            v = x["f"].get(feature)
            if v is None or not x["price"] or not y["price"]:
                continue
            r = y["price"] / x["price"] - 1
            if abs(r) > 3.0:
                continue
            rows.append((v, r))
        if len(rows) < MIN_ASSETS:
            continue
        rows.sort(key=lambda z: z[0])
        k = max(1, len(rows) // frac)
        lo_r.append(statistics.mean([r for _, r in rows[:k]]))
        hi_r.append(statistics.mean([r for _, r in rows[-k:]]))
        mkt_r.append(statistics.mean([r for _, r in rows]))

    if len(lo_r) < 10:
        return None

    lo = np.array(lo_r)
    ls = np.array(lo_r) - np.array(hi_r)
    per_year = 365.0 / horizon

    def stats(x):
        sd = x.std(ddof=1)
        sr = float(x.mean() / sd) if sd > 0 else 0.0
        d = {"median_pct": round(float(np.median(x)) * 100, 2),
             "sharpe_per_period": round(sr, 3),
             "ir_annual": round(sr * math.sqrt(per_year), 3)}
        try:
            import purgedcv as pcv
            res = pcv.deflated_sharpe_ratio_full(x, n_trials=n_trials,
                                                 var_sharpe=0.05)
            d["dsr"] = round(float(getattr(res, "probability",
                                           getattr(res, "dsr", 0.0))), 3)
        except ImportError:
            d["dsr"] = None
        return d

    return {
        "periods": len(lo_r),
        "long_bottom_third": stats(lo),
        "long_short": stats(ls),
        "market_median_pct": round(float(np.median(np.array(mkt_r))) * 100, 2),
    }


def main(horizons, min_assets):
    print("=== Информационный коэффициент детекторов ===\n")
    data = load_all()
    if not data:
        print(f"  Нет данных в {HIST_DIR}")
        print("  Сначала: python3 scripts/collectors/coinmetrics_history.py")
        return 1

    dates = sorted({d for a in data.values() for d in a})
    feats = sorted({f for a in data.values() for r in a.values() for f in r["f"]})
    rho, n_eff = avg_pairwise_corr(data, dates)

    print(f"  Активов: {len(data)} · фич: {len(feats)} · "
          f"минимум активов на дату: {min_assets}")
    print(f"  Наблюдения через горизонт, без перекрытия")
    print(f"  Средняя парная корреляция активов: {rho:.3f} → "
          f"независимых ставок {n_eff:.2f}, а не {len(data)}\n")

    n_trials = len(feats) * len(horizons)
    result = {}
    for h in horizons:
        rows = []
        for f in feats:
            ics = cross_sectional_ic(data, f, h, min_assets)
            if not ics:
                continue
            s = summarize(ics, h, n_eff)
            if s:
                s["feature"] = f
                rows.append(s)
        rows.sort(key=lambda z: -abs(z["mean_ic"]))
        result[str(h)] = rows

        need = rows[0]["ic_needed_for_ir_1"] if rows else 0
        print(f"### Горизонт {h} дней · для IR=1.0 нужен IC ≈ {need:.3f}")
        print(f"    {'ФИЧА':14}{'период':>8}{'ср.IC':>9}{'IC-IR':>8}"
              f"{'t':>7}  вывод")
        print("    " + "─" * 66)
        for r in rows:
            verdict = ("несёт информацию" if r["meaningful"]
                       else "не отличимо от шума")
            print(f"    {r['feature']:14}{r['periods']:>8}{r['mean_ic']:>+9.4f}"
                  f"{r['ic_ir']:>8.2f}{r['t_stat']:>+7.2f}  {verdict}")

        # Что получилось бы НА САМОМ ДЕЛЕ у фич, прошедших порог по t
        picks = [r["feature"] for r in rows if r["meaningful"]]
        if picks:
            print(f"\n    Проверка портфелем · поправка на {n_trials} проверок")
            print(f"    {'ФИЧА':14}{'медиана':>10}{'Sharpe':>9}{'IR/год':>9}"
                  f"{'DSR':>7}  что это")
            print("    " + "─" * 66)
            for f in picks:
                pt = portfolio_test(data, dates, f, h, n_trials)
                if not pt:
                    continue
                for label, key in (("нижняя треть в лонг", "long_bottom_third"),
                                   ("нижняя минус верхняя", "long_short")):
                    s = pt[key]
                    dsr = s.get("dsr")
                    ok = "преимущество" if (dsr or 0) >= 0.95 else "не доказано"
                    print(f"    {f:14}{s['median_pct']:>+9.2f}%"
                          f"{s['sharpe_per_period']:>9.3f}{s['ir_annual']:>+9.2f}"
                          f"{(dsr if dsr is not None else 0):>7.2f}  {label} · {ok}")
                result[str(h)] = [dict(r, portfolio=pt) if r["feature"] == f else r
                                  for r in result[str(h)]]
        print()

    strong = [r for rs in result.values() for r in rs if r["meaningful"]]
    proven = [r for r in strong
              if (r.get("portfolio") or {}).get("long_short", {}).get("dsr", 0) >= 0.95]
    print(f"  Фич с ненулевым IC: {len(strong)} из "
          f"{sum(len(v) for v in result.values())} проверок")
    print(f"  Из них доживших до портфеля с DSR ≥ 0.95: {len(proven)}")
    if strong and not proven:
        print("\n  Разрыв между этими двумя строками — главное, что тут видно.")
        print("  Ранжирование информацию несёт. Но ставки коррелированы,")
        print("  и на выходе преимущество не выживает.")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "coinmetrics_community · CC BY-NC 4.0",
            "assets": sorted(data),
            "method": "поперечный IC (Spearman), наблюдения через горизонт",
            "law": "IR ≈ IC × √breadth (Grinold 1989); breadth считается через эффективное число независимых ставок",
            "caveat": "t-статистика здесь без поправки на множественность "
                      "проверок; для решения о правиле нужен deflated Sharpe",
            "min_assets_per_date": min_assets,
            "avg_pairwise_corr": round(rho, 3),
            "effective_independent_assets": round(n_eff, 2),
            "n_trials_for_dsr": n_trials,
            "by_horizon": result,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=0)
    ap.add_argument("--min-assets", type=int, default=MIN_ASSETS)
    a = ap.parse_args()
    hs = [a.horizon] if a.horizon else HORIZONS
    sys.exit(main(hs, a.min_assets))