#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detector_backtest.py · v1.0 · 22.08.2026
STRK ENGINE · бэктест правил technical_momentum на длинной истории

ЧТО ЭТО
-------
`technical_momentum.py` считает голоса по восьми правилам и складывает
их в один сигнал BULLISH / BEARISH. Ни одно из этих правил никогда не
проверялось по отдельности: голос «структура UPTREND» весит 2, голос
«RSI < 30» весит 1, и откуда взялись эти веса — неизвестно.

Здесь каждое правило вынимается из детектора и проверяется отдельно,
на всей доступной истории, по всем токенам сразу.

ПРО 90 ДНЕЙ · ЧИТАТЬ ДО ЗАПУСКА
--------------------------------
Бэктест на 90 днях при горизонте 14 дней даёт ШЕСТЬ непересекающихся
наблюдений на токен. Шесть. На шести наблюдениях нельзя отличить
правило с преимуществом от подброшенной монеты — доверительный
интервал шире любого разумного эффекта.

Если брать наблюдение каждый день, получится «90 наблюдений», но
соседние перекрываются на 13 днях из 14, и вся статистика раздувается.
Это ровно та ловушка, на которой MVRV сначала показал DSR 1.00, а
после честного пересчёта — 0.74.

Поэтому скрипт считает на ВСЕЙ истории (2.3 года 4-часовых свечей по
40 токенам), а последние 90 дней показывает ОТДЕЛЬНОЙ КОЛОНКОЙ — не
как доказательство, а чтобы было видно, насколько 90 дней врут.

МЕТОД
-----
  · наблюдения через горизонт, без перекрытия
  · все токены в одном пуле — правило либо работает на классе активов,
    либо это подгонка под один тикер
  · базовая линия: доходность того же токена в тот же период без
    всякого правила. Правило обязано её обыграть, а не просто быть
    положительным на растущем рынке
  · deflated Sharpe с поправкой на число проверенных правил

ЗАПУСК
------
  python3 scripts/detector_backtest.py
  python3 scripts/detector_backtest.py --horizon 14 --interval 4h
  python3 scripts/detector_backtest.py --interval 1d --horizon 28

ВХОД
----
  data/history/hl_4h/<TOKEN>.json    (scripts/collectors/hl_history.py --interval 4h)
  data/history/hl/<TOKEN>.json       дневные, для проверки устойчивости

ВЫХОД
-----
  data/cache/detector_backtest.json
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

OUT_FILE = "data/cache/detector_backtest.json"
RECENT_DAYS = 90
DSR_STRONG = 0.95


# ─────────────────────────────────────────────────────────────
# фичи · дословно из scripts/detectors/technical_momentum.py

