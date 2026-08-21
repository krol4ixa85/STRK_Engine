#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
volume_profile_collector.py · v2.1 · 21.08.2026
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
    "14d": {"days": 14, "interval": "2h", "bins": 45},
    "30d": {"days": 30, "interval": "4h", "bins": 50},
    "90d": {"days": 90, "interval": "1d", "bins": 50},
}

# Отдельное длинное окно ТОЛЬКО для поиска структуры выше цены.
# Когда актив на локальном максимуме, объёмных уровней сверху нет —
# но исторические свинг-хаи остаются магнитами: там стоят стопы шортов
# и лимитки тех, кто ждёт возврата к прошлым уровням.
# У LINK на 90д ничего сверху, а на 365д — $14.40, $15.00, $15.24.
STRUCTURE_WINDOW_DAYS = 365
STRUCTURE_INTERVAL = "1d"

# Сколько баров слева и справа должен превышать бар, чтобы считаться
# свинг-точкой. 5 отсекает мелкий шум, оставляя значимые развороты.
SWING_LOOKBACK = 5

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


def find_swings(candles, lookback=SWING_LOOKBACK):
    """
    Локальные максимумы и минимумы. Свинг-хай — бар, чей максимум выше
    lookback баров слева и справа. Там, где цена разворачивалась, обычно
    и стоят отложенные заявки: стопы шортов над хаями, стопы лонгов под лоу.
    """
    try:
        highs = [float(c["h"]) for c in candles]
        lows = [float(c["l"]) for c in candles]
        times = [int(c.get("t", 0)) for c in candles]
    except (KeyError, TypeError, ValueError):
        return [], []

    n = len(highs)
    sw_high, sw_low = [], []
    for i in range(lookback, n - lookback):
        w_h = highs[i - lookback:i + lookback + 1]
        w_l = lows[i - lookback:i + lookback + 1]
        if highs[i] == max(w_h):
            sw_high.append({"price": round(highs[i], 8),
                            "bars_ago": n - 1 - i,
                            "ts": times[i]})
        if lows[i] == min(w_l):
            sw_low.append({"price": round(lows[i], 8),
                           "bars_ago": n - 1 - i,
                           "ts": times[i]})
    return sw_high, sw_low


def measured_move(candles, current):
    """
    Проекция хода, когда цена вышла на новый максимум и уровней сверху нет.
    Берём высоту последнего диапазона консолидации и откладываем её вверх
    от точки выхода. Это оценка, а не уровень объёма — помечаем отдельно.
    """
    try:
        highs = [float(c["h"]) for c in candles]
        lows = [float(c["l"]) for c in candles]
    except (KeyError, TypeError, ValueError):
        return None
    if len(highs) < 30:
        return None

    # Диапазон предыдущих 30 баров до последних 10
    base = highs[-40:-10] if len(highs) >= 40 else highs[:-10]
    base_lo = lows[-40:-10] if len(lows) >= 40 else lows[:-10]
    if not base or not base_lo:
        return None

    rng_high, rng_low = max(base), min(base_lo)
    height = rng_high - rng_low
    if height <= 0 or rng_high <= 0:
        return None

    target = rng_high + height
    if target <= current:
        return None

    return {
        "price": round(target, 8),
        "distance_pct": round((target / current - 1) * 100, 2),
        "range_high": round(rng_high, 8),
        "range_low": round(rng_low, 8),
        "method": "measured move: высота диапазона отложена от верхней границы",
    }


MARKUP_VOL_RATIO = 1.3   # объём последних 3 дней относительно среднего по окну


def volume_expansion(candles, recent_n):
    """
    Во сколько раз объём последних recent_n свечей выше среднего по окну.

    Нужно, чтобы отличить выход вверх ИЗ зоны стоимости на растущем
    объёме (акцептанс, markup) от простого отрыва цены на тонком
    объёме (растяжение, ждём возврата). Без этого различения любая
    цена выше VAH читалась как «дорого» — и на пробое AAVE 21.08
    движок штрафовал ровно то движение, которое и было markup.
    """
    if not candles or len(candles) < recent_n * 2:
        return None
    try:
        vols = [float(c.get("v") or 0) for c in candles]
    except (TypeError, ValueError):
        return None
    if not vols:
        return None
    avg_all = sum(vols) / len(vols)
    if avg_all <= 0:
        return None
    avg_recent = sum(vols[-recent_n:]) / recent_n
    return round(avg_recent / avg_all, 2)


def classify_position(current, levels, vol_ratio=None):
    """
    Где цена относительно value area — читаемо.

    ВАЖНО про ABOVE_VALUE. Код остаётся прежним (его знают потребители),
    но добавлено поле above_kind:

      MARKUP    цена вышла вверх на объёме выше обычного — рынок
                принимает новую цену. Это НЕ повод штрафовать вход.
      EXTENDED  цена ушла вверх, а объём не подтверждает — растяжение,
                вероятен возврат в зону. Здесь штраф уместен.
      None      объём посчитать не удалось → не утверждаем ничего.
    """
    poc, vah, val = levels["poc"], levels["vah"], levels["val"]
    dist_poc = (current / poc - 1) * 100 if poc else None
    above_kind = None

    if current > vah:
        code = "ABOVE_VALUE"
        if vol_ratio is None:
            above_kind = None
            tail = "объём не посчитан — характер выхода неизвестен"
        elif vol_ratio >= MARKUP_VOL_RATIO:
            above_kind = "MARKUP"
            tail = (f"выход на объёме ×{vol_ratio} к среднему — "
                    f"рынок принимает новую цену")
        else:
            above_kind = "EXTENDED"
            tail = (f"объём ×{vol_ratio} к среднему, движение не подтверждено — "
                    f"вероятен возврат в зону")
        ru = f"цена выше зоны стоимости на {(current/vah-1)*100:.1f}% — {tail}"
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
        "above_kind": above_kind,
        "vol_ratio_recent": vol_ratio,
        "text_ru": ru,
        "distance_to_poc_pct": round(dist_poc, 2) if dist_poc is not None else None,
    }


