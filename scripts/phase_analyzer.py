#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase_analyzer.py · v1.2 · 21.08.2026
STRK ENGINE · быстрая часть анализа фазы, работает каждые 30 мин

ЗАЧЕМ
-----
Dune-скан считает фазу раз в неделю. Между обновлениями движок ничего
нового не узнаёт, хотя цена и фандинг двигаются каждые минуты.

Для свинг-горизонта 3-14 дней важна не сама фаза, а её ИЗМЕНЕНИЕ.
Ускоряется ли приток. Разворачивается ли фандинг. Замедляется ли цена.
Эти вопросы можно и нужно считать чаще Dune.

ЧТО СЧИТАЕТ
-----------
Для каждого токена:

  flow_accel_4w     ускорение потока: последние 4 недели vs предыдущие 4
  flow_accel_8w     то же на 8-недельном окне
  flow_velocity     скользящая скорость притока $/неделю
  price_momentum_7d  свежий price change
  price_momentum_30d то же на месяце

  divergence_flag   цена растёт, а деньги уходят (или наоборот)
  data_quality      OK / SUSPICIOUS если price change > 500%
  regime_hint       ACCEL_UP / ACCEL_DOWN / STALLING / FLIPPING / STABLE

ИНТЕРПРЕТАЦИЯ
-------------
flow_accel_4w > 0 и большой  → приток разгоняется, ранняя фаза
flow_accel_4w > 0 маленький  → еле-еле в плюс
flow_accel_4w < 0 при positive flow → приток затухает, близко к вершине
flow_accel_4w < 0 при negative flow → отток ускоряется, распродажа

flow_velocity показывает средний темп: сколько $/неделю в среднем.
Для сравнения токенов между собой лучше чем абсолютные цифры.

data_quality SUSPICIOUS означает что этот токен нельзя использовать
в rotation-скане как STRONG_BUY кандидата. Битые данные Dune-момента
20.08 дали ARB +1 717 040% за неделю — правило rotation честно сработало
на цифру, которой не бывает. Здесь такие данные помечаются и вычёркиваются.

БЕЗ ЕДИНОГО ЗАПРОСА К DUNE
--------------------------
Работает ТОЛЬКО с локальными файлами:
  data/cache/token_scan/*.json       weekly-история от Dune
  data/cache/hive_prices.json        свежие цены и объёмы
  data/cache/funding_per_token.json  фандинг с OKX

Значит стоит 0 кредитов и может крутиться сколь угодно часто.
На расписании — каждые 30 минут, синхронно с funding.

ЗАПУСК
------
  python3 scripts/phase_analyzer.py           # все токены
  python3 scripts/phase_analyzer.py --token LINK,STRK

ВЫХОД
-----
  data/cache/phase_analysis.json
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime, timezone

CACHE = "data/cache"
SCAN_DIR = os.path.join(CACHE, "token_scan")
OUT_FILE = os.path.join(CACHE, "phase_analysis.json")

# Битая цена: изменение больше этого — данные Dune-запроса сбились
# в единицах (например wei вместо eth). См. ARB +1 717 040% 20.08.
DATA_QUALITY_MAX_PCT = 500.0

# Значимое ускорение потока: разница должна быть больше этого, чтобы
# считать её сигналом, а не шумом пересчёта окон.
FLOW_ACCEL_NOISE_USD = 500_000


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def discover_tokens():
    out = []
    for p in sorted(glob.glob(os.path.join(SCAN_DIR, "*.json"))):
        name = os.path.basename(p)[:-5].upper()
        if name and name not in ("INDEX", "_META"):
            out.append(name)
    return out


# ─────────────────────────────────────────────────────────────
# УСКОРЕНИЕ ПОТОКА · главное измерение
# ─────────────────────────────────────────────────────────────

# Лестница окон в неделях. Одно окно отвечает только «растёт или падает»,
# а их набор показывает форму разгона: нарастает он, держится плато
# или уже выдыхается. Это и есть «где мы внутри фазы».
FLOW_WINDOWS = [
    ("1w",  1),
    ("2w",  2),
    ("4w",  4),
    ("8w",  8),
    ("13w", 13),
]


def analyze_flow(scan):
    """
    Дельты потока по лестнице окон. Каждое окно сравнивается с
    предыдущим отрезком той же длины — так видно ускорение, а не
    просто величину.

    Данные берутся из weekly_history, который приходит с Dune-сканом.
    Дополнительных запросов не нужно: 26 недель уже лежат в файле.
    """
    if not scan:
        return None

    weekly = scan.get("weekly_history") or []
    if len(weekly) < 4:
        return {"status": "NOT_ENOUGH_HISTORY", "weeks": len(weekly)}

    flows = [float(w.get("net_flow_m_usd") or 0) for w in weekly]
    n = len(flows)

    def window_sum(offset_from_end, size):
        end = n - offset_from_end
        start = end - size
        if start < 0:
            return None
        return sum(flows[start:end])

    result = {"status": "OK", "weeks_available": n, "windows": {}}

    for label, size in FLOW_WINDOWS:
        last = window_sum(0, size)
        prev = window_sum(size, size)
        if last is None:
            continue

        entry = {
            "weeks": size,
            "days": size * 7,
            "last_usd": round(last * 1e6),
            "prev_usd": round(prev * 1e6) if prev is not None else None,
            # Скорость в неделю — так окна разной длины сравнимы между собой
            "velocity_weekly_usd": round(last / size * 1e6),
        }
        if prev is not None:
            accel = (last - prev) * 1e6
            entry["accel_usd"] = round(accel)
            entry["significant"] = abs(accel) >= FLOW_ACCEL_NOISE_USD
        result["windows"][label] = entry

    # Совместимость: старые поля продолжают работать, чтобы decision_engine
    # и дашборд не сломались до того, как перейдут на новую структуру
    w4 = result["windows"].get("4w") or {}
    w8 = result["windows"].get("8w") or {}
    result.update({
        "last_4w_usd": w4.get("last_usd"),
        "prev_4w_usd": w4.get("prev_usd"),
        "last_8w_usd": w8.get("last_usd"),
        "prev_8w_usd": w8.get("prev_usd"),
        "flow_accel_4w_usd": w4.get("accel_usd"),
        "flow_accel_4w_significant": w4.get("significant", False),
        "flow_accel_8w_usd": w8.get("accel_usd"),
        "flow_accel_8w_significant": w8.get("significant", False),
        "flow_velocity_weekly_usd": w4.get("velocity_weekly_usd"),
    })

    result["shape"] = flow_shape(result["windows"])
    return result


def flow_shape(windows):
    """
    Форма разгона по лестнице скоростей. Отвечает на вопрос, где мы
    внутри фазы, а не только куда идёт поток.

    Сравниваем скорость в неделю на коротких и длинных окнах:

      скорость растёт к короткому концу → разгон нарастает, фаза молодая
      скорости примерно равны            → плато, фаза зрелая
      скорость падает к короткому концу  → разгон выдыхается, фаза к концу

    Порог 25% выбран так, чтобы обычные недельные колебания не
    выглядели сменой формы. На истории не проверялся.
    """
    v = {}
    for label in ("1w", "2w", "4w", "8w", "13w"):
        w = windows.get(label)
        if w and w.get("velocity_weekly_usd") is not None:
            v[label] = w["velocity_weekly_usd"]

    if len(v) < 3:
        return {"code": "UNKNOWN", "text_ru": "истории мало для формы разгона"}

    short = v.get("1w", v.get("2w"))
    mid = v.get("4w")
    long = v.get("8w", v.get("13w"))

    if short is None or mid is None or long is None:
        return {"code": "UNKNOWN", "text_ru": "не хватает окон"}

    # Направление берём по среднему окну — оно устойчивее недельного шума
    direction = "IN" if mid > 0 else ("OUT" if mid < 0 else "FLAT")

    def rel(a, b):
        if b == 0:
            return 0.0
        return (a - b) / abs(b)

    short_vs_mid = rel(short, mid)
    mid_vs_long = rel(mid, long)

    building = short_vs_mid > 0.25 and mid_vs_long > 0.25
    fading = short_vs_mid < -0.25
    plateau = abs(short_vs_mid) <= 0.25

    if direction == "FLAT":
        return {"code": "FLAT", "stage_pct": None,
                "text_ru": "потока почти нет, о фазе говорить рано"}

    if direction == "IN":
        if building:
            return {"code": "ACCUMULATION_BUILDING", "stage_pct": 25,
                    "text_ru": "разгон притока нарастает — фаза молодая, "
                               "самое начало"}
        if plateau:
            return {"code": "ACCUMULATION_MATURE", "stage_pct": 55,
                    "text_ru": "приток идёт ровно — фаза зрелая, середина"}
        if fading:
            return {"code": "ACCUMULATION_FADING", "stage_pct": 85,
                    "text_ru": "разгон притока выдыхается — фаза к концу, "
                               "новые входы дороже"}
        return {"code": "ACCUMULATION_MIXED", "stage_pct": 50,
                "text_ru": "приток без чёткой формы"}

    if building:
        return {"code": "DISTRIBUTION_BUILDING", "stage_pct": 25,
                "text_ru": "отток ускоряется — распродажа только началась"}
    if plateau:
        return {"code": "DISTRIBUTION_MATURE", "stage_pct": 55,
                "text_ru": "отток идёт ровно — распродажа в разгаре"}
    if fading:
        return {"code": "DISTRIBUTION_FADING", "stage_pct": 85,
                "text_ru": "отток замедляется — распродажа выдыхается, "
                           "возможное дно"}
    return {"code": "DISTRIBUTION_MIXED", "stage_pct": 50,
            "text_ru": "отток без чёткой формы"}


# ─────────────────────────────────────────────────────────────
# СИГНАЛ РЕЖИМА · короткая формулировка что происходит
# ─────────────────────────────────────────────────────────────

def classify_regime(flow):
    """
    Комбинирует знак потока и ускорение в одну метку. Читать так:

    ACCEL_UP      деньги заходят и разгон растёт    → лучшее окно входа
    STEADY_UP     деньги заходят стабильно          → зрелый приток
    STALLING_UP   деньги заходят но темп падает     → возможная вершина
    FLIPPING_UP   вышли из оттока в приток          → разворот на подтверждение
    STABLE_ZERO   поток около нуля                   → без сигнала
    FLIPPING_DOWN приток закончился, идёт отток     → выход
    STALLING_DOWN отток замедляется                  → возможное дно
    STEADY_DOWN   стабильный отток                   → распродажа
    ACCEL_DOWN    отток ускоряется                   → капитуляция
    """
    if flow.get("status") != "OK":
        return {"code": "UNKNOWN", "text_ru": "нет данных"}

    last = flow.get("last_4w_usd") or 0
    accel = flow.get("flow_accel_4w_usd") or 0
    sig = flow.get("flow_accel_4w_significant", False)

    # Пороги в долларах: значимо начиная с $500K
    small = FLOW_ACCEL_NOISE_USD

    if abs(last) < small and abs(accel) < small:
        return {"code": "STABLE_ZERO", "text_ru": "поток около нуля, без сигнала"}

    # Приток
    if last > 0:
        if sig and accel > 0:
            return {"code": "ACCEL_UP",
                    "text_ru": "приток разгоняется — лучшее окно для входа"}
        if sig and accel < 0:
            # Приток есть, но темп упал — возможная вершина
            return {"code": "STALLING_UP",
                    "text_ru": "приток есть, но темп падает — возможная вершина"}
        if last > small and abs(accel) < small:
            return {"code": "STEADY_UP",
                    "text_ru": "стабильный приток без ускорения"}
        # Раньше был отток, теперь плюс
        prev = flow.get("prev_4w_usd") or 0
        if prev < -small and last > 0:
            return {"code": "FLIPPING_UP",
                    "text_ru": "развернулось из оттока в приток — молодой разворот"}
        return {"code": "STEADY_UP", "text_ru": "лёгкий приток"}

    # Отток
    if last < 0:
        if sig and accel < 0:
            return {"code": "ACCEL_DOWN",
                    "text_ru": "отток ускоряется — капитуляция или distribution"}
        if sig and accel > 0:
            return {"code": "STALLING_DOWN",
                    "text_ru": "отток замедляется — возможное дно"}
        prev = flow.get("prev_4w_usd") or 0
        if prev > small and last < 0:
            return {"code": "FLIPPING_DOWN",
                    "text_ru": "приток закончился, пошёл отток — сигнал выхода"}
        return {"code": "STEADY_DOWN", "text_ru": "стабильный отток"}

    return {"code": "STABLE_ZERO", "text_ru": "нейтрально"}


# ─────────────────────────────────────────────────────────────
# ДИВЕРГЕНЦИЯ · цена vs капитал
# ─────────────────────────────────────────────────────────────

def price_flow_divergence(scan, flow, price_ref=None):
    """
    Классическая дивергенция: цена в одну сторону, деньги в другую.
    Правильный сигнал разворота, если направления рассогласовались.
    """
    # Опорные цены с биржи имеют приоритет над ценами Dune
    if price_ref and price_ref.get("status") == "OK" and price_ref.get("change_30d_pct") is not None:
        price_pct = price_ref["change_30d_pct"]
    else:
        price_now = scan.get("price_now")
        price_30d = scan.get("price_30d_ago")
        if not price_now or not price_30d or price_30d <= 0:
            return None
        price_pct = (price_now / price_30d - 1) * 100
    last_flow = flow.get("last_4w_usd") if flow else None

    if last_flow is None:
        return {"code": "UNKNOWN"}

    # Битые данные
    if abs(price_pct) > DATA_QUALITY_MAX_PCT:
        return {"code": "DATA_SUSPICIOUS",
                "price_change_30d_pct": round(price_pct, 1),
                "note": "изменение цены больше 500% — данные подозрительны"}

    # Дивергенция
    if price_pct > 5 and last_flow < -FLOW_ACCEL_NOISE_USD:
        return {"code": "BEARISH_DIV",
                "text_ru": f"цена +{price_pct:.0f}% за 30д, но приток ${last_flow/1e6:+.1f}M — рост без капитала",
                "price_change_30d_pct": round(price_pct, 1),
                "flow_last_4w_usd": last_flow}
    if price_pct < -5 and last_flow > FLOW_ACCEL_NOISE_USD:
        return {"code": "BULLISH_DIV",
                "text_ru": f"цена {price_pct:.0f}% за 30д, а деньги заходят ${last_flow/1e6:+.1f}M — накопление на просадке",
                "price_change_30d_pct": round(price_pct, 1),
                "flow_last_4w_usd": last_flow}

    return {"code": "ALIGNED",
            "text_ru": "цена и капитал движутся в одну сторону",
            "price_change_30d_pct": round(price_pct, 1),
            "flow_last_4w_usd": last_flow}


# ─────────────────────────────────────────────────────────────
# КАЧЕСТВО ДАННЫХ · чёрный список
# ─────────────────────────────────────────────────────────────

def check_data_quality(scan, momentum_row, price_ref=None):
    """
    Отделяет реальные сигналы от артефактов Dune-момента.

    Пример 20.08.2026: ARB получил STRONG_BUY на price_change_7d = +1 717 040%,
    потому что price_7d_ago в запросе вернулся в неправильных единицах.
    Такие цифры не бывают у настоящих активов — фильтруем.
    """
    flags = []

    # Если есть проверенная опорная цена с биржи, битые цены Dune больше
    # не повод браковать токен: потоки Dune остаются пригодными, а цены
    # мы берём из другого места. Раньше из-за этого выпадали шесть
    # токенов целиком — ARB, BONK, DOGE, TAO, UNI, WIF.
    ref_ok = bool(price_ref and price_ref.get("status") == "OK"
                  and (price_ref.get("sanity") or {}).get("ok"))
    if ref_ok:
        return {"ok": True, "flags": [],
                "note": "цены взяты из price_reference (биржа), Dune-цены игнорируются"}

    if momentum_row and momentum_row.get("price_change_7d_pct") is not None:
        pct = momentum_row["price_change_7d_pct"]
        if abs(pct) > DATA_QUALITY_MAX_PCT:
            flags.append({"source": "dune_sector_momentum",
                          "field": "price_change_7d_pct",
                          "value": pct,
                          "reason": f"изменение цены {pct:.0f}% > 500% — единицы измерения сбились"})

    price_now = scan.get("price_now") if scan else None
    price_30d = scan.get("price_30d_ago") if scan else None
    if price_now and price_30d and price_30d > 0:
        pct = (price_now / price_30d - 1) * 100
        if abs(pct) > DATA_QUALITY_MAX_PCT:
            flags.append({"source": "token_scan",
                          "field": "price_30d_ago",
                          "value": pct,
                          "reason": f"30-дневное изменение {pct:.0f}% > 500%"})

    return {"ok": len(flags) == 0, "flags": flags}


# ─────────────────────────────────────────────────────────────
# ГЛАВНЫЙ ПРОХОД
# ─────────────────────────────────────────────────────────────

def analyze_all(only=None):
    now = datetime.now(timezone.utc)
    prices_hive = load_json(os.path.join(CACHE, "hive_prices.json"), {})
    # Опорные цены с биржи. Появились после того, как выяснилось, что Dune
    # выводит цену из отдельных DEX-сделок и на ARB/UNI/DOGE даёт мусор
    # (+1 717 040% за неделю). Если файл есть — цены берём отсюда.
    price_ref = load_json(os.path.join(CACHE, "price_reference.json"), {}) or {}
    funding = load_json(os.path.join(CACHE, "funding_per_token.json"), {})
    momentum = load_json(os.path.join(CACHE, "dune_sector_momentum.json"), {})

    # Индекс momentum по токену
    momentum_by_token = {}
    for row in (momentum.get("rows") or []):
        if row.get("token"):
            momentum_by_token[row["token"]] = row

    tokens = only or discover_tokens()

    result = {"computed_at": now.isoformat(),
              "engine_version": "phase_analyzer/1.2",
              "cost": "0 credits · locally-computed",
              "tokens_analyzed": 0,
              "tokens_suspicious": 0,
              "sources": {
                  "token_scan": len(discover_tokens()),
                  "hive_prices": bool(prices_hive.get("prices")),
                  "funding": bool(funding.get("tokens")),
                  "momentum": bool(momentum_by_token),
              },
              "tokens": {}}

    print(f"=== phase_analyzer v1.0 · {len(tokens)} токенов ===\n")

    for t in tokens:
        scan = load_json(os.path.join(SCAN_DIR, f"{t}.json"))
        if not scan:
            continue

        flow = analyze_flow(scan)
        regime = classify_regime(flow) if flow else {"code": "NO_DATA"}
        pref = (price_ref.get("tokens") or {}).get(t)
        divergence = price_flow_divergence(scan, flow, pref)
        quality = check_data_quality(scan, momentum_by_token.get(t), pref)

        f = (funding.get("tokens") or {}).get(t) or {}

        entry = {
            "phase_verdict_dune": scan.get("phase_verdict"),
            "phase_verdict_age_min": None,
            "flow": flow,
            "regime": regime,
            "divergence": divergence,
            "data_quality": quality,
            "funding_bias": f.get("bias"),
            "funding_rate_pct": f.get("funding_rate_pct"),
        }

        # Возраст скана — важно чтобы понимать насколько актуальна фаза
        try:
            ts = scan.get("collected_at")
            if ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                entry["phase_verdict_age_min"] = round((now - dt).total_seconds() / 60)
        except Exception:
            pass

        result["tokens"][t] = entry
        result["tokens_analyzed"] += 1
        if not quality["ok"]:
            result["tokens_suspicious"] += 1

        # Печать в лог
        r_code = regime.get("code", "?")
        q_flag = "" if quality["ok"] else " ⚠BAD_DATA"
        accel = flow.get("flow_accel_4w_usd", 0) / 1e6 if flow and flow.get("flow_accel_4w_usd") is not None else 0
        print(f"  {t:8} {entry['phase_verdict_dune'] or '—':28} {r_code:14} accel4w ${accel:+.2f}M{q_flag}")

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n  Проанализировано: {result['tokens_analyzed']}")
    print(f"  Подозрительных: {result['tokens_suspicious']}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    only = [s.strip().upper() for s in a.token.split(",") if s.strip()] or None
    analyze_all(only)
