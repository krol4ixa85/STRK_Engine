#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decision_backtest.py · v1.0 · 21.08.2026
STRK ENGINE · исторический прогон правил

ЗАЧЕМ
-----
Форвард-тест честен, но медленный: первые закрытые прогнозы будут
27 августа, статистически значимая выборка — через два месяца. До тех
пор правила работают вслепую.

Здесь то же самое, но задним числом. В каждом token_scan лежит 26 недель
истории: поток и цена закрытия. Этого достаточно чтобы восстановить
состояние движка на любой неделе в прошлом и посмотреть что случилось
с ценой дальше.

ЧТО ПРОВЕРЯЕМ
-------------
Только правила, которые можно восстановить из weekly_history:

  flow_accel_up      приток ускоряется (ACCEL_UP)
  flow_accel_down    отток ускоряется (ACCEL_DOWN)
  flow_stalling_up   приток есть, но темп падает
  flow_flipping_up   развернулось из оттока в приток
  flow_flipping_down приток закончился, пошёл отток
  flow_steady_up     стабильный приток
  streak_4plus       серия 4+ недель притока подряд
  divergence_bear    цена вверх, поток вниз
  divergence_bull    цена вниз, поток вверх

ЧЕГО НЕ ПРОВЕРЯЕМ И ПОЧЕМУ
--------------------------
  phase_verdict от Dune  — в скане только текущее значение, истории нет
  unified_verdict        — истории нет
  CVD, Volume Profile    — начали собирать 21.08, истории нет
  regime рынка           — истории нет
  news, funding          — истории нет

Эти правила проверит только форвард-тест. Здесь честно молчим о них.

МЕТОДИКА
--------
Для каждой недели t от 8 до N-H:
  1. Считаем состояние потока на момент t (только данные ДО t)
  2. Определяем какие правила сработали бы
  3. Смотрим цену через H недель (H = 1, 2, 4)
  4. Записываем исход

Данные до момента t и только до него — иначе получим заглядывание
в будущее, из-за которого любая система выглядит гениальной.

МЕТРИКИ
-------
  n              сколько раз правило сработало
  hit_rate       доля случаев с движением в ожидаемую сторону >3%
  avg_return     средняя доходность за горизонт
  median_return  медиана (устойчивее к выбросам)
  best / worst   лучший и худший исход
  sharpe_like    avg / std — грубая мера отношения сигнала к шуму

ВАЖНО ПРО ТОЛКОВАНИЕ
--------------------
Это не доказательство. 26 недель на токен — короткая выборка, а весь
период был одной рыночной фазой. Правило может показать 70% и провалиться
в другом режиме. Смысл в другом: увидеть какие правила заведомо не
работают, чтобы не тащить их дальше.

ЗАПУСК
------
  python3 scripts/decision_backtest.py
  python3 scripts/decision_backtest.py --horizon 4
  python3 scripts/decision_backtest.py --token LINK,STRK

ВЫХОД
-----
  data/cache/rule_backtest.json