def features_at(closes, highs, lows, vols, i, per_day):
    """
    Значения фич на баре i, посчитанные ТОЛЬКО по прошлому.
    per_day — сколько баров в сутках (6 для 4h, 1 для дневных).
    """
    n_3d = 3 * per_day
    n_7d = 7 * per_day
    n_30d = 30 * per_day

    if i < n_30d + n_3d * 2 + 1:
        return None

    c = closes[i]
    f = {}

    f["slope_3d"] = (c / closes[i - n_3d] - 1) * 100 if closes[i - n_3d] > 0 else 0.0
    f["slope_7d"] = (c / closes[i - n_7d] - 1) * 100 if closes[i - n_7d] > 0 else 0.0

    # Ускорение наклона: два ПОСЛЕДОВАТЕЛЬНЫХ непересекающихся окна.
    # Так это исправлено в детекторе 21.08 — раньше было 2*slope_3d -
    # slope_7d, что не производная, а линейная комбинация.
    prev = closes[i - 2 * n_3d]
    f["slope_accel"] = (f["slope_3d"] - (closes[i - n_3d] / prev - 1) * 100
                        if prev > 0 else None)

    v3 = sum(vols[i - n_3d + 1:i + 1])
    v7 = sum(vols[i - n_7d + 1:i + 1])
    v30 = sum(vols[i - n_30d + 1:i + 1])
    f["vol_ratio_3d"] = v3 / max(v30 / 10.0, 1e-9)
    f["vol_accel"] = (v3 / max(n_3d, 1)) / max(v7 / max(n_7d, 1), 1e-9)

    n_14d = 14 * per_day
    hi = max(highs[i - n_14d + 1:i + 1])
    lo = min(lows[i - n_14d + 1:i + 1])
    f["pct_from_high"] = (c / hi - 1) * 100 if hi > 0 else 0.0
    f["pct_from_low"] = (c / lo - 1) * 100 if lo > 0 else 0.0

    # Структура: старшие максимумы / младшие минимумы по половинам окна
    half = n_14d // 2
    h1 = max(highs[i - n_14d + 1:i - half + 1])
    h2 = max(highs[i - half + 1:i + 1])
    l1 = min(lows[i - n_14d + 1:i - half + 1])
    l2 = min(lows[i - half + 1:i + 1])
    hh, hl_, ll, lh = h2 > h1, l2 > l1, l2 < l1, h2 < h1
    f["structure"] = ("UPTREND" if hh and hl_ else
                      "DOWNTREND" if ll and lh else
                      "VOLATILE" if hh and ll else "RANGING")

    # RSI(14 баров) — как в детекторе, период в БАРАХ, не в днях
    n_rsi = 14
    gains = [max(closes[k] - closes[k - 1], 0) for k in range(i - n_rsi + 1, i + 1)]
    losses = [max(closes[k - 1] - closes[k], 0) for k in range(i - n_rsi + 1, i + 1)]
    ag = sum(gains) / n_rsi
    al = sum(losses) / n_rsi
    f["rsi"] = 100 - 100 / (1 + ag / max(al, 1e-9))

    return f


# ─────────────────────────────────────────────────────────────
# правила · один в один голоса из classify_technical()

def rules_fired(f):
    """→ множество имён правил, сработавших на этом баре."""
    out = set()
    if f["structure"] == "UPTREND":
        out.add("structure_UPTREND (+2 бык)")
    if f["structure"] == "DOWNTREND":
        out.add("structure_DOWNTREND (+2 медв)")
    if f["slope_3d"] > 5:
        out.add("slope_3d > +5% (+1 бык)")
    if f["slope_3d"] < -5:
        out.add("slope_3d < -5% (+1 медв)")
    a = f.get("slope_accel")
    if a is not None and a > 3:
        out.add("ускорение > +3 (+2 бык)")
    if a is not None and a < -3:
        out.add("ускорение < -3 (+1 медв)")
    if f["vol_ratio_3d"] > 1.5 and f["slope_3d"] > 3:
        out.add("объём×1.5 + цена вверх (+2 бык)")
    if f["vol_ratio_3d"] > 1.5 and f["slope_3d"] < -3:
        out.add("объём×1.5 + цена вниз (+2 медв)")
    if f["rsi"] < 30:
        out.add("RSI < 30 (+1 бык)")
    if f["rsi"] > 70:
        out.add("RSI > 70 (+1 медв)")
    if f["pct_from_high"] < -20 and f["pct_from_low"] > 10:
        out.add("после капитуляции (+2 бык)")
    if f["pct_from_high"] > -5 and f["vol_accel"] > 1.3:
        out.add("у максимума на объёме (+1 медв)")
    return out


def composite(f):
    """Итоговый сигнал детектора — с теми же весами, что в проде."""
    bull = bear = 0
    if f["structure"] == "UPTREND":
        bull += 2
    elif f["structure"] == "DOWNTREND":
        bear += 2
    if f["slope_3d"] > 5:
        bull += 1
    elif f["slope_3d"] < -5:
        bear += 1
    a = f.get("slope_accel")
    if a is not None:
        if a > 3:
            bull += 2
        elif a < -3:
            bear += 1
    if f["vol_ratio_3d"] > 1.5 and f["slope_3d"] > 3:
        bull += 2
    elif f["vol_ratio_3d"] > 1.5 and f["slope_3d"] < -3:
        bear += 2
    if f["rsi"] < 30:
        bull += 1
    elif f["rsi"] > 70:
        bear += 1
    if f["pct_from_high"] < -20 and f["pct_from_low"] > 10:
        bull += 2
    if f["pct_from_high"] > -5 and f["vol_accel"] > 1.3:
        bear += 1
    if bull > bear:
        return "СИГНАЛ ДЕТЕКТОРА: BULLISH"
    if bear > bull:
        return "СИГНАЛ ДЕТЕКТОРА: BEARISH"
    return None


