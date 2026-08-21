#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cvd_multi_collector.py · v1.0 · 21.08.2026
STRK ENGINE · CVD по всем токенам

ЗАЧЕМ
-----
Существующий cvd_analysis.py считает CVD только для STRK. Логика в нём
правильная, но для ротации нужны все 32 токена: без CVD непонятно, кто
двигает цену — агрессивные покупатели или просто тонкий стакан.

ЧТО ТАКОЕ CVD
-------------
Cumulative Volume Delta = накопленная разница между агрессивными
покупками и агрессивными продажами (taker orders).

Taker — тот, кто бьёт по стакану рыночной заявкой, то есть готов
переплатить за немедленное исполнение. Это и есть агрессия.
Maker — тот, кто стоит лимиткой и ждёт.

ГЛАВНЫЙ СИГНАЛ · ДИВЕРГЕНЦИЯ
----------------------------
  цена ВВЕРХ  + CVD ВНИЗ  → DISTRIBUTION divergence
     Розница агрессивно покупает, киты продают лимитками.
     Цену тянут вверх мелкими покупками, а крупные разгружаются.
     Классическая вершина.

  цена ВНИЗ + CVD ВВЕРХ → ACCUMULATION divergence
     Розница агрессивно продаёт, киты подбирают лимитками.
     Классическое дно.

  цена и CVD в одну сторону → здоровый тренд, продолжение вероятно

ТРИ ОКНА
--------
  1H  · 24 точки  — что происходит прямо сейчас
  4H  · 42 точки  — среднесрочная агрессия (неделя)
  1D  · 30 точек  — месячная картина

Дивергенция на 1H — шум чаще чем сигнал. Дивергенция на 1D — серьёзно.
Совпадение на всех трёх — сильнейший сигнал.

ИСТОЧНИК · БЕСПЛАТНО
--------------------
OKX rubik/stat/taker-volume — публичный endpoint, ноль ключей,
720 точек за запрос. Работает по всем нашим токенам.

ЗАПУСК
------
  python3 scripts/collectors/cvd_multi_collector.py
  python3 scripts/collectors/cvd_multi_collector.py --token LINK,STRK

ВЫХОД
-----
  data/cache/cvd_multi.json
