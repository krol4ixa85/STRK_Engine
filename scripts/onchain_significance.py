#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onchain_significance.py · v1.0 · 22.08.2026
STRK ENGINE · проверка оценочных правил на длинной истории

ЗАЧЕМ
-----
Правила на потоках Dune проверить нельзя: 26 недель на токен, и
deflated Sharpe на такой выборке не пройдёт никогда. MVRV и NUPL —
другое дело: по ним есть от 5 до 9 лет ежедневных данных.

Здесь проверяется простое семейство гипотез: связано ли положение
MVRV в собственной истории актива с доходностью следующих N дней.

ГЛАВНАЯ ЛОВУШКА, РАДИ КОТОРОЙ ЭТОТ ФАЙЛ И НАПИСАН
--------------------------------------------------
Если брать наблюдение каждый день, а доходность считать на 28 дней
вперёд, соседние наблюдения перекрываются на 27/28. Формально
получается 13 827 «наблюдений», фактически независимых — около 500.

Все статистики (PSR, DSR) считают наблюдения независимыми. Подай им
перекрывающиеся — и они выдадут уверенность, которой нет.

Проверено на этих же данных:

    правило mvrv_p75
      перекрывающиеся   n=1302  DSR 1.00   «доказано»
      непересекающиеся  n=51    DSR 0.74   «возможно»

Одни и те же данные, одна формула, противоположные выводы. Разница
только в том, считать ли перекрытие. По умолчанию скрипт считает
ЧЕСТНО — берёт наблюдения через горизонт. Режим с перекрытием
оставлен только чтобы разницу было видно (--show-overlap).

ЗАПУСК
------
  python3 scripts/onchain_significance.py
  python3 scripts/onchain_significance.py --horizon 56
  python3 scripts/onchain_significance.py --show-overlap

ВХОД
----
  data/history/coinmetrics/<ASSET>.json

ВЫХОД
-----
  data/cache/onchain_significance.json

ЗАВИСИМОСТЬ
-----------
  pip install purgedcv   (MIT)
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:
    raise SystemExit("ERROR: pip install numpy")
try:
    import purgedcv as pcv
except ImportError:
    raise SystemExit("ERROR: pip install purgedcv")

HIST_DIR = "data/history/coinmetrics"
OUT_FILE = "data/cache/onchain_significance.json"

# Сколько дней истории должно накопиться, прежде чем считать перцентиль.
# Меньше — и «перцентиль собственной истории» считается по кусочку,
# которого не хватает ни на что.
WARMUP_DAYS = 400

# Отсекаем заведомо битые цены
MAX_ABS_RETURN_PCT = 300

# Порог, выше которого считаем правило отличимым от случайности
DSR_STRONG = 0.95
DSR_MAYBE = 0.75


