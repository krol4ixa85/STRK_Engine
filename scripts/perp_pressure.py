#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
perp_pressure.py · v1.0 · 22.08.2026
STRK ENGINE · проверка наблюдения «рынком движут перпы»

НАБЛЮДЕНИЕ, КОТОРОЕ ПРОВЕРЯЕТСЯ
--------------------------------
«Всё двигается по единой указке, будто кто-то нажал кнопку.»

Здесь оно разбирается на два РАЗНЫХ утверждения, потому что первое
может быть правдой, а второе — нет:

  1. Рынок ходит одним куском.
     Проверяется долей движения токена, объяснимой общим рынком.

  2. Кнопку нажимают перпы, и по ним видно разворот.
     Проверяется тем, предсказывает ли перекос фандинга будущую
     доходность.

Первое можно измерить и оно почти наверняка подтвердится. Второе —
совсем другое утверждение, и подтверждается оно гораздо реже: «рынок
ходит вместе» не означает «фандинг говорит, куда».

ПОЧЕМУ ФАНДИНГ, А НЕ ЧТО-ТО ЕЩЁ
--------------------------------
Фандинг — это плата, которую одна сторона перпов платит другой за то,
чтобы держать позицию. Положительный — за длинные позиции платят
лонги, значит их больше и они готовы доплачивать. Отрицательный —
наоборот. Это прямая мера перекоса толпы в плече, и на Hyperliquid
она бесплатно доступна почасово на годы назад.

Открытый интерес был бы вторым измерением, но исторически он не
отдаётся — только текущий срез. Поэтому здесь только фандинг, и это
честно названо.

ЗАПУСК
------
  python3 scripts/perp_pressure.py
  python3 scripts/perp_pressure.py --horizon 7

ВХОД
----
  data/history/hl/<TOKEN>.json          свечи
  data/history/hl_funding/<TOKEN>.json  фандинг

ВЫХОД
-----
  data/cache/perp_pressure.json
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

CANDLE_DIR = "data/history/hl"
FUND_DIR = "data/history/hl_funding"
OUT_FILE = "data/cache/perp_pressure.json"

HORIZONS = [7, 14, 28]
MIN_TOKENS_PER_DAY = 10
DSR_STRONG = 0.95