"""

import os
import sys
import json
import time
import argparse
import urllib.request
from datetime import datetime, timezone

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "cvd_multi.json")

OKX_TAKER = "https://www.okx.com/api/v5/rubik/stat/taker-volume"
OKX_CANDLES = "https://www.okx.com/api/v5/market/candles"

# Наши токены. OKX использует тикер как есть.
TOKENS = [
    "BTC", "ETH", "SOL",
    "STRK", "LINK", "ETHFI", "MORPHO", "ONDO",
    "ARB", "OP", "MNT", "ZK",
    "AAVE", "PENDLE", "LDO", "CRV", "COMP", "SNX",
    "DYDX", "GMX", "UNI", "FXS", "ENA",
    "EIGEN", "JTO",
    "TAO", "FET", "RNDR", "AIXBT", "VIRTUAL",
    "TIA", "SEI", "SUI", "APT", "INJ",
    "BONK", "PEPE", "DOGE", "WIF",
    "AXS", "SAND", "FIL", "GRT", "RPL", "AKT", "CFG",
]

# Окна: сколько точек берём для каждого таймфрейма
WINDOWS = {
    "1H": {"period": "1H", "points": 24,  "label": "сутки"},
    "4H": {"period": "4H", "points": 42,  "label": "неделя"},
    "1D": {"period": "1D", "points": 30,  "label": "месяц"},
}

# Порог значимого изменения. Ниже — шум, не дивергенция.
PRICE_NOISE_PCT = 1.0
CVD_NOISE_SHARE = 0.02   # 2% от суммарного объёма

REQUEST_DELAY = 0.12


def http_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_taker(ccy, period):
    """OKX taker volume: [[ts, sellVol, buyVol], ...] от новых к старым."""
    url = f"{OKX_TAKER}?ccy={ccy}&instType=SPOT&period={period}"
    data = http_json(url)
    if not data or data.get("code") != "0":
        return None
    rows = data.get("data") or []
    # разворачиваем в хронологический порядок
    return list(reversed(rows))


def fetch_candles(inst_id, bar, limit):
    """OKX candles: [[ts, o, h, l, c, vol, volCcy, ...], ...]"""
    url = f"{OKX_CANDLES}?instId={inst_id}&bar={bar}&limit={limit}"
    data = http_json(url)
    if not data or data.get("code") != "0":
        return None
    rows = data.get("data") or []
    return list(reversed(rows))


def analyze_window(ccy, period, points):
    """Считает CVD и цену за окно, возвращает классификацию."""
    taker = fetch_taker(ccy, period)
    time.sleep(REQUEST_DELAY)
    if not taker or len(taker) < 3:
        return None

    rows = taker[-points:] if len(taker) > points else taker

    # OKX формат: [ts, sellVol, buyVol]
    total_buy = 0.0
    total_sell = 0.0
    cvd_series = []
    cvd = 0.0
    for r in rows:
        try:
            sell = float(r[1])
            buy = float(r[2])
        except (IndexError, TypeError, ValueError):
            continue
        total_buy += buy
        total_sell += sell
        cvd += (buy - sell)
        cvd_series.append(cvd)

    if not cvd_series or len(cvd_series) < 3:
        return None

    cvd_change = cvd_series[-1] - cvd_series[0]
    total_vol = total_buy + total_sell

    # Цена за то же окно
    bar_map = {"1H": "1H", "4H": "4H", "1D": "1D"}
    candles = fetch_candles(f"{ccy}-USDT", bar_map.get(period, "1H"), points)
    time.sleep(REQUEST_DELAY)

    price_change_pct = None
    price_now = None
    if candles and len(candles) >= 2:
        try:
            first_close = float(candles[0][4])
            last_close = float(candles[-1][4])
            price_now = last_close
            if first_close > 0:
                price_change_pct = (last_close / first_close - 1) * 100
        except (IndexError, TypeError, ValueError):
            pass

    # Классификация
    signal, ru = classify(price_change_pct, cvd_change, total_vol)

    return {
        "period": period,
        "points": len(cvd_series),
        "price_now": price_now,
        "price_change_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
        "cvd_change": round(cvd_change, 2),
        "cvd_change_share_pct": round(cvd_change / total_vol * 100, 2) if total_vol else None,
        "total_buy": round(total_buy, 2),
        "total_sell": round(total_sell, 2),
        "buy_sell_ratio": round(total_buy / total_sell, 3) if total_sell else None,
        "signal": signal,
        "text_ru": ru,
    }


def classify(price_pct, cvd_change, total_vol):
    """
    Дивергенция или согласие. Порог по CVD берём в долях объёма,
    чтобы сравнивать разные токены между собой.
    """
    if price_pct is None:
        return "UNKNOWN", "нет данных о цене"

    cvd_share = (cvd_change / total_vol * 100) if total_vol else 0
    price_up = price_pct > PRICE_NOISE_PCT
    price_down = price_pct < -PRICE_NOISE_PCT
    cvd_up = cvd_share > CVD_NOISE_SHARE * 100
    cvd_down = cvd_share < -CVD_NOISE_SHARE * 100

    if price_up and cvd_down:
        return ("DISTRIBUTION_DIV",
                f"цена {price_pct:+.1f}%, а агрессия продающая ({cvd_share:+.1f}% объёма) — "
                f"розница тянет вверх, крупные разгружаются лимитками")

    if price_down and cvd_up:
        return ("ACCUMULATION_DIV",
                f"цена {price_pct:+.1f}%, а агрессия покупающая ({cvd_share:+.1f}% объёма) — "
                f"розница сдаёт, крупные подбирают")

    if price_up and cvd_up:
        return ("HEALTHY_UPTREND",
                f"цена {price_pct:+.1f}% и покупатели агрессивны ({cvd_share:+.1f}%) — "
                f"рост подтверждён деньгами")

    if price_down and cvd_down:
        return ("HEALTHY_DOWNTREND",
                f"цена {price_pct:+.1f}% и продавцы агрессивны ({cvd_share:+.1f}%) — "
                f"падение подтверждено")

    return ("NEUTRAL",
            f"цена {price_pct:+.1f}%, CVD {cvd_share:+.1f}% — без явного перевеса")


def consensus(windows):
    """
    Сводит три окна в один вывод.
    Дивергенция на 1D весит больше чем на 1H.
    """
    sigs = {w: (d or {}).get("signal") for w, d in windows.items()}
    weights = {"1H": 1, "4H": 2, "1D": 3}

    score = 0
    votes = []
    for w, s in sigs.items():
        if not s or s in ("UNKNOWN", "NEUTRAL"):
            continue
        wt = weights.get(w, 1)
        if s == "DISTRIBUTION_DIV":
            score -= wt * 2
            votes.append(f"{w}:DIST_DIV")
        elif s == "ACCUMULATION_DIV":
            score += wt * 2
            votes.append(f"{w}:ACC_DIV")
        elif s == "HEALTHY_UPTREND":
            score += wt
            votes.append(f"{w}:UP")
        elif s == "HEALTHY_DOWNTREND":
            score -= wt
            votes.append(f"{w}:DOWN")

    if score >= 4:
        code, ru = "STRONG_BUY_PRESSURE", "агрессия покупателей на нескольких окнах"
    elif score >= 2:
        code, ru = "BUY_PRESSURE", "лёгкий перевес покупателей"
    elif score <= -4:
        code, ru = "STRONG_SELL_PRESSURE", "агрессия продавцов на нескольких окнах"
    elif score <= -2:
        code, ru = "SELL_PRESSURE", "лёгкий перевес продавцов"
    else:
        code, ru = "MIXED", "окна не согласованы"

    return {"code": code, "score": score, "text_ru": ru, "votes": votes}


def main(only=None):
    print("=== CVD Multi Collector v1.0 (OKX public, 0 кредитов) ===\n")

    tokens = [t for t in TOKENS if not only or t in only]
    print(f"  Токенов: {len(tokens)} · окон: {len(WINDOWS)}\n")

    out_tokens = {}
    ok, failed = 0, 0
    divergences = []

    for ccy in tokens:
        windows = {}
        for wname, cfg in WINDOWS.items():
            windows[wname] = analyze_window(ccy, cfg["period"], cfg["points"])

        if not any(windows.values()):
            out_tokens[ccy] = {"symbol": ccy, "status": "NO_DATA"}
            failed += 1
            print(f"  {ccy:8} нет данных")
            continue

        cons = consensus(windows)
        out_tokens[ccy] = {
            "symbol": ccy,
            "status": "OK",
            "windows": windows,
            "consensus": cons,
        }
        ok += 1

        # собираем дивергенции для сводки
        for wname, w in windows.items():
            if w and w.get("signal") in ("DISTRIBUTION_DIV", "ACCUMULATION_DIV"):
                divergences.append({
                    "symbol": ccy, "window": wname,
                    "signal": w["signal"],
                    "price_change_pct": w.get("price_change_pct"),
                    "cvd_share_pct": w.get("cvd_change_share_pct"),
                })

        d1 = (windows.get("1D") or {}).get("signal", "—")
        print(f"  {ccy:8} {cons['code']:22} score {cons['score']:+3}  1D: {d1}")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "okx_taker_volume",
        "cost": "free · 0 credits",
        "windows": {k: v["label"] for k, v in WINDOWS.items()},
        "tokens_ok": ok,
        "tokens_failed": failed,
        "divergences": divergences,
        "tokens": out_tokens,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Готово: {ok} успешно, {failed} без данных")
    if divergences:
        print(f"\n  Найдено дивергенций: {len(divergences)}")
        for d in divergences[:8]:
            print(f"    {d['symbol']:8} {d['window']:3} {d['signal']:18} "
                  f"цена {d['price_change_pct']:+.1f}% CVD {d['cvd_share_pct']:+.1f}%")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    only = set(s.strip().upper() for s in a.token.split(",") if s.strip()) or None
    main(only)
