#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
signal_graph.py · v1.0 · 22.08.2026
STRK ENGINE · сколько на самом деле даёт усреднение детекторов,
              и есть ли в графе активов опережение

ВОПРОС, НА КОТОРЫЙ ЭТО ОТВЕЧАЕТ
--------------------------------
«У меня много детекторов — может, усреднить их и получится решение?»

Формула усреднения известна и та же самая, что для breadth:

    IC(смесь) = IC(среднее) × √( k / (1 + (k-1)·ρ) )

k — сколько детекторов, ρ — средняя корреляция МЕЖДУ НИМИ.

Смысл прямой: усреднение помогает ровно настолько, насколько детекторы
смотрят в РАЗНЫЕ стороны. Двадцать детекторов, посчитанных из одной и
той же цены, — это один детектор, переписанный двадцатью способами.
Корень из двадцати там не появится.

Здесь ρ не предполагается, а МЕРЯЕТСЯ на живых данных. И сразу
считается, во сколько раз усреднение способно улучшить результат —
верхняя граница, до всякой реализации.

ВТОРАЯ ЧАСТЬ · ГРАФ ОПЕРЕЖЕНИЯ
-------------------------------
Единственная графовая идея, которая может дать НОВЫЙ сигнал, а не
переупаковать старый: опережает ли какой-то актив другие. Если движение
BTC систематически предсказывает движение альта через N дней — это
информация, которой нет ни в одном текущем детекторе.

Проверяется корреляцией доходности актива A на окне [t-w, t] с
доходностью актива B на окне [t, t+h]. Считается в обе стороны: если
A→B и B→A одинаковы, это просто общая корреляция, а не опережение.
Разница между направлениями — вот что интересно.

ЧЕГО ЗДЕСЬ НЕТ
--------------
Оптимизации портфеля. Иерархический паритет риска (HRP), вложенная
кластеризация (NCO) и прочие графовые методы отвечают на вопрос «как
распределить деньги между активами, когда сигнал уже есть». Готовые
реализации: skfolio (BSD-3), riskfolio-lib. Брать их имеет смысл
ПОСЛЕ того, как найдётся сигнал, а не вместо.

ЗАПУСК
------
  python3 scripts/signal_graph.py
  python3 scripts/signal_graph.py --horizon 7 --lookback 7

ВХОД
----
  data/history/hl/<TOKEN>.json    (scripts/collectors/hl_history.py)

ВЫХОД
-----
  data/cache/signal_graph.json
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

HIST_DIR = "data/history/hl"
OUT_FILE = "data/cache/signal_graph.json"

MIN_OVERLAP = 250          # минимум общих дней, иначе корреляция — фантазия
T_MEANINGFUL = 2.5         # порог по t для пары в графе опережения
DSR_STRONG = 0.95          # порог, выше которого преимущество считается доказанным


# ─────────────────────────────────────────────────────────────
# фичи-детекторы · те же семейства, что в платформе

def detector_series(cs):
    """
    По дневным свечам считает набор «детекторов» — по одному числу на
    день. Каждый из них соответствует живому детектору платформы.
    Все значения — только из прошлого.
    """
    c = [r["c"] for r in cs]
    h = [r["h"] for r in cs]
    l = [r["l"] for r in cs]
    v = [r["v"] for r in cs]
    n = len(c)
    out = {k: [None] * n for k in
           ("mom_3", "mom_7", "mom_14", "mom_30",
            "accel", "vol_ratio", "vol_accel", "rsi",
            "pct_from_high", "pct_from_low", "vol_30", "range_pos")}

    for i in range(n):
        if i < 62:
            continue
        out["mom_3"][i] = c[i] / c[i - 3] - 1
        out["mom_7"][i] = c[i] / c[i - 7] - 1
        out["mom_14"][i] = c[i] / c[i - 14] - 1
        out["mom_30"][i] = c[i] / c[i - 30] - 1
        out["accel"][i] = (c[i] / c[i - 3] - 1) - (c[i - 3] / c[i - 6] - 1)

        v3 = sum(v[i - 2:i + 1]) / 3
        v7 = sum(v[i - 6:i + 1]) / 7
        v30 = sum(v[i - 29:i + 1]) / 30
        out["vol_ratio"][i] = v3 / max(v30, 1e-9)
        out["vol_accel"][i] = v3 / max(v7, 1e-9)

        gains = [max(c[k] - c[k - 1], 0) for k in range(i - 13, i + 1)]
        losses = [max(c[k - 1] - c[k], 0) for k in range(i - 13, i + 1)]
        ag, al = sum(gains) / 14, sum(losses) / 14
        out["rsi"][i] = 100 - 100 / (1 + ag / max(al, 1e-9))

        hi = max(h[i - 13:i + 1])
        lo = min(l[i - 13:i + 1])
        out["pct_from_high"][i] = c[i] / hi - 1 if hi > 0 else None
        out["pct_from_low"][i] = c[i] / lo - 1 if lo > 0 else None
        out["range_pos"][i] = (c[i] - lo) / (hi - lo) if hi > lo else 0.5

        rets = [c[k] / c[k - 1] - 1 for k in range(i - 29, i + 1)]
        out["vol_30"][i] = statistics.pstdev(rets)

    return out