def build_targets(current, all_windows, structure=None):
    """
    Собирает магниты вверх и вниз.

    Два разных типа уровней, и путать их нельзя:
      объёмные (POC/VAH/VAL/HVN) — где реально торговали, там цена вязнет
      структурные (свинг-хаи/лоу)  — где разворачивались, там стоят стопы

    Когда цена на локальном максимуме, объёмных уровней сверху не бывает
    по определению. Тогда работают только структурные с длинного окна
    и проекция хода.
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

    # Структурные уровни с длинного окна — главный источник целей вверх,
    # когда цена вышла выше всего торгованного объёма
    if structure:
        for sh in structure.get("swing_highs", []):
            price = sh["price"]
            dist = (price / current - 1) * 100
            if dist > 0.5:
                ups.append({
                    "price": price,
                    "distance_pct": round(dist, 2),
                    "label": f"свинг-хай {sh['bars_ago']}д назад",
                    "kind": "SWING_HIGH",
                    "window": "365d",
                    "bars_ago": sh["bars_ago"],
                })
        for sl in structure.get("swing_lows", []):
            price = sl["price"]
            dist = (price / current - 1) * 100
            if dist < -0.5:
                downs.append({
                    "price": price,
                    "distance_pct": round(dist, 2),
                    "label": f"свинг-лоу {sl['bars_ago']}д назад",
                    "kind": "SWING_LOW",
                    "window": "365d",
                    "bars_ago": sl["bars_ago"],
                })

        mm = structure.get("measured_move")
        if mm:
            ups.append({
                "price": mm["price"],
                "distance_pct": mm["distance_pct"],
                "label": "проекция хода",
                "kind": "MEASURED_MOVE",
                "window": "projection",
                "note": mm["method"],
            })

    # Убираем близкие дубли: уровни ближе 1% друг к другу — это один уровень
    def dedupe(levels):
        out = []
        for lv in levels:
            if not any(abs(lv["price"] / o["price"] - 1) < 0.01 for o in out):
                out.append(lv)
        return out

    ups.sort(key=lambda x: x["distance_pct"])
    downs.sort(key=lambda x: -x["distance_pct"])
    ups, downs = dedupe(ups), dedupe(downs)

    return {
        "nearest_up": ups[:5],
        "nearest_down": downs[:5],
        "has_volume_targets_up": any(u["kind"] in ("POC", "VAH", "VAL", "HVN")
                                     for u in ups),
    }


def analyze_token(symbol, hl_name):
    result = {"symbol": symbol, "hl_name": hl_name, "windows": {}}
    current = None
    # None означает «не посчитали». Ноль здесь был бы утверждением
    # «объёма нет», а это разные вещи.
    vol_ratio_30d = None
    vol_ratio_90d = None

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

        if wname == "30d":
            # 4h-свечи: последние 3 дня = 18 свечей. Нужен для того,
            # чтобы отличить markup от растяжения (см. classify_position)
            vol_ratio_30d = volume_expansion(candles, 18)
        elif wname == "90d":
            # 1d-свечи: последние 3 дня = 3 свечи
            vol_ratio_90d = volume_expansion(candles, 3)

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
        result["position"] = classify_position(current, w30["levels"], vol_ratio_30d)
    w90 = result["windows"].get("90d")
    if w90 and w90.get("levels"):
        result["position_90d"] = classify_position(current, w90["levels"], vol_ratio_90d)

    # Длинное окно только для структуры — объёмный профиль на нём не строим,
    # он был бы слишком размазан и бесполезен для свинга
    struct_candles = fetch_candles(hl_name, STRUCTURE_INTERVAL, STRUCTURE_WINDOW_DAYS)
    time.sleep(REQUEST_DELAY)
    structure = None
    if struct_candles and len(struct_candles) > SWING_LOOKBACK * 2 + 1:
        sw_high, sw_low = find_swings(struct_candles)
        # оставляем только релевантные: выше цены для хаёв, ниже для лоу
        sw_high = sorted([h for h in sw_high if h["price"] > current * 1.005],
                         key=lambda x: x["price"])[:5]
        sw_low = sorted([l for l in sw_low if l["price"] < current * 0.995],
                        key=lambda x: -x["price"])[:5]
        structure = {
            "window_days": STRUCTURE_WINDOW_DAYS,
            "candles": len(struct_candles),
            "swing_highs": sw_high,
            "swing_lows": sw_low,
            "period_high": round(max(float(c["h"]) for c in struct_candles), 8),
            "period_low": round(min(float(c["l"]) for c in struct_candles), 8),
        }
        # Проекция нужна только если сверху пусто
        if not sw_high:
            mm = measured_move(struct_candles, current)
            if mm:
                structure["measured_move"] = mm
        result["structure"] = structure

    result["targets"] = build_targets(current, result["windows"], structure)
    return result


def main(only=None):
    print("=== Volume Profile Collector v2.1 (HL candles, 0 кредитов) ===\n")

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