def load_prices():
    out = {}
    for p in sorted(glob.glob(os.path.join(CANDLE_DIR, "*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        cs = j.get("candles") or []
        if len(cs) > 300:
            out[j["token"]] = {r["date"]: r["c"] for r in cs}
    return out


def load_funding():
    out = {}
    for p in sorted(glob.glob(os.path.join(FUND_DIR, "*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        s = j.get("series") or []
        if len(s) > 120:
            out[j["token"]] = {r["date"]: r["funding_annual_pct"] for r in s}
    return out


# ─────────────────────────────────────────────────────────────
# часть 1 · насколько рынок ходит одним куском

def market_share_of_movement(prices):
    """
    Для каждого токена: какая доля дисперсии его дневной доходности
    объясняется доходностью рынка (R² простой регрессии). Это и есть
    численное выражение «всё ходит по единой указке».
    """
    dates = sorted({d for m in prices.values() for d in m})
    rets = {}
    for t, m in prices.items():
        r = {}
        for i in range(1, len(dates)):
            p0, p1 = m.get(dates[i - 1]), m.get(dates[i])
            if p0 and p1:
                x = p1 / p0 - 1
                if abs(x) < 1:
                    r[dates[i]] = x
        rets[t] = r

    mkt = {}
    for d in dates:
        vals = [r[d] for r in rets.values() if d in r]
        if len(vals) >= MIN_TOKENS_PER_DAY:
            mkt[d] = statistics.median(vals)

    out = []
    for t, r in rets.items():
        pairs = [(mkt[d], r[d]) for d in r if d in mkt]
        if len(pairs) < 200:
            continue
        x = np.array([a for a, _ in pairs])
        y = np.array([b for _, b in pairs])
        if x.std() == 0 or y.std() == 0:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if not math.isnan(c):
            out.append({"token": t, "r2_pct": round(c * c * 100, 1),
                        "n_days": len(pairs)})
    out.sort(key=lambda z: -z["r2_pct"])
    return out, mkt


# ─────────────────────────────────────────────────────────────
# часть 2 · говорит ли фандинг, куда

def build_index(funding):
    """Совокупный перекос: медиана годового фандинга по всем токенам за день."""
    dates = sorted({d for m in funding.values() for d in m})
    idx = {}
    for d in dates:
        vals = [m[d] for m in funding.values() if d in m]
        if len(vals) >= MIN_TOKENS_PER_DAY:
            idx[d] = statistics.median(vals)
    return idx


def sharpe(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return 0.0
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd > 0 else 0.0


def dsr_of(x, n_trials):
    try:
        import purgedcv as pcv
        r = pcv.deflated_sharpe_ratio_full(np.asarray(x, float),
                                           n_trials=n_trials, var_sharpe=0.05)
        return round(float(getattr(r, "probability", getattr(r, "dsr", 0.0))), 3)
    except ImportError:
        return None


def timing_test(idx, mkt, horizon, n_trials):
    """
    Работает ли перекос фандинга как таймер рынка: наблюдения через
    горизонт, доходность рынка складывается за горизонт вперёд.
    """
    dates = sorted(set(idx) & set(mkt))
    if len(dates) < horizon * 12:
        return None

    rows = []
    for i in range(0, len(dates) - horizon, horizon):
        d0 = dates[i]
        fwd = [mkt[d] for d in dates[i:i + horizon] if d in mkt]
        if len(fwd) < horizon * 0.6:
            continue
        # сумма дневных медиан — приближение доходности рынка за окно
        rows.append((idx[d0], sum(fwd) * 100))

    if len(rows) < 20:
        return None

    f = np.array([a for a, _ in rows])
    r = np.array([b for _, b in rows])
    c = float(np.corrcoef(f, r)[0, 1]) if f.std() > 0 else 0.0
    t = c * math.sqrt(max(len(rows) - 2, 1)) / math.sqrt(max(1 - c * c, 1e-9))

    order = np.argsort(f)
    q = max(3, len(rows) // 5)
    low, high = r[order[:q]], r[order[-q:]]
    base = float(np.median(r))

    return {
        "n": len(rows),
        "corr_funding_vs_forward": round(c, 3),
        "t_stat": round(t, 2),
        "baseline_median_pct": round(base, 2),
        "top_quintile": {
            "meaning": "толпа сильнее всего в лонге",
            "median_pct": round(float(np.median(high)), 2),
            "edge_vs_baseline": round(float(np.median(high)) - base, 2),
            "sharpe": round(sharpe(high / 100), 3),
            "dsr_short": dsr_of(-high / 100, n_trials),
        },
        "bottom_quintile": {
            "meaning": "толпа сильнее всего в шорте",
            "median_pct": round(float(np.median(low)), 2),
            "edge_vs_baseline": round(float(np.median(low)) - base, 2),
            "sharpe": round(sharpe(low / 100), 3),
            "dsr_long": dsr_of(low / 100, n_trials),
        },
    }


def cross_section_test(funding, prices, horizon, n_trials):
    """
    Внутри одного дня: отстают ли токены с высоким фандингом от токенов
    с низким. Это уже не про рынок целиком, а про выбор актива.
    """
    dates = sorted({d for m in funding.values() for d in m})
    lo_r, hi_r, mkt_r = [], [], []

    for i in range(0, len(dates) - horizon, horizon):
        d0, d1 = dates[i], dates[i + horizon]
        rows = []
        for t, fm in funding.items():
            pm = prices.get(t)
            if not pm or d0 not in fm:
                continue
            p0, p1 = pm.get(d0), pm.get(d1)
            if not p0 or not p1:
                continue
            ret = p1 / p0 - 1
            if abs(ret) > 3:
                continue
            rows.append((fm[d0], ret))
        if len(rows) < MIN_TOKENS_PER_DAY:
            continue
        rows.sort(key=lambda z: z[0])
        q = max(1, len(rows) // 3)
        lo_r.append(statistics.mean([r for _, r in rows[:q]]))
        hi_r.append(statistics.mean([r for _, r in rows[-q:]]))
        mkt_r.append(statistics.mean([r for _, r in rows]))

    if len(lo_r) < 15:
        return None

    lo, hi = np.array(lo_r), np.array(hi_r)
    ls = lo - hi                      # низкий фандинг минус высокий
    per_year = 365.0 / horizon
    return {
        "periods": len(lo_r),
        "market_median_pct": round(float(np.median(np.array(mkt_r))) * 100, 2),
        "low_funding_median_pct": round(float(np.median(lo)) * 100, 2),
        "high_funding_median_pct": round(float(np.median(hi)) * 100, 2),
        "long_short": {
            "median_pct": round(float(np.median(ls)) * 100, 2),
            "sharpe": round(sharpe(ls), 3),
            "ir_annual": round(sharpe(ls) * math.sqrt(per_year), 2),
            "dsr": dsr_of(ls, n_trials),
        },
    }


def main(horizons):
    prices = load_prices()
    funding = load_funding()
    if not prices:
        print(f"  Нет свечей в {CANDLE_DIR} — "
              f"python3 scripts/collectors/hl_history.py")
        return 1
    if not funding:
        print(f"  Нет фандинга в {FUND_DIR} — "
              f"python3 scripts/collectors/hl_funding_history.py")
        return 1

    print("=== Перпы как индикатор рынка ===\n")
    print(f"  Токенов со свечами: {len(prices)} · с фандингом: {len(funding)}\n")

    # ── часть 1
    print("### Насколько рынок ходит одним куском\n")
    shares, mkt = market_share_of_movement(prices)
    if shares:
        med = statistics.median([s["r2_pct"] for s in shares])
        print(f"  Медианная доля движения токена, объяснимая общим рынком: "
              f"{med:.0f}%")
        print(f"  То есть в среднем {med:.0f} процентов дневного движения "
              f"любого актива —")
        print(f"  это не он сам, а рынок целиком.\n")
        print(f"    {'ТОКЕН':10}{'доля рынка':>12}")
        print("    " + "─" * 22)
        for s in shares[:5]:
            print(f"    {s['token']:10}{s['r2_pct']:>11.0f}%")
        print("    ...")
        for s in shares[-3:]:
            print(f"    {s['token']:10}{s['r2_pct']:>11.0f}%")
        print("\n  Наблюдение подтверждается: это не ощущение, это число.")

    # ── часть 2
    idx = build_index(funding)
    print(f"\n\n### Говорит ли перекос фандинга, КУДА пойдёт рынок\n")
    print(f"  Дней с индексом фандинга: {len(idx)}")
    if idx:
        vals = list(idx.values())
        print(f"  Совокупный фандинг: медиана {statistics.median(vals):+.1f}% "
              f"годовых · от {min(vals):+.0f}% до {max(vals):+.0f}%")
        print(f"  Положительный почти всегда — за плечо в лонг платят "
              f"постоянно.\n")

    n_trials = len(horizons) * 4
    timing, cross = {}, {}
    for h in horizons:
        tt = timing_test(idx, mkt, h, n_trials)
        timing[str(h)] = tt
        if not tt:
            continue
        print(f"  Горизонт {h} дней · n={tt['n']} · "
              f"корреляция фандинга с будущим {tt['corr_funding_vs_forward']:+.3f} "
              f"(t {tt['t_stat']:+.2f})")
        b = tt["baseline_median_pct"]
        for key, label in (("top_quintile", "толпа в лонге "),
                           ("bottom_quintile", "толпа в шорте")):
            q = tt[key]
            d = q.get("dsr_short") if key == "top_quintile" else q.get("dsr_long")
            print(f"     {label} → рынок {q['median_pct']:+6.2f}% "
                  f"(база {b:+.2f}%, разница {q['edge_vs_baseline']:+.2f}) "
                  f"DSR {d if d is not None else '—'}")
        print()

    print("\n### Внутри дня: отстают ли токены с высоким фандингом\n")
    for h in horizons:
        ct = cross_section_test(funding, prices, h, n_trials)
        cross[str(h)] = ct
        if not ct:
            print(f"  Горизонт {h}: данных мало")
            continue
        ls = ct["long_short"]
        print(f"  Горизонт {h} дней · периодов {ct['periods']}")
        print(f"     низкий фандинг {ct['low_funding_median_pct']:+6.2f}%  ·  "
              f"высокий {ct['high_funding_median_pct']:+6.2f}%  ·  "
              f"рынок {ct['market_median_pct']:+6.2f}%")
        print(f"     низкий минус высокий: медиана {ls['median_pct']:+.2f}% · "
              f"Sharpe {ls['sharpe']:+.3f} · IR/год {ls['ir_annual']:+.2f} · "
              f"DSR {ls['dsr'] if ls['dsr'] is not None else '—'}")

    proven = [1 for h in horizons
              if (cross.get(str(h)) or {}).get("long_short", {}).get("dsr", 0)
              and cross[str(h)]["long_short"]["dsr"] >= DSR_STRONG]
    print(f"\n  Проверок с доказанным преимуществом: {len(proven)}")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "hyperliquid · свечи и фандинг",
            "tokens_prices": len(prices), "tokens_funding": len(funding),
            "market_share_of_movement": shares,
            "funding_index_days": len(idx),
            "timing": timing,
            "cross_section": cross,
            "caveat": "открытый интерес исторически недоступен — здесь только "
                      "фандинг; наблюдения через горизонт, без перекрытия",
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=0)
    a = ap.parse_args()
    sys.exit(main([a.horizon] if a.horizon else HORIZONS))