# правила, которые по смыслу медвежьи: у них доходность должна быть НИЖЕ
# базовой линии. Иначе «правило работает» будет означать разное для
# разных правил, и таблицу нельзя будет читать одним взглядом.
BEARISH = {"structure_DOWNTREND (+2 медв)", "slope_3d < -5% (+1 медв)",
           "ускорение < -3 (+1 медв)", "объём×1.5 + цена вниз (+2 медв)",
           "RSI > 70 (+1 медв)", "у максимума на объёме (+1 медв)",
           "СИГНАЛ ДЕТЕКТОРА: BEARISH"}


# ─────────────────────────────────────────────────────────────

def load_candles(interval):
    d = "data/history/hl" if interval == "1d" else f"data/history/hl_{interval}"
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        cs = j.get("candles") or []
        if len(cs) > 300:
            out[j.get("token") or os.path.basename(p)[:-5]] = cs
    return out, d


def collect(data, horizon_days, per_day, recent_cut):
    """
    → rules: имя → [доходности], baseline: [доходности],
      recent: имя → [доходности за последние 90 дней]
    Наблюдения через горизонт: без перекрытия.
    """
    step = horizon_days * per_day
    rules, recent = {}, {}
    baseline, baseline_recent = [], []

    for token, cs in data.items():
        closes = [c["c"] for c in cs]
        highs = [c["h"] for c in cs]
        lows = [c["l"] for c in cs]
        vols = [c["v"] for c in cs]
        n = len(closes)
        start = 30 * per_day + 6 * per_day + 2

        for i in range(start, n - step, step):
            f = features_at(closes, highs, lows, vols, i, per_day)
            if not f:
                continue
            p0, p1 = closes[i], closes[i + step]
            if not p0 or not p1:
                continue
            ret = (p1 / p0 - 1) * 100
            if abs(ret) > 300:
                continue

            is_recent = cs[i]["date"] >= recent_cut
            baseline.append(ret)
            if is_recent:
                baseline_recent.append(ret)

            fired = rules_fired(f)
            comp = composite(f)
            if comp:
                fired.add(comp)
            for r in fired:
                rules.setdefault(r, []).append(ret)
                if is_recent:
                    recent.setdefault(r, []).append(ret)

    return rules, baseline, recent, baseline_recent