def corr(a, b):
    pa, pb = [], []
    for x, y in zip(a, b):
        if x is not None and y is not None:
            pa.append(x)
            pb.append(y)
    if len(pa) < MIN_OVERLAP:
        return None
    sa, sb = np.std(pa), np.std(pb)
    if sa == 0 or sb == 0:
        return None
    c = float(np.corrcoef(pa, pb)[0, 1])
    return None if math.isnan(c) else c


def load():
    data = {}
    for p in sorted(glob.glob(os.path.join(HIST_DIR, "*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        cs = j.get("candles") or []
        if len(cs) > 400:
            data[j.get("token") or os.path.basename(p)[:-5]] = cs
    return data


# ─────────────────────────────────────────────────────────────
# часть 1 · корреляция между детекторами

def detector_correlation(data):
    """
    Средняя |корреляция| между детекторами, посчитанная внутри каждого
    токена и усреднённая по токенам. Знак не важен — важно, несут ли
    два детектора одно и то же.
    """
    names = None
    per_pair = {}
    for token, cs in data.items():
        d = detector_series(cs)
        if names is None:
            names = sorted(d)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                c = corr(d[names[i]], d[names[j]])
                if c is not None:
                    per_pair.setdefault((names[i], names[j]), []).append(abs(c))

    pairs = [(k, statistics.mean(v)) for k, v in per_pair.items() if v]
    if not pairs:
        return None
    rho = statistics.mean([p[1] for p in pairs])
    k = len(names)
    n_eff = k / (1 + (k - 1) * rho) if rho > 0 else k
    pairs.sort(key=lambda z: -z[1])
    return {
        "detectors": k,
        "pairs_measured": len(pairs),
        "avg_abs_corr": round(rho, 3),
        "effective_independent": round(n_eff, 2),
        "gain_from_averaging_x": round(math.sqrt(n_eff), 2),
        "gain_if_independent_x": round(math.sqrt(k), 2),
        "most_duplicated": [{"a": a, "b": b, "corr": round(c, 3)}
                            for (a, b), c in pairs[:8]],
        "least_duplicated": [{"a": a, "b": b, "corr": round(c, 3)}
                             for (a, b), c in pairs[-5:]],
    }


# ─────────────────────────────────────────────────────────────
# часть 2 · граф опережения

def lead_lag(data, lookback, horizon, min_pairs, neutralize=False):
    """
    Для каждой пары (A, B): корреляция прошлого движения A с будущим
    движением B. Наблюдения через горизонт — без перекрытия.

    neutralize=True вычитает из доходности каждого актива среднюю
    доходность рынка за тот же период. Это ГЛАВНАЯ проверка: если
    «A опережает B» держится только пока в цифрах сидит общий рынок,
    то никакого опережения нет — есть одна волна, накрывшая всех.
    """
    dates = sorted({r["date"] for cs in data.values() for r in cs})
    idx = {t: {r["date"]: r["c"] for r in cs} for t, cs in data.items()}
    toks = sorted(data)

    def ret(t, d_from, d_to):
        p0, p1 = idx[t].get(d_from), idx[t].get(d_to)
        if not p0 or not p1:
            return None
        r = p1 / p0 - 1
        return None if abs(r) > 3 else r

    # Средняя доходность рынка на каждом окне — считается один раз
    mkt = {}
    if neutralize:
        for i in range(lookback, len(dates) - horizon, horizon):
            for d_from, d_to in ((dates[i - lookback], dates[i]),
                                 (dates[i], dates[i + horizon])):
                if (d_from, d_to) in mkt:
                    continue
                rs = [r for t in toks if (r := ret(t, d_from, d_to)) is not None]
                mkt[(d_from, d_to)] = statistics.mean(rs) if len(rs) >= 8 else None

    edges = []
    for a in toks:
        for b in toks:
            if a == b:
                continue
            xs, ys = [], []
            for i in range(lookback, len(dates) - horizon, horizon):
                d_prev, d0, d1 = dates[i - lookback], dates[i], dates[i + horizon]
                ra = ret(a, d_prev, d0)
                rb = ret(b, d0, d1)
                if ra is None or rb is None:
                    continue
                if neutralize:
                    ma, mb = mkt.get((d_prev, d0)), mkt.get((d0, d1))
                    if ma is None or mb is None:
                        continue
                    ra, rb = ra - ma, rb - mb
                xs.append(ra)
                ys.append(rb)
            if len(xs) < min_pairs:
                continue
            sa, sb = np.std(xs), np.std(ys)
            if sa == 0 or sb == 0:
                continue
            c = float(np.corrcoef(xs, ys)[0, 1])
            if math.isnan(c):
                continue
            t = c * math.sqrt(max(len(xs) - 2, 1)) / math.sqrt(max(1 - c * c, 1e-9))
            edges.append({"from": a, "to": b, "n": len(xs),
                          "corr": round(c, 3), "t": round(t, 2)})

    # Асимметрия: настоящее опережение — это когда A→B сильнее, чем B→A.
    # Одинаковые значения в обе стороны означают общую корреляцию.
    by_pair = {(e["from"], e["to"]): e for e in edges}
    asym = []
    for (a, b), e in by_pair.items():
        back = by_pair.get((b, a))
        if not back or a > b:
            continue
        diff = e["corr"] - back["corr"]
        asym.append({
            "a": a, "b": b,
            "a_leads_b": e["corr"], "b_leads_a": back["corr"],
            "asymmetry": round(diff, 3),
            "n": min(e["n"], back["n"]),
            "t_forward": e["t"], "t_backward": back["t"],
        })
    asym.sort(key=lambda z: -abs(z["asymmetry"]))
    edges.sort(key=lambda z: -abs(z["t"]))
    return edges, asym


def out_of_sample(data, lookback, horizon, split, min_corr=0.30):
    """
    ГЛАВНАЯ ПРОВЕРКА ГРАФА.

    Рёбра подбираются на ПЕРВОЙ части истории, а торгуются на ВТОРОЙ,
    которую подбор не видел. Без этого граф всегда выглядит блестяще:
    корреляции подогнаны под те же данные, на которых их измеряют.

    Каждый период: по рёбрам считается предсказание для каждого актива,
    активы ранжируются, берётся верхняя треть (и верх минус низ).
    """
    dates = sorted({r["date"] for cs in data.values() for r in cs})
    idx = {t: {r["date"]: r["c"] for r in cs} for t, cs in data.items()}
    toks = sorted(data)

    def ret(t, a, b):
        p0, p1 = idx[t].get(a), idx[t].get(b)
        if not p0 or not p1:
            return None
        r = p1 / p0 - 1
        return None if abs(r) > 3 else r

    periods = [(dates[i - lookback], dates[i], dates[i + horizon])
               for i in range(lookback, len(dates) - horizon, horizon)]

    nr = {}
    for dp, d0, d1 in periods:
        for pair in ((dp, d0), (d0, d1)):
            if pair in nr:
                continue
            rs = {t: ret(t, pair[0], pair[1]) for t in toks}
            vals = [v for v in rs.values() if v is not None]
            nr[pair] = ({t: v - statistics.mean(vals)
                         for t, v in rs.items() if v is not None}
                        if len(vals) >= 8 else None)

    k = int(len(periods) * split)
    train, test = periods[:k], periods[k:]

    edges = {}
    for a in toks:
        for b in toks:
            if a == b:
                continue
            xs, ys = [], []
            for dp, d0, d1 in train:
                ra = (nr.get((dp, d0)) or {}).get(a)
                rb = (nr.get((d0, d1)) or {}).get(b)
                if ra is None or rb is None:
                    continue
                xs.append(ra)
                ys.append(rb)
            if len(xs) < 25 or np.std(xs) == 0 or np.std(ys) == 0:
                continue
            c = float(np.corrcoef(xs, ys)[0, 1])
            if not math.isnan(c) and abs(c) >= min_corr:
                edges.setdefault(b, []).append((a, c))

    hi_r, lo_r = [], []
    for dp, d0, d1 in test:
        past, fut = nr.get((dp, d0)), nr.get((d0, d1))
        if not past or not fut:
            continue
        pred = {}
        for b, srcs in edges.items():
            vals = [c * past[a] for a, c in srcs if a in past]
            if vals:
                pred[b] = statistics.mean(vals)
        rows = sorted(((p, fut[b]) for b, p in pred.items() if b in fut),
                      key=lambda z: z[0])
        if len(rows) < 9:
            continue
        q = max(1, len(rows) // 3)
        lo_r.append(statistics.mean([r for _, r in rows[:q]]))
        hi_r.append(statistics.mean([r for _, r in rows[-q:]]))

    if len(hi_r) < 10:
        return None

    per_year = 365.0 / horizon
    n_trials = len(toks) * (len(toks) - 1)

    def stats(x):
        x = np.asarray(x, float)
        sd = x.std(ddof=1)
        sr = float(x.mean() / sd) if sd > 0 else 0.0
        d = {"median_pct": round(float(np.median(x)) * 100, 2),
             "sharpe": round(sr, 3),
             "ir_annual": round(sr * math.sqrt(per_year), 2)}
        try:
            import purgedcv as pcv
            r = pcv.deflated_sharpe_ratio_full(x, n_trials=n_trials,
                                               var_sharpe=0.05)
            d["dsr"] = round(float(getattr(r, "probability",
                                           getattr(r, "dsr", 0.0))), 3)
        except ImportError:
            d["dsr"] = None
        return d

    return {
        "split": split,
        "train_periods": len(train),
        "test_periods": len(hi_r),
        "edges_fitted": sum(len(v) for v in edges.values()),
        "top_third": stats(hi_r),
        "long_short": stats(np.array(hi_r) - np.array(lo_r)),
    }


def main(lookback, horizon, min_pairs):
    data = load()
    if not data:
        print(f"  Нет свечей в {HIST_DIR}")
        print("  Сначала: python3 scripts/collectors/hl_history.py")
        return 1

    print("=== Усреднение детекторов и граф опережения ===\n")
    print(f"  Токенов: {len(data)} · дней суммарно: "
          f"{sum(len(c) for c in data.values())}\n")

    # ── часть 1
    print("### Сколько детекторов на самом деле разных\n")
    dc = detector_correlation(data)
    if not dc:
        print("  Не хватило данных")
        return 1

    print(f"  Детекторов: {dc['detectors']} · пар измерено: {dc['pairs_measured']}")
    print(f"  Средняя |корреляция| между ними: {dc['avg_abs_corr']}")
    print(f"  Независимых по существу: {dc['effective_independent']} "
          f"из {dc['detectors']}\n")
    print(f"  Усреднение улучшило бы результат в {dc['gain_from_averaging_x']}×")
    print(f"  Если бы детекторы были независимы — в {dc['gain_if_independent_x']}×\n")
    print("  Самые дублирующие друг друга пары:")
    for p in dc["most_duplicated"][:6]:
        print(f"    {p['a']:16} ↔ {p['b']:16} {p['corr']:+.3f}")
    print("\n  Наименее пересекающиеся:")
    for p in dc["least_duplicated"][:4]:
        print(f"    {p['a']:16} ↔ {p['b']:16} {p['corr']:+.3f}")

    # ── часть 2
    print(f"\n\n### Граф опережения · прошлые {lookback} дней → следующие "
          f"{horizon} дней\n")
    edges, asym = lead_lag(data, lookback, horizon, min_pairs)
    edges_n, asym_n = lead_lag(data, lookback, horizon, min_pairs, neutralize=True)
    if not edges:
        print("  Не хватило общих дат")
    else:
        strong = [e for e in edges if abs(e["t"]) >= T_MEANINGFUL]
        print(f"  Рёбер посчитано: {len(edges)} · с |t| ≥ {T_MEANINGFUL}: "
              f"{len(strong)}")
        print(f"  При {len(edges)} проверках по чистой случайности "
              f"ожидается ≈{len(edges) * 0.012:.0f} таких рёбер\n")
        if strong:
            print(f"    {'ОПЕРЕЖАЕТ':10}{'→':3}{'КОГО':10}{'n':>6}{'корр':>8}{'t':>7}")
            print("    " + "─" * 44)
            for e in strong[:15]:
                print(f"    {e['from']:10}{'→':3}{e['to']:10}{e['n']:>6}"
                      f"{e['corr']:>+8.3f}{e['t']:>+7.2f}")

        print("\n  Наибольшая асимметрия — где опережение односторонне:")
        print(f"    {'ПАРА':22}{'A→B':>8}{'B→A':>8}{'разница':>9}{'n':>6}")
        print("    " + "─" * 53)
        for p in asym[:8]:
            print(f"    {p['a'] + ' / ' + p['b']:22}{p['a_leads_b']:>+8.3f}"
                  f"{p['b_leads_a']:>+8.3f}{p['asymmetry']:>+9.3f}{p['n']:>6}")

        real = [p for p in asym if abs(p["asymmetry"]) > 0.15
                and abs(p["t_forward"]) >= T_MEANINGFUL]
        print(f"\n  Пар с односторонним опережением "
              f"(разница > 0.15 и t ≥ {T_MEANINGFUL}): {len(real)}")

        # ── ГЛАВНАЯ ПРОВЕРКА
        strong_n = [e for e in edges_n if abs(e["t"]) >= T_MEANINGFUL]
        exp_rand = len(edges) * 0.012
        print("\n\n### Та же проверка, но БЕЗ общего движения рынка\n")
        print("  Из доходности каждого актива вычтена средняя доходность")
        print("  рынка за тот же период. Остаётся только то, что актив")
        print("  сделал СВЕРХ общей волны.\n")
        print(f"    сильных рёбер с рынком:  {len(strong):>4}")
        print(f"    сильных рёбер без рынка: {len(strong_n):>4}")
        print(f"    ожидается случайно:      {exp_rand:>4.0f}")
        survivors = {(e["from"], e["to"]) for e in strong_n}
        kept = [e for e in strong if (e["from"], e["to"]) in survivors]
        print(f"    из прежних сильных выжило: {len(kept)} из {len(strong)}")
        if strong_n and len(strong_n) > exp_rand * 1.5:
            print(f"\n    {'ОПЕРЕЖАЕТ':10}{'→':3}{'КОГО':10}{'n':>6}{'корр':>8}{'t':>7}")
            print("    " + "─" * 44)
            for e in strong_n[:10]:
                print(f"    {e['from']:10}{'→':3}{e['to']:10}{e['n']:>6}"
                      f"{e['corr']:>+8.3f}{e['t']:>+7.2f}")
            print("\n  Связи пережили вычитание рынка — значит это НЕ просто")
            print("  общая волна. Но это по-прежнему одиночные t без поправки")
            print("  на множественность: проверять отдельно, как гипотезу.")
        else:
            print("\n  Почти ничего не выжило. Значит «A опережает B» держалось")
            print("  на общем движении рынка: волна накрывает всех, и кто в неё")
            print("  вошёл на день раньше, выглядит как лидер. Сигнала здесь нет.")

    # ── проверка вне выборки
    print("\n\n### Рёбра подобраны на первой половине, торгуются на второй\n")
    print("  Без этого граф всегда красив: корреляции подогнаны под те же")
    print("  данные, на которых их и меряют.\n")
    oos = []
    for sp in (0.5, 0.65):
        r = out_of_sample(data, lookback, horizon, sp)
        if not r:
            continue
        oos.append(r)
        print(f"  Раздел {int(sp*100)}/{int((1-sp)*100)} · обучение "
              f"{r['train_periods']} периодов · рёбер {r['edges_fitted']} · "
              f"тест {r['test_periods']}")
        for name, key in (("верхняя треть", "top_third"),
                          ("верх минус низ", "long_short")):
            st = r[key]
            dsr = f"{st['dsr']:.2f}" if st.get("dsr") is not None else "—"
            print(f"    {name:16} медиана {st['median_pct']:+6.2f}%  "
                  f"Sharpe {st['sharpe']:+.3f}  IR/год {st['ir_annual']:+.2f}  "
                  f"DSR {dsr}")
        print()
    best = max((r["long_short"]["dsr"] or 0) for r in oos) if oos else 0
    if oos and best < DSR_STRONG:
        print("  Вне выборки граф не даёт преимущества. Сильные рёбра внутри")
        print("  выборки были подгонкой — ровно то, ради чего эта проверка.")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": f"hyperliquid дневные · {len(data)} токенов",
            "formula": "IC(смесь) = IC(среднее) × √(k / (1 + (k-1)ρ))",
            "detector_overlap": dc,
            "lead_lag": {
                "lookback_days": lookback,
                "horizon_days": horizon,
                "edges_tested": len(edges),
                "edges_strong": len([e for e in edges
                                     if abs(e["t"]) >= T_MEANINGFUL]),
                "top_edges": edges[:40],
                "top_asymmetry": asym[:20],
                "market_neutral": {
                    "edges_strong": len([e for e in edges_n
                                         if abs(e["t"]) >= T_MEANINGFUL]),
                    "top_edges": edges_n[:40],
                },
            },
            "out_of_sample": oos,
            "caveat": "t-статистика без поправки на множественность: рёбер "
                      "много, часть сильных появится случайно. Здесь это "
                      "учтено сравнением с ожидаемым числом случайных рёбер",
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=14)
    ap.add_argument("--horizon", type=int, default=14)
    ap.add_argument("--min-pairs", type=int, default=30)
    a = ap.parse_args()
    sys.exit(main(a.lookback, a.horizon, a.min_pairs))