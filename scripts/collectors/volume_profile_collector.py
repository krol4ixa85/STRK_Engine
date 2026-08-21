#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
volume_profile_collector.py · v1.0 · 21.08.2026
STRK ENGINE · Volume Profile и магниты цены

ЗАЧЕМ
-----
Фаза и поток говорят НАПРАВЛЕНИЕ. Они не говорят КУДА ЦЕНА ДОЙДЁТ.
Без целей совет "заходи 46%" неполон: непонятно где брать профит,
где ставить стоп, и насколько текущая цена вообще адекватна.

Volume Profile отвечает на это через объём: цена тянется туда, где
реально торговали. Уровни с большим накопленным объёмом работают как
магниты — их проходят медленно, от них отскакивают.

ЧТО СЧИТАЕМ
-----------
Для каждого токена на трёх окнах (7д / 30д / 90д):

  POC   Point of Control — цена с максимальным объёмом. Справедливая
        цена по мнению рынка. Магнит номер один.
  VAH   Value Area High — верх зоны, где прошло 70% объёма
  VAL   Value Area Low — низ той же зоны
  HVN   High Volume Nodes — топ-5 уровней объёма. Дополнительные магниты.
  LVN   Low Volume Nodes — провалы объёма. Цена проходит их БЫСТРО,
        поэтому это плохие места для лимитных заявок и хорошие
        для стопов (не заденет случайно).

ЧИТАЕТСЯ ТАК
------------
  цена выше VAH  → перекуплена относительно объёма, поддержки сверху нет
  цена ниже VAL  → недооценена, но может быть в нисходящем тренде
  цена внутри VA → в равновесии, ждём выхода за границу

  Расстояние до POC = насколько цена оторвалась от справедливой.
  Больше 20% в любую сторону = натянутая струна.

ИСТОЧНИК ДАННЫХ · БЕСПЛАТНО
---------------------------
Hyperliquid candleSnapshot — публичный endpoint, ноль ключей.
Отдаёт до 5000 свечей за запрос, нам нужно 90 дневных.

Изначально планировал через Dune dex.trades (~800 кредитов на 32 токена
раз в неделю), но HL отдаёт то же самое бесплатно и чаще. Dune остаётся
для STRK, которого на HL нет в достаточной ликвидности.

ЗАПУСК
------
  python3 scripts/collectors/volume_profile_collector.py
  python3 scripts/collectors/volume_profile_collector.py --token LINK,STRK

ВЫХОД
-----
  data/cache/volume_profile.json
"""

import os
import sys
import json
import time
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "volume_profile.json")

HL_ENDPOINT = "https://api.hyperliquid.xyz/info"

# Токены и их имена на HL (те же что в hl_perps_collector)
TOKEN_MAP = {
    "BTC": "BTC", "ETH": "ETH", "SOL": "SOL",
    "STRK": "STRK", "LINK": "LINK", "ETHFI": "ETHFI",
    "MORPHO": "MORPHO", "ONDO": "ONDO",
    "ARB": "ARB", "OP": "OP", "MNT": "MNT", "ZK": "ZK",
    "AAVE": "AAVE", "PENDLE": "PENDLE", "LDO": "LDO",
    "CRV": "CRV", "COMP": "COMP", "SNX": "SNX",
    "DYDX": "DYDX", "GMX": "GMX", "UNI": "UNI",
    "FXS": "FXS", "ENA": "ENA",
    "EIGEN": "EIGEN", "JTO": "JTO",
    "TAO": "TAO", "FET": "FET", "RNDR": "RNDR",
    "AIXBT": "AIXBT", "VIRTUAL": "VIRTUAL",
    "TIA": "TIA", "SEI": "SEI", "SUI": "SUI", "APT": "APT",
    "INJ": "INJ",
    "BONK": "kBONK", "PEPE": "kPEPE", "DOGE": "DOGE", "WIF": "WIF",
    "AXS": "AXS", "SAND": "SAND", "FIL": "FIL",
}

# Окна анализа: локальное / среднесрочное / фундаментальное
WINDOWS = {
    "7d":  {"days": 7,  "interval": "1h", "bins": 40},
    "30d": {"days": 30, "interval": "4h", "bins": 50},
    "90d": {"days": 90, "interval": "1d", "bins": 50},
}

# Доля объёма для Value Area. 70% — стандарт из классического
# Market Profile (Steidlmayer), примерно одно стандартное отклонение.
VALUE_AREA_PCT = 0.70

# Пауза между запросами чтобы не долбить API
REQUEST_DELAY = 0.15


def fetch_candles(hl_name, interval, days):
    """Забирает свечи с HL за нужный период."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    try:
        r = requests.post(
            HL_ENDPOINT,
            json={"type": "candleSnapshot", "req": {
                "coin": hl_name, "interval": interval,
                "startTime": start_ms, "endTime": now_ms,
            }},
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) and data else None
    except Exception:
        return None


def build_profile(candles, bins):
    """
    Строит распределение объёма по ценам.

    Каждая свеча имеет диапазон low..high и объём v. Распределяем объём
    равномерно по бинам, которые свеча покрывает. Это стандартное
    приближение: точное распределение внутри свечи неизвестно, но при
    достаточном числе свечей ошибка усредняется.
    """
    prices = []
    for c in candles:
        try:
            prices += [float(c["l"]), float(c["h"])]
        except (KeyError, TypeError, ValueError):
            continue
    if not prices:
        return None

    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return None

    step = (hi - lo) / bins
    vol_at = defaultdict(float)

    for c in candles:
        try:
            l, h, v = float(c["l"]), float(c["h"]), float(c["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if v <= 0:
            continue
        if h <= l:
            idx = min(bins - 1, max(0, int((l - lo) / step)))
            vol_at[idx] += v
            continue
        n = max(1, int((h - l) / step))
        per = v / n
        for i in range(n):
            px = l + i * step
            idx = min(bins - 1, max(0, int((px - lo) / step)))
            vol_at[idx] += per

    if not vol_at:
        return None

    return {"lo": lo, "hi": hi, "step": step, "vol_at": dict(vol_at), "bins": bins}


def extract_levels(profile):
    """Из распределения объёма вытаскивает POC, VAH, VAL, HVN, LVN."""
    lo, step, bins = profile["lo"], profile["step"], profile["bins"]
    vol_at = profile["vol_at"]

    def bin_price(b):
        return lo + b * step + step / 2

    # POC — бин с максимальным объёмом
    poc_bin = max(vol_at, key=vol_at.get)
    poc = bin_price(poc_bin)

    # Value Area: расширяемся от POC пока не наберём VALUE_AREA_PCT объёма
    total = sum(vol_at.values())
    target = total * VALUE_AREA_PCT
    acc = vol_at[poc_bin]
    lo_b = hi_b = poc_bin

    while acc < target:
        down = vol_at.get(lo_b - 1, 0) if lo_b > 0 else -1
        up = vol_at.get(hi_b + 1, 0) if hi_b < bins - 1 else -1
        if down < 0 and up < 0:
            break
        if up >= down:
            hi_b += 1
            acc += vol_at.get(hi_b, 0)
        else:
            lo_b -= 1
            acc += vol_at.get(lo_b, 0)

    val = lo + lo_b * step
    vah = lo + (hi_b + 1) * step

    # HVN — топ-5 узлов объёма (магниты)
    srt = sorted(vol_at.items(), key=lambda x: -x[1])
    hvn = [{"price": round(bin_price(b), 6),
            "volume": round(v),
            "share_pct": round(v / total * 100, 2)}
           for b, v in srt[:5]]

    # LVN — провалы объёма ВНУТРИ диапазона (цена проходит их быстро).
    # Берём только бины с ненулевым объёмом чтобы не ловить пустоту
    # за пределами торговли.
    nonzero = {b: v for b, v in vol_at.items() if v > 0}
    if len(nonzero) > 10:
        srt_low = sorted(nonzero.items(), key=lambda x: x[1])
        lvn = [{"price": round(bin_price(b), 6),
                "volume": round(v),
                "share_pct": round(v / total * 100, 3)}
               for b, v in srt_low[:3]]
    else:
        lvn = []

    return {
        "poc": round(poc, 6),
        "vah": round(vah, 6),
        "val": round(val, 6),
        "range_low": round(lo, 6),
        "range_high": round(profile["hi"], 6),
        "total_volume": round(total),
        "hvn": hvn,
        "lvn": lvn,
    }


def classify_position(current, levels):
    """Где цена относительно value area — читаемо."""
    poc, vah, val = levels["poc"], levels["vah"], levels["val"]
    dist_poc = (current / poc - 1) * 100 if poc else None

    if current > vah:
        code = "ABOVE_VALUE"
        ru = (f"цена выше зоны стоимости на {(current/vah-1)*100:.1f}% — "
              f"объёмной поддержки сверху нет")
    elif current < val:
        code = "BELOW_VALUE"
        ru = (f"цена ниже зоны стоимости на {(1-current/val)*100:.1f}% — "
              f"либо распродажа, либо недооценка")
    else:
        code = "INSIDE_VALUE"
        pos = (current - val) / (vah - val) * 100 if vah > val else 50
        ru = f"цена внутри зоны стоимости, в {pos:.0f}% от нижней границы"

    return {
        "code": code,
        "text_ru": ru,
        "distance_to_poc_pct": round(dist_poc, 2) if dist_poc is not None else None,
    }


def build_targets(current, all_windows):
    """
    Собирает список магнитов вверх и вниз из всех окон.
    Это и есть ответ на вопрос "куда стремится цена".
    """
    ups, downs = [], []

    for wname, w in all_windows.items():
        if not w or "levels" not in w:
            continue
        lv = w["levels"]
        for label, price in (("POC", lv["poc"]), ("VAH", lv["vah"]), ("VAL", lv["val"])):
            if not price:
                continue
            dist = (price / current - 1) * 100
            entry = {
                "price": price,
                "distance_pct": round(dist, 2),
                "label": f"{label} {wname}",
                "kind": label,
                "window": wname,
            }
            if dist > 0.5:
                ups.append(entry)
            elif dist < -0.5:
                downs.append(entry)

        # HVN тоже магниты
        for h in lv.get("hvn", [])[:3]:
            price = h["price"]
            dist = (price / current - 1) * 100
            entry = {
                "price": price,
                "distance_pct": round(dist, 2),
                "label": f"HVN {wname}",
                "kind": "HVN",
                "window": wname,
                "volume_share_pct": h["share_pct"],
            }
            if dist > 0.5:
                ups.append(entry)
            elif dist < -0.5:
                downs.append(entry)

    ups.sort(key=lambda x: x["distance_pct"])
    downs.sort(key=lambda x: -x["distance_pct"])

    return {
        "nearest_up": ups[:4],
        "nearest_down": downs[:4],
    }


def analyze_token(symbol, hl_name):
    result = {"symbol": symbol, "hl_name": hl_name, "windows": {}}
    current = None

    for wname, cfg in WINDOWS.items():
        candles = fetch_candles(hl_name, cfg["interval"], cfg["days"])
        time.sleep(REQUEST_DELAY)
        if not candles:
            result["windows"][wname] = None
            continue

        if current is None:
            try:
                current = float(candles[-1]["c"])
            except (KeyError, TypeError, ValueError):
                pass

        profile = build_profile(candles, cfg["bins"])
        if not profile:
            result["windows"][wname] = None
            continue

        levels = extract_levels(profile)
        result["windows"][wname] = {
            "candles": len(candles),
            "interval": cfg["interval"],
            "levels": levels,
        }

    if current is None:
        result["status"] = "NO_DATA"
        return result

    result["status"] = "OK"
    result["current_price"] = current

    # Положение относительно 30d value area — основное окно для свинга
    w30 = result["windows"].get("30d")
    if w30 and w30.get("levels"):
        result["position"] = classify_position(current, w30["levels"])
    w90 = result["windows"].get("90d")
    if w90 and w90.get("levels"):
        result["position_90d"] = classify_position(current, w90["levels"])

    result["targets"] = build_targets(current, result["windows"])
    return result


def main(only=None):
    print("=== Volume Profile Collector v1.0 (HL candles, 0 кредитов) ===\n")

    tokens = {k: v for k, v in TOKEN_MAP.items() if not only or k in only}
    print(f"  Токенов: {len(tokens)} · окон на токен: {len(WINDOWS)}\n")

    out_tokens = {}
    ok, failed = 0, 0

    for symbol, hl_name in tokens.items():
        r = analyze_token(symbol, hl_name)
        out_tokens[symbol] = r

        if r.get("status") == "OK":
            ok += 1
            cur = r["current_price"]
            w30 = (r["windows"].get("30d") or {}).get("levels") or {}
            pos = (r.get("position") or {}).get("code", "?")
            poc = w30.get("poc")
            if poc:
                d = (cur / poc - 1) * 100
                nearest_up = (r["targets"]["nearest_up"] or [{}])[0].get("distance_pct")
                nearest_dn = (r["targets"]["nearest_down"] or [{}])[0].get("distance_pct")
                up_s = f"↑{nearest_up:+.1f}%" if nearest_up is not None else "↑—"
                dn_s = f"↓{nearest_dn:+.1f}%" if nearest_dn is not None else "↓—"
                print(f"  {symbol:8} ${cur:>10.4f}  POC30 ${poc:>10.4f} ({d:+6.1f}%)  "
                      f"{pos:14} {up_s:>8} {dn_s:>8}")
        else:
            failed += 1
            print(f"  {symbol:8} нет данных")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "hyperliquid_candles",
        "cost": "free · 0 credits",
        "value_area_pct": VALUE_AREA_PCT * 100,
        "windows": {k: {"days": v["days"], "interval": v["interval"]}
                    for k, v in WINDOWS.items()},
        "tokens_ok": ok,
        "tokens_failed": failed,
        "tokens": out_tokens,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Готово: {ok} успешно, {failed} без данных")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    only = set(s.strip().upper() for s in a.token.split(",") if s.strip()) or None
    main(only)