def sharpe(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return 0.0
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd > 0 else 0.0


def evaluate(rules, baseline, recent, min_n):
    names = sorted(r for r in rules if len(rules[r]) >= min_n)
    if not names:
        return [], 0.0
    base_med = float(np.median(np.array(baseline))) if baseline else 0.0
    n_trials = len(rules)

    try:
        import purgedcv as pcv
    except ImportError:
        pcv = None

    srs = [sharpe(np.array(rules[r]) / 100) for r in names]
    var_sr = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.05

    out = []
    for r in names:
        x = np.array(rules[r]) / 100
        med = float(np.median(x)) * 100
        edge = med - base_med
        if r in BEARISH:
            edge = -edge          # медвежье правило «работает», если НИЖЕ базы

        dsr = None
        if pcv is not None:
            try:
                d = pcv.deflated_sharpe_ratio_full(
                    x if r not in BEARISH else -x,
                    n_trials=n_trials, var_sharpe=var_sr)
                dsr = float(getattr(d, "probability", getattr(d, "dsr", 0.0)))
            except Exception:
                dsr = None

        # rec уже в процентах — второй раз на 100 не умножаем
        rec = recent.get(r) or []
        rec_med = float(np.median(np.array(rec))) if len(rec) >= 3 else None

        out.append({
            "rule": r,
            "n": len(x),
            "median_pct": round(med, 2),
            "edge_vs_baseline_pts": round(edge, 2),
            "sharpe": round(sharpe(x), 3),
            "dsr": round(dsr, 3) if dsr is not None else None,
            "recent_n": len(rec),
            "recent_median_pct": round(rec_med, 2) if rec_med is not None else None,
            "bearish": r in BEARISH,
            "verdict": ("преимущество" if (dsr or 0) >= DSR_STRONG
                        else "не доказано"),
        })
    out.sort(key=lambda z: -(z["dsr"] or 0))
    return out, base_med


def main(horizon, interval, min_n):
    per_day = 1 if interval == "1d" else 24 // int(interval.rstrip("h"))
    data, src = load_candles(interval)
    if not data:
        print(f"  Нет свечей в {src}")
        print(f"  Сначала: python3 scripts/collectors/hl_history.py --interval {interval}")
        return 1

    last_dates = [cs[-1]["date"][:10] for cs in data.values()]
    end = max(last_dates)
    y, m, d = (int(x) for x in end.split("-"))
    cut_ord = datetime(y, m, d, tzinfo=timezone.utc).toordinal() - RECENT_DAYS
    cut = datetime.fromordinal(cut_ord).strftime("%Y-%m-%d")

    bars = sum(len(c) for c in data.values())
    print("=== Бэктест правил technical_momentum ===\n")
    print(f"  Токенов: {len(data)} · баров {interval}: {bars} · "
          f"история до {end}")
    print(f"  Горизонт: {horizon} дней · наблюдения через горизонт, без перекрытия\n")

    rules, baseline, recent, base_rec = collect(data, horizon, per_day, cut)
    rows, base_med = evaluate(rules, baseline, recent, min_n)

    if not rows:
        print("  Ни одно правило не набрало достаточно наблюдений")
        return 1

    obs_90 = 90 // horizon
    print(f"  Базовая линия (просто держать): n={len(baseline)} · "
          f"медиана {base_med:+.2f}%")
    print(f"  За последние {RECENT_DAYS} дней: n={len(base_rec)} · медиана "
          f"{(statistics.median(base_rec) if base_rec else 0):+.2f}%\n")

    print(f"    {'ПРАВИЛО':34}{'n':>6}{'медиана':>10}{'+к базе':>9}"
          f"{'DSR':>7}{'90д':>9}  вердикт")
    print("    " + "─" * 88)
    for r in rows:
        rec = (f"{r['recent_median_pct']:+.1f}%" if r["recent_median_pct"] is not None
               else "мало")
        dsr = f"{r['dsr']:.2f}" if r["dsr"] is not None else "  —"
        print(f"    {r['rule']:34}{r['n']:>6}{r['median_pct']:>+10.2f}%"
              f"{r['edge_vs_baseline_pts']:>+9.2f}{dsr:>7}{rec:>9}  {r['verdict']}")

    strong = [r for r in rows if (r["dsr"] or 0) >= DSR_STRONG]
    print(f"\n  Правил проверено: {len(rows)} · с доказанным преимуществом: {len(strong)}")

    # Насколько 90 дней расходятся с полной историей
    flips = [r for r in rows if r["recent_median_pct"] is not None
             and (r["median_pct"] - base_med > 0) != (r["recent_median_pct"] > 0)]
    print(f"\n  За 90 дней на каждое правило приходится ≈{obs_90} "
          f"непересекающихся наблюдений на токен.")
    print(f"  Правил, у которых знак за 90 дней ПРОТИВОПОЛОЖЕН знаку на всей "
          f"истории: {len(flips)} из {len(rows)}")
    if flips:
        print("  Именно поэтому бэктест на 90 днях показал бы другое — "
              "и это была бы не новая информация, а шум.")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": f"hyperliquid {interval} · {len(data)} токенов · {bars} баров",
            "detector": "scripts/detectors/technical_momentum.py",
            "horizon_days": horizon,
            "method": "наблюдения через горизонт без перекрытия; пул по всем "
                      "токенам; медвежьи правила оцениваются по отрицательному "
                      "краю; DSR с поправкой на число проверенных правил",
            "caveat": f"последние {RECENT_DAYS} дней показаны отдельной колонкой "
                      f"для сравнения, это ≈{obs_90} наблюдений на токен и "
                      f"доказательством не является",
            "baseline_median_pct": round(base_med, 3),
            "baseline_n": len(baseline),
            "rules": rows,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=14)
    ap.add_argument("--interval", type=str, default="4h")
    ap.add_argument("--min-n", type=int, default=30)
    a = ap.parse_args()
    sys.exit(main(a.horizon, a.interval, a.min_n))