"""

import os
import sys
import json
import glob
import math
import argparse
import statistics
from datetime import datetime, timezone
from collections import defaultdict

CACHE = "data/cache"
SCAN_DIR = os.path.join(CACHE, "token_scan")
OUT_FILE = os.path.join(CACHE, "rule_backtest.json")

# Горизонты проверки в неделях
HORIZONS = [1, 2, 4]

# Движение, которое считаем подтверждением
MOVE_THRESHOLD_PCT = 3.0

# Минимум недель истории до точки, чтобы посчитать 4w/4w дельту
MIN_HISTORY_WEEKS = 8

# Порог значимого ускорения, как в phase_analyzer
FLOW_ACCEL_NOISE_USD = 500_000

# Ниже этого числа срабатываний метрики не печатаем — шум
MIN_N_TO_REPORT = 10

# Sanity-фильтр. Недельная доходность больше этого — битые данные
# (тот же баг единиц измерения, что даёт ARB +1 717 040%). Такие точки
# выбрасываем, иначе одно наблюдение перекашивает всю статистику:
# без фильтра средняя доходность выходила +25 892 289%.
MAX_SANE_WEEKLY_RETURN_PCT = 200.0


def load_scans(only=None):
    out = {}
    for p in sorted(glob.glob(os.path.join(SCAN_DIR, "*.json"))):
        name = os.path.basename(p)[:-5].upper()
        if name in ("INDEX", "_META"):
            continue
        if only and name not in only:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        wh = d.get("weekly_history") or []
        if len(wh) >= MIN_HISTORY_WEEKS + max(HORIZONS) + 1:
            out[name] = wh
    return out


def flow_state_at(weekly, t):
    """
    Восстанавливает состояние потока на неделе t, используя ТОЛЬКО
    данные до t включительно. Заглядывание в будущее исключено.
    """
    hist = weekly[:t + 1]
    if len(hist) < MIN_HISTORY_WEEKS:
        return None

    flows = [float(w.get("net_flow_m_usd") or 0) for w in hist]

    last_4 = sum(flows[-4:])
    prev_4 = sum(flows[-8:-4])
    accel_4w = (last_4 - prev_4) * 1e6
    last_4_usd = last_4 * 1e6

    # Серия положительных недель подряд
    streak = 0
    for f in reversed(flows):
        if f > 0:
            streak += 1
        else:
            break

    # Доля положительных недель за доступную историю
    pos_share = sum(1 for f in flows if f > 0) / len(flows) * 100

    return {
        "last_4w_usd": last_4_usd,
        "prev_4w_usd": prev_4 * 1e6,
        "accel_4w_usd": accel_4w,
        "streak": streak,
        "pos_share_pct": pos_share,
    }


def regime_at(state):
    """Та же классификация что в phase_analyzer, но на историческом срезе."""
    if not state:
        return None
    last = state["last_4w_usd"]
    accel = state["accel_4w_usd"]
    prev = state["prev_4w_usd"]
    small = FLOW_ACCEL_NOISE_USD
    sig = abs(accel) >= small

    if abs(last) < small and abs(accel) < small:
        return "STABLE_ZERO"
    if last > 0:
        if sig and accel > 0:
            return "ACCEL_UP"
        if sig and accel < 0:
            return "STALLING_UP"
        if prev < -small:
            return "FLIPPING_UP"
        return "STEADY_UP"
    if last < 0:
        if sig and accel < 0:
            return "ACCEL_DOWN"
        if sig and accel > 0:
            return "STALLING_DOWN"
        if prev > small:
            return "FLIPPING_DOWN"
        return "STEADY_DOWN"
    return "STABLE_ZERO"


def price_divergence_at(weekly, t, state):
    """Цена против потока на срезе t."""
    if t < 4:
        return None
    try:
        p_now = float(weekly[t].get("close_price") or 0)
        p_4w = float(weekly[t - 4].get("close_price") or 0)
    except (TypeError, ValueError):
        return None
    if not p_now or not p_4w:
        return None

    price_pct = (p_now / p_4w - 1) * 100
    flow = state["last_4w_usd"]

    if price_pct > 5 and flow < -FLOW_ACCEL_NOISE_USD:
        return "BEARISH_DIV"
    if price_pct < -5 and flow > FLOW_ACCEL_NOISE_USD:
        return "BULLISH_DIV"
    return "ALIGNED"


def rules_fired_at(weekly, t):
    """Список правил, которые сработали бы на неделе t."""
    state = flow_state_at(weekly, t)
    if not state:
        return [], None

    fired = []
    reg = regime_at(state)
    if reg:
        fired.append(f"flow:{reg}")

    if state["streak"] >= 4:
        fired.append("streak_4plus")
    if state["pos_share_pct"] >= 65:
        fired.append("pos_share_65plus")

    div = price_divergence_at(weekly, t, state)
    if div and div != "ALIGNED":
        fired.append(f"div:{div}")

    return fired, {"regime": reg, "divergence": div, **state}


def forward_return(weekly, t, horizon):
    """Изменение цены через horizon недель после t."""
    if t + horizon >= len(weekly):
        return None
    try:
        p0 = float(weekly[t].get("close_price") or 0)
        p1 = float(weekly[t + horizon].get("close_price") or 0)
    except (TypeError, ValueError):
        return None
    if not p0 or not p1:
        return None
    ret = (p1 / p0 - 1) * 100
    # Отбрасываем невозможные движения — это ошибка данных, не рынок
    if abs(ret) > MAX_SANE_WEEKLY_RETURN_PCT * horizon:
        return None
    return ret


# Ожидаемое направление для каждого правила. Нужно чтобы считать hit_rate:
# бычье правило "попало", если цена выросла; медвежье — если упала.
EXPECTED_DIRECTION = {
    "flow:ACCEL_UP": "UP",
    "flow:STEADY_UP": "UP",
    "flow:FLIPPING_UP": "UP",
    "flow:STALLING_UP": "FLAT_OR_DOWN",
    "flow:STABLE_ZERO": None,
    "flow:STALLING_DOWN": "FLAT_OR_UP",
    "flow:FLIPPING_DOWN": "DOWN",
    "flow:STEADY_DOWN": "DOWN",
    "flow:ACCEL_DOWN": "DOWN",
    "streak_4plus": "UP",
    "pos_share_65plus": "UP",
    "div:BEARISH_DIV": "DOWN",
    "div:BULLISH_DIV": "UP",
}


def is_hit(direction, ret):
    if direction is None:
        return None
    if direction == "UP":
        return ret >= MOVE_THRESHOLD_PCT
    if direction == "DOWN":
        return ret <= -MOVE_THRESHOLD_PCT
    if direction == "FLAT_OR_DOWN":
        return ret < MOVE_THRESHOLD_PCT
    if direction == "FLAT_OR_UP":
        return ret > -MOVE_THRESHOLD_PCT
    return None


def summarize(returns, direction):
    """Метрики по списку доходностей."""
    if not returns:
        return None
    n = len(returns)
    avg = statistics.mean(returns)
    med = statistics.median(returns)
    sd = statistics.stdev(returns) if n > 1 else 0.0

    hits = [is_hit(direction, r) for r in returns]
    hits = [h for h in hits if h is not None]
    hit_rate = (sum(hits) / len(hits) * 100) if hits else None

    return {
        "n": n,
        "hit_rate_pct": round(hit_rate, 1) if hit_rate is not None else None,
        "avg_return_pct": round(avg, 2),
        "median_return_pct": round(med, 2),
        "std_pct": round(sd, 2),
        "best_pct": round(max(returns), 2),
        "worst_pct": round(min(returns), 2),
        "sharpe_like": round(avg / sd, 3) if sd > 0 else None,
        "enough_data": n >= MIN_N_TO_REPORT,
    }


def run(only=None, horizons=None):
    horizons = horizons or HORIZONS
    print(f"=== Rule Backtest v1.0 ===\n")

    scans = load_scans(only)
    if not scans:
        print("✗ Нет сканов с достаточной историей")
        return

    print(f"  Токенов с историей: {len(scans)}")
    print(f"  Горизонты: {horizons} недель")
    print(f"  Порог движения: ±{MOVE_THRESHOLD_PCT}%\n")

    # rule -> horizon -> [returns]
    collected = defaultdict(lambda: defaultdict(list))
    # базовая линия: доходность вообще, без правил
    baseline = defaultdict(list)
    total_points = 0

    for token, weekly in scans.items():
        n_weeks = len(weekly)
        for t in range(MIN_HISTORY_WEEKS, n_weeks - max(horizons)):
            fired, state = rules_fired_at(weekly, t)
            if not fired:
                continue
            total_points += 1

            for h in horizons:
                ret = forward_return(weekly, t, h)
                if ret is None:
                    continue
                baseline[h].append(ret)
                for rule in fired:
                    collected[rule][h].append(ret)

    print(f"  Точек наблюдения: {total_points}\n")

    # Базовая линия — с чем сравнивать
    base_summary = {}
    for h in horizons:
        rets = baseline[h]
        if rets:
            base_summary[f"{h}w"] = {
                "n": len(rets),
                "avg_return_pct": round(statistics.mean(rets), 2),
                "median_return_pct": round(statistics.median(rets), 2),
                "share_up_3pct": round(sum(1 for r in rets if r >= MOVE_THRESHOLD_PCT) / len(rets) * 100, 1),
                "share_down_3pct": round(sum(1 for r in rets if r <= -MOVE_THRESHOLD_PCT) / len(rets) * 100, 1),
            }

    print("  БАЗОВАЯ ЛИНИЯ (все точки, без фильтра правил):")
    for h in horizons:
        b = base_summary.get(f"{h}w")
        if b:
            print(f"    {h}w: средняя {b['avg_return_pct']:+.2f}% · "
                  f"выросло >3%: {b['share_up_3pct']:.0f}% · "
                  f"упало >3%: {b['share_down_3pct']:.0f}%")
    print()

    # Метрики по правилам
    rules_out = {}
    for rule, by_h in sorted(collected.items()):
        direction = EXPECTED_DIRECTION.get(rule)
        entry = {"expected_direction": direction, "horizons": {}}
        for h in horizons:
            s = summarize(by_h[h], direction)
            if s:
                entry["horizons"][f"{h}w"] = s
        rules_out[rule] = entry

    # Печать, отсортировав по осмысленности на 4-недельном горизонте
    def sort_key(item):
        rule, e = item
        h4 = e["horizons"].get("4w") or {}
        return -(h4.get("n") or 0)

    print(f"  {'ПРАВИЛО':24} {'напр':6} {'n':>4} {'hit%':>6} {'med4w':>8} {'avg4w':>8} {'sharpe':>7}")
    print("  " + "─" * 68)
    for rule, e in sorted(rules_out.items(), key=sort_key):
        h4 = e["horizons"].get("4w")
        if not h4:
            continue
        d = (e["expected_direction"] or "—")[:6]
        hit = f"{h4['hit_rate_pct']:.0f}" if h4.get("hit_rate_pct") is not None else "—"
        sh = f"{h4['sharpe_like']:+.2f}" if h4.get("sharpe_like") is not None else "—"
        flag = "" if h4["enough_data"] else "  (мало)"
        print(f"  {rule:24} {d:6} {h4['n']:>4} {hit:>6} "
              f"{h4['median_return_pct']:>+7.2f}% {h4['avg_return_pct']:>+7.2f}% {sh:>7}{flag}")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "method": "historical replay of weekly_history, no lookahead",
        "tokens": len(scans),
        "observation_points": total_points,
        "horizons_weeks": horizons,
        "move_threshold_pct": MOVE_THRESHOLD_PCT,
        "min_n_to_report": MIN_N_TO_REPORT,
        "caveat": ("26 недель на токен — короткая выборка, весь период был "
                   "одной рыночной фазой. Это не доказательство работы правила, "
                   "а способ увидеть заведомо нерабочие."),
        "not_tested": ["phase_verdict", "unified_verdict", "cvd", "volume_profile",
                       "market_regime", "news", "funding"],
        "not_tested_reason": "нет исторических данных, проверяются только форвард-тестом",
        "baseline": base_summary,
        "rules": rules_out,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    ready = sum(1 for e in rules_out.values()
                if (e["horizons"].get("4w") or {}).get("enough_data"))
    print(f"\n  Правил проверено: {len(rules_out)} · "
          f"с выборкой ≥{MIN_N_TO_REPORT}: {ready}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    ap.add_argument("--horizon", type=int, default=0,
                    help="один горизонт в неделях вместо трёх")
    a = ap.parse_args()
    only = set(s.strip().upper() for s in a.token.split(",") if s.strip()) or None
    hz = [a.horizon] if a.horizon else None
    run(only, hz)