def sharpe(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return 0.0
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd > 0 else 0.0


def collect(horizon, step):
    """
    step=1        наблюдение каждый день — перекрытие horizon-1 дней
    step=horizon  наблюдения не пересекаются — так честно
    """
    rules, baseline = {}, []
    assets = 0
    for path in sorted(glob.glob(os.path.join(HIST_DIR, "*.json"))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        s = [r for r in (d.get("series") or [])
             if r.get("CapMVRVCur") and r.get("PriceUSD")]
        if len(s) < WARMUP_DAYS + horizon + 50:
            continue
        assets += 1
        mv = [r["CapMVRVCur"] for r in s]

        for i in range(WARMUP_DAYS, len(s) - horizon, step):
            p0, p1 = s[i]["PriceUSD"], s[i + horizon]["PriceUSD"]
            if not p0 or not p1:
                continue
            ret = (p1 / p0 - 1) * 100
            if abs(ret) > MAX_ABS_RETURN_PCT:
                continue
            baseline.append(ret)

            # Перцентиль считаем ТОЛЬКО по прошлому — иначе подглядывание
            hist = mv[:i + 1]
            cur = mv[i]
            pr = sum(1 for v in hist if v < cur) / len(hist) * 100
            nupl = 1.0 - 1.0 / cur if cur > 0 else None

            if pr >= 90:
                rules.setdefault("mvrv_p90 · исторически дорого", []).append(ret)
            if pr >= 75:
                rules.setdefault("mvrv_p75 · выше своей нормы", []).append(ret)
            if pr <= 25:
                rules.setdefault("mvrv_p25 · ниже своей нормы", []).append(ret)
            if pr <= 10:
                rules.setdefault("mvrv_p10 · исторически дёшево", []).append(ret)
            if cur < 1:
                rules.setdefault("mvrv < 1 · держатели в убытке", []).append(ret)
            if nupl is not None and nupl > 0.5:
                rules.setdefault("nupl > 0.5 · много бумажной прибыли", []).append(ret)

    return rules, baseline, assets


def evaluate(rules, baseline, min_n):
    names = [r for r in sorted(rules) if len(rules[r]) >= min_n]
    if not names:
        return [], None
    srs = [sharpe(np.array(rules[r]) / 100) for r in names]
    var_sr = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    n_trials = len(rules)

    base = np.array(baseline) / 100
    base_med = float(np.median(base) * 100)

    out = []
    for r in names:
        x = np.array(rules[r]) / 100
        sr = sharpe(x)
        d = pcv.deflated_sharpe_ratio_full(x, n_trials=n_trials, var_sharpe=var_sr)
        dsr = float(getattr(d, "probability", getattr(d, "dsr", 0.0)))
        med = float(np.median(x) * 100)
        out.append({
            "rule": r,
            "n": int(len(x)),
            "median_pct": round(med, 2),
            "edge_vs_baseline_pts": round(med - base_med, 2),
            "sharpe": round(sr, 3),
            "psr": round(float(pcv.probabilistic_sharpe_ratio(x, 0.0)), 3),
            "dsr": round(dsr, 3),
            "verdict": ("преимущество" if dsr > DSR_STRONG
                        else "возможно, нужно больше" if dsr > DSR_MAYBE
                        else "не отличимо от случайности"),
        })
    out.sort(key=lambda z: -z["dsr"])
    return out, base_med


def print_block(title, rows, base_med, n_base, assets, horizon):
    print(f"\n### {title}")
    print(f"    активов {assets} · базовая линия n={n_base} · медиана {base_med:+.2f}%")
    if not rows:
        print("    правил с достаточным n нет")
        return
    print(f"    {'ПРАВИЛО':32}{'n':>6}{'медиана':>10}{'+к базе':>9}{'Sharpe':>8}{'DSR':>7}  вердикт")
    print("    " + "─" * 88)
    for r in rows:
        print(f"    {r['rule']:32}{r['n']:>6}{r['median_pct']:>+10.2f}%"
              f"{r['edge_vs_baseline_pts']:>+9.2f}{r['sharpe']:>8.3f}{r['dsr']:>7.2f}  {r['verdict']}")


def main(horizon, min_n, show_overlap):
    print("=== Проверка оценочных правил на длинной истории ===")
    print(f"    горизонт: {horizon} дней\n")

    honest_rules, honest_base, assets = collect(horizon, horizon)
    honest_rows, honest_med = evaluate(honest_rules, honest_base, min_n)

    if not honest_base:
        print("  Нет данных. Сначала: python3 scripts/collectors/coinmetrics_history.py")
        return 1

    over_rows = over_med = None
    if show_overlap:
        over_rules, over_base, _ = collect(horizon, 1)
        over_rows, over_med = evaluate(over_rules, over_base, min_n)
        print_block("ПЕРЕКРЫВАЮЩИЕСЯ наблюдения — так статистика ВРЁТ",
                    over_rows, over_med, len(over_base), assets, horizon)

    print_block("НЕПЕРЕСЕКАЮЩИЕСЯ наблюдения — честно",
                honest_rows, honest_med, len(honest_base), assets, horizon)

    strong = [r for r in honest_rows if r["dsr"] > DSR_STRONG]
    print(f"\n  Правил с доказанным преимуществом: {len(strong)}")
    if not strong:
        print("  Ни одно правило не отличимо от случайности на честной выборке.")

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "horizon_days": horizon,
            "assets": assets,
            "method": "перцентиль MVRV считается только по прошлому; "
                      "наблюдения не пересекаются (шаг = горизонт)",
            "caveat": "активы коррелированы между собой и период включает "
                      "два бычьих цикла — независимость наблюдений неполная "
                      "даже без перекрытия по времени",
            "baseline_median_pct": honest_med,
            "baseline_n": len(honest_base),
            "rules": honest_rows,
            "overlapping_for_comparison": over_rows,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=28, help="дней вперёд")
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--show-overlap", action="store_true",
                    help="показать, как перекрытие раздувает уверенность")
    a = ap.parse_args()
    sys.exit(main(a.horizon, a.min_n, a.show_overlap))