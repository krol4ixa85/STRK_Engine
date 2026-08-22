#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_compass.py · v1.3 · 22.08.2026
STRK ENGINE · шкала LONG / SHORT по каждому активу

ЗАЧЕМ
-----
Движок отвечает «что делать», но не показывает БАЛАНС СИЛ. Иногда важно
видеть не только вердикт, но и то, насколько он уверенный и какая
составляющая тянет в какую сторону.

Компас — это одно число от -100 (сильный шорт) до +100 (сильный лонг)
плюс три составляющие, из которых оно собрано.

ПОЧЕМУ СЧИТАЕТСЯ НА СЕРВЕРЕ
---------------------------
Тот же принцип, что и с решениями: браузерная арифметика не проверяется
и расходится с сервером. Один раз мы это уже получили — в модалке
показывалось 33% рядом с серверными 23%.

Компас читает decisions.json и включает вердикт движка в свой вывод,
поэтому дашборд рисует оба из одного файла и разойтись они не могут.

СОСТАВЛЯЮЩИЕ И ВЕСА
-------------------
  ОНЧЕЙН       40%  фаза Wyckoff, ускорение потока, дивергенция цена/капитал
  ДЕРИВАТИВЫ   30%  premium Hyperliquid, фандинг, перекос позиций
  ТЕХНИКА      30%  положение к зоне объёма, CVD, расстояние до POC

Каждая нормализуется в диапазон от -1 до +1, затем взвешенная сумма
умножается на 100.

ЛОГИКА ЗНАКОВ · ГДЕ ЛЕГКО ОШИБИТЬСЯ
-----------------------------------
Деривативы работают ПРОТИВ толпы. Высокий premium значит, что лонги
переплачивают за вход — это не бычий сигнал, а риск каскада ликвидаций.
Поэтому premium > 0.15% даёт минус, а отрицательный premium (шорты
переплачивают) даёт плюс: их вынос толкает цену вверх.

Ончейн работает ЗА поток. Деньги заходят — плюс, уходят — минус.
Здесь всё прямо.

Техника смешанная: цена выше зоны объёма — минус (нет поддержки),
CVD с агрессивными покупками — плюс.

ЧЕСТНО О ПОРОГАХ
----------------
Все числа ниже подобраны экспертно и на истории не проверялись. Это
способ увидеть баланс, а не измеренное преимущество. Бэктест по ним
появится, когда накопится история asset_compass.json.

ЗАПУСК
------
  python3 scripts/asset_compass.py
  python3 scripts/asset_compass.py --token LINK

ВЫХОД
-----
  data/cache/asset_compass.json
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common.nodata import MISSING, weighted, explain  # noqa: E402

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "asset_compass.json")

# Ниже этой доли измеренного веса вердикт не выдаётся: неполная
# картина не должна выглядеть уверенным сигналом.
MIN_COVERAGE = 0.7

WEIGHTS = {"onchain": 0.40, "derivatives": 0.30, "technical": 0.30}

# Границы вердиктов по итоговому баллу
BANDS = [
    (60,  "STRONG_LONG",  "сильный лонг"),
    (30,  "LONG",         "лонг"),
    (10,  "WEAK_LONG",    "слабый лонг"),
    (-10, "NEUTRAL",      "нейтрально"),
    (-30, "WEAK_SHORT",   "слабый шорт"),
    (-60, "SHORT",        "шорт"),
    (-101, "STRONG_SHORT", "сильный шорт"),
]

# ── ОНЧЕЙН ──
PHASE_SCORE = {
    "MID_ACCUMULATION_STRONG":     0.80,
    "LATE_ACCUMULATION_OR_MARKUP": 0.60,
    "MID_ACCUMULATION":            0.50,
    "ACCUMULATION_PHASE_B":        0.30,
    "EARLY_ACCUMULATION":          0.20,
    "MIXED_OR_NEUTRAL":            0.00,
    "WEAKENING":                  -0.50,
    "MARKDOWN":                   -0.80,
    "DISTRIBUTION_ACTIVE":        -1.00,
}

FLOW_SCORE = {
    "ACCEL_UP":       0.30,
    "FLIPPING_UP":    0.20,
    "STEADY_UP":      0.15,
    "STALLING_DOWN":  0.10,
    "STABLE_ZERO":    0.00,
    "STALLING_UP":   -0.10,
    "STEADY_DOWN":   -0.15,
    "FLIPPING_DOWN": -0.30,
    "ACCEL_DOWN":    -0.40,
}

DIV_SCORE = {"BULLISH_DIV": 0.20, "BEARISH_DIV": -0.30, "ALIGNED": 0.0}

CVD_SCORE = {
    "STRONG_BUY_PRESSURE":  0.40,
    "BUY_PRESSURE":         0.20,
    "MIXED":                0.00,
    "SELL_PRESSURE":       -0.20,
    "STRONG_SELL_PRESSURE": -0.40,
}


def load(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────
# СОСТАВЛЯЮЩИЕ
# ─────────────────────────────────────────────────────────────

def score_onchain(scan, pa):
    """Фаза + динамика потока + дивергенция. Работает ЗА поток."""
    parts = []
    score = 0.0

    phase = (scan or {}).get("phase_verdict")
    if phase in PHASE_SCORE:
        v = PHASE_SCORE[phase]
        score += v
        parts.append({"name": "фаза", "value": round(v, 2), "detail": phase})

    regime = ((pa or {}).get("regime") or {}).get("code")
    if regime in FLOW_SCORE:
        v = FLOW_SCORE[regime]
        score += v
        parts.append({"name": "динамика потока", "value": round(v, 2),
                      "detail": regime})

    div = ((pa or {}).get("divergence") or {}).get("code")
    if div in DIV_SCORE and DIV_SCORE[div] != 0:
        v = DIV_SCORE[div]
        score += v
        parts.append({"name": "дивергенция", "value": round(v, 2), "detail": div})

    return clamp(score), parts


def score_derivatives(hl, funding):
    """
    Premium и фандинг. Работает ПРОТИВ толпы: чем сильнее перекос,
    тем выше риск разворота против большинства.
    """
    parts = []
    score = 0.0

    if hl and hl.get("status") == "OK":
        prem = hl.get("premium_pct")
        if prem is not None:
            if prem > 0.15:
                v = -0.50
                d = f"лонги сильно переплачивают ({prem:+.3f}%)"
            elif prem > 0.05:
                v = -0.20
                d = f"перекос в лонги ({prem:+.3f}%)"
            elif prem < -0.15:
                v = 0.40
                d = f"шорты сильно переплачивают ({prem:+.3f}%) — топливо для выноса"
            elif prem < -0.05:
                v = 0.20
                d = f"перекос в шорты ({prem:+.3f}%)"
            else:
                v = 0.0
                d = f"баланс ({prem:+.3f}%)"
            score += v
            parts.append({"name": "премия HL", "value": round(v, 2), "detail": d})

        apr = hl.get("funding_annualised_pct")
        if apr is not None:
            if apr > 50:
                v = -0.40
                d = f"держать лонг очень дорого ({apr:.0f}% годовых)"
            elif apr > 20:
                v = -0.20
                d = f"лонги платят {apr:.0f}% годовых"
            elif apr < -20:
                v = 0.30
                d = f"шорты платят {abs(apr):.0f}% годовых"
            else:
                v = 0.0
                d = f"фандинг спокойный ({apr:.0f}% годовых)"
            score += v
            parts.append({"name": "фандинг", "value": round(v, 2), "detail": d})

        oi = hl.get("open_interest_usd") or 0
        if oi and oi < 5e6:
            parts.append({"name": "открытый интерес", "value": 0.0,
                          "detail": f"${oi/1e6:.1f}M — деривативов мало, "
                                    f"сигналам этого блока веры меньше"})

    if not parts and funding:
        bias = (funding.get("tokens") or {}).get("_") or {}
        if bias:
            parts.append({"name": "фандинг OKX", "value": 0.0,
                          "detail": "данных HL нет"})

    return clamp(score), parts


def score_technical(vp, cvd):
    """Положение к зоне объёма + агрессия покупателей."""
    parts = []
    score = 0.0

    if vp and vp.get("status") == "OK":
        pos = vp.get("position") or {}
        code = pos.get("code")
        dist = pos.get("distance_to_poc_pct")

        if code == "ABOVE_VALUE":
            # ФИКС 21.08.2026 · выход вверх на объёме — это markup,
            # а не «дорого». Раньше любая цена выше VAH давала минус,
            # причём вдвое сильнее, чем BELOW_VALUE давало плюс.
            kind = pos.get("above_kind")
            vr = pos.get("vol_ratio_recent")
            if kind == "MARKUP":
                v = 0.20
                d = (f"цена вышла вверх из зоны объёма на объёме ×{vr} — "
                     f"рынок принимает новую цену")
            elif kind == "EXTENDED":
                if dist is not None and dist > 30:
                    v = -0.60
                    d = (f"цена на {dist:.0f}% выше справедливой, "
                         f"объём не подтверждает (×{vr})")
                else:
                    v = -0.40
                    d = f"цена выше зоны объёма, объём не подтверждает (×{vr})"
            else:
                # above_kind ещё нет — volume_profile.json старой версии.
                # До первого прогона нового коллектора (максимум 6 часов)
                # ведём себя как раньше, чтобы миграция ничего не сдвинула
                # молча в сторону меньшей осторожности.
                if dist is not None and dist > 30:
                    v = -0.60
                    d = f"цена на {dist:.0f}% выше справедливой, поддержки сверху нет"
                else:
                    v = -0.40
                    d = "цена выше зоны объёма"
        elif code == "BELOW_VALUE":
            v = 0.40
            d = "цена ниже зоны объёма — потенциал возврата вверх"
        else:
            v = 0.10
            d = "цена внутри зоны объёма"
        score += v
        parts.append({"name": "положение к объёму", "value": round(v, 2), "detail": d})

        # Есть ли куда расти по объёму — отдельный штрих
        tg = vp.get("targets") or {}
        if tg.get("has_volume_targets_up") is False and tg.get("nearest_up"):
            parts.append({"name": "цели вверх", "value": 0.0,
                          "detail": "только структурные, объёмных уровней сверху нет"})

    if cvd and cvd.get("status") == "OK":
        cons = cvd.get("consensus") or {}
        code = cons.get("code")
        if code in CVD_SCORE:
            v = CVD_SCORE[code]
            score += v
            parts.append({"name": "агрессия CVD", "value": round(v, 2),
                          "detail": cons.get("text_ru", code)})

    return clamp(score), parts


def verdict_for(score):
    for threshold, code, ru in BANDS:
        if score >= threshold:
            return code, ru
    return "NEUTRAL", "нейтрально"


# ─────────────────────────────────────────────────────────────
# СБОРКА
# ─────────────────────────────────────────────────────────────

def compute(token, sig):
    scan = load(f"token_scan/{token}.json")
    pa = (sig["phase"].get("tokens") or {}).get(token)
    hl = (sig["hl"].get("tokens") or {}).get(token)
    vp = (sig["vp"].get("tokens") or {}).get(token)
    cvd = (sig["cvd"].get("tokens") or {}).get(token)
    dec = (sig["dec"].get("decisions") or {}).get(token)

    # Битые данные — компас не строим, иначе он оправдает мусор
    dq = (pa or {}).get("data_quality") or {}
    if dq and not dq.get("ok", True):
        return {
            "token": token,
            "status": "DATA_SUSPICIOUS",
            "score": None,
            "verdict": "NO_SIGNAL",
            "verdict_ru": "данные забракованы фильтром качества",
            "decision_action": (dec or {}).get("action"),
        }

    onchain, p_on = score_onchain(scan, pa)
    deriv, p_der = score_derivatives(hl, sig["funding"])
    tech, p_tech = score_technical(vp, cvd)

    present = {
        "onchain": bool(p_on),
        "derivatives": bool(p_der),
        "technical": bool(p_tech),
    }

    # ФИКС 21.08.2026 · комментарий здесь утверждал ровно правильное,
    # а код делал обратное: отсутствующий блок входил в сумму нулём и
    # ВСЁ РАВНО умножался на свой вес. Токен с ончейн-баллом -1.00 и без
    # двух других блоков получал -40 («шорт») вместо -100 («сильный
    # шорт») — отсутствие данных работало как голос за нейтральность.
    #
    # Теперь вес перераспределяется на присутствующие блоки, а покрытие
    # пишется честно. При покрытии ниже 70% вердикт не выдаётся: три
    # десятых картины — не повод называть актив сильным шортом.
    agg = weighted([
        (onchain if p_on else MISSING, WEIGHTS["onchain"], "ончейн"),
        (deriv if p_der else MISSING, WEIGHTS["derivatives"], "деривативы"),
        (tech if p_tech else MISSING, WEIGHTS["technical"], "техника"),
    ], min_coverage=MIN_COVERAGE)

    if agg["score"] is MISSING:
        return {
            "token": token,
            "status": "NO_DATA",
            "score": None,
            "verdict": "NO_SIGNAL",
            "verdict_ru": "ни один слой не посчитан",
            "data_coverage_pct": 0,
            "missing_blocks": agg["missing"],
            "decision_action": (dec or {}).get("action"),
        }

    score = round(agg["score"] * 100, 1)
    if not agg["enough"]:
        return {
            "token": token,
            "status": "LOW_COVERAGE",
            "score": score,
            "verdict": "NO_SIGNAL",
            "verdict_ru": (f"измерено {agg['coverage_pct']}% картины — "
                           f"для вердикта мало"),
            "data_coverage_pct": agg["coverage_pct"],
            "missing_blocks": agg["missing"],
            "coverage_note": explain(agg["missing"], "вердикта компаса"),
            "components": {
                "onchain": {"score": onchain, "weight": WEIGHTS["onchain"],
                            "parts": p_on, "has_data": bool(p_on)},
                "derivatives": {"score": deriv, "weight": WEIGHTS["derivatives"],
                                "parts": p_der, "has_data": bool(p_der)},
                "technical": {"score": tech, "weight": WEIGHTS["technical"],
                              "parts": p_tech, "has_data": bool(p_tech)},
            },
            "decision_action": (dec or {}).get("action"),
        }

    code, ru = verdict_for(score)

    return {
        "token": token,
        "status": "OK",
        "score": score,
        "verdict": code,
        "verdict_ru": ru,
        "components": {
            "onchain":     {"score": round(onchain, 2), "weight": WEIGHTS["onchain"],
                            "parts": p_on, "has_data": present["onchain"]},
            "derivatives": {"score": round(deriv, 2), "weight": WEIGHTS["derivatives"],
                            "parts": p_der, "has_data": present["derivatives"]},
            "technical":   {"score": round(tech, 2), "weight": WEIGHTS["technical"],
                            "parts": p_tech, "has_data": present["technical"]},
        },
        "data_coverage_pct": round(sum(WEIGHTS[k] for k, v in present.items() if v) * 100),
        # Вердикт движка кладём рядом, чтобы дашборд рисовал оба из
        # одного файла и они не могли разойтись
        "decision_action": (dec or {}).get("action"),
        "decision_size_pct": (dec or {}).get("size_pct"),
        "current_price": (vp or {}).get("current_price"),
        "targets_up": ((vp or {}).get("targets") or {}).get("nearest_up", [])[:3],
        "targets_down": ((vp or {}).get("targets") or {}).get("nearest_down", [])[:3],
    }


def discover_tokens():
    out = []
    for p in sorted(glob.glob(os.path.join(CACHE, "token_scan", "*.json"))):
        n = os.path.basename(p)[:-5].upper()
        if n not in ("INDEX", "_META"):
            out.append(n)
    return out


def main(only=None):
    print("=== Asset Compass v1.3 ===\n")

    sig = {
        "phase": load("phase_analysis.json", {}) or {},
        "hl": load("hl_perps.json", {}) or {},
        "vp": load("volume_profile.json", {}) or {},
        "cvd": load("cvd_multi.json", {}) or {},
        "dec": load("decisions.json", {}) or {},
        "funding": load("funding_per_token.json", {}) or {},
    }

    missing = [k for k, v in sig.items() if not v]
    if missing:
        print(f"⚠ Нет данных: {', '.join(missing)} — покрытие будет неполным\n")

    tokens = only or discover_tokens()
    out_tokens = {}
    ok = 0

    print(f"  {'ТОКЕН':8} {'БАЛЛ':>7} {'ВЕРДИКТ':14} "
          f"{'онч':>6} {'дер':>6} {'тех':>6}  {'покрытие':>9}")
    print("  " + "─" * 66)

    for t in sorted(tokens):
        r = compute(t, sig)
        out_tokens[t] = r
        if r.get("status") != "OK":
            # Причины разные, и путать их нельзя: «забракованы фильтром
            # качества» и «измерена треть картины» — это не одно и то же.
            why = {
                "DATA_SUSPICIOUS": "данные забракованы фильтром качества",
                "LOW_COVERAGE": f"измерено {r.get('data_coverage_pct', 0)}% картины — мало для вердикта",
                "NO_DATA": "ни один слой не посчитан",
            }.get(r.get("status"), r.get("verdict_ru") or "нет вердикта")
            print(f"  {t:8} {'—':>7} {why}")
            continue
        ok += 1
        c = r["components"]
        print(f"  {t:8} {r['score']:>+7.1f} {r['verdict']:14} "
              f"{c['onchain']['score']:>+6.2f} {c['derivatives']['score']:>+6.2f} "
              f"{c['technical']['score']:>+6.2f}  {r['data_coverage_pct']:>8}%")

    # Крайности — с них начинается внимание
    # ФИКС 22.08.2026 · было `if r.get("score") is not None`.
    # После введения LOW_COVERAGE у таких строк балл есть, а вердикта
    # нет — и AKT одновременно печатался как «данные забракованы» и как
    # второй сильнейший лонг (+20). Ровно та же путаница, которую этот
    # заход и лечил: неполная картина не должна попадать в крайности.
    scored = [r for r in out_tokens.values()
              if r.get("status") == "OK" and r.get("score") is not None]
    longs = sorted(scored, key=lambda r: -r["score"])[:5]
    shorts = sorted(scored, key=lambda r: r["score"])[:5]

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": "compass/1.0",
        "weights": WEIGHTS,
        "thresholds_status": "HYPOTHESIS · пороги экспертные, на истории не проверены",
        "tokens_ok": ok,
        "top_long": [{"token": r["token"], "score": r["score"],
                      "verdict": r["verdict"]} for r in longs if r["score"] > 0],
        "top_short": [{"token": r["token"], "score": r["score"],
                       "verdict": r["verdict"]} for r in shorts if r["score"] < 0],
        "tokens": out_tokens,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Посчитано: {ok}")
    if out["top_long"]:
        print("  Сильнее всего в лонг: " +
              " · ".join(f"{x['token']} {x['score']:+.0f}" for x in out["top_long"][:3]))
    if out["top_short"]:
        print("  Сильнее всего в шорт: " +
              " · ".join(f"{x['token']} {x['score']:+.0f}" for x in out["top_short"][:3]))
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    only = [s.strip().upper() for s in a.token.split(",") if s.strip()] or None
    main(only)
