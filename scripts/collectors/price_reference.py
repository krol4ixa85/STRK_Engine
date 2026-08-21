#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
price_reference.py · v1.0 · 21.08.2026
STRK ENGINE · единый источник цен

ЗАЧЕМ
-----
Dune хорош для потоков и плох для цен. Он выводит цену из отдельных
DEX-сделок как amount_usd / amount_token, и когда попадается сделка
с неверными decimals или пустой пул, получается мусор.

Наблюдённое 21.08.2026:

  ARB   now=0.089463  7d=0.00000521   → +1 717 040%   отношение 17171
  UNI   now=2316.32   7d=3.2          → +72 285%      цена UNI ~$4
  DOGE  now=0.07987   7d=0.000759     → +10 423%      отношение 105
  BONK  7d=0                          → деление на ноль
  TAO   обе цены None
  WIF   momentum норм, битый price_30d_ago в token_scan

Множители разные (17171, 724, 105) — это не единицы измерения,
а случайный мусор из отдельных сделок. Чинить такие цены нельзя,
их надо заменить.

РЕШЕНИЕ · РАЗДЕЛИТЬ ИСТОЧНИКИ
-----------------------------
  Dune            → потоки капитала (netflow, buy/sell volume)
  Hyperliquid     → цены и их история
  Hive/CoinGecko  → сверка и токены, которых нет на HL

Свечи HL — настоящие рыночные цены с биржи, а не производная от
DEX-сделок. Проверено: все шесть проблемных токенов отдают адекватные
значения (+15..+32% за неделю вместо миллионов процентов).

ЧТО СЧИТАЕТ
-----------
  price_now, price_24h_ago, price_7d_ago, price_30d_ago, price_90d_ago
  change_24h_pct, change_7d_pct, change_30d_pct, change_90d_pct
  high_90d, low_90d
  source · откуда взята цена
  sanity · прошла ли проверку правдоподобности

САМОПРОВЕРКА
------------
Если у токена есть и HL, и Hive-цена, они сравниваются. Расхождение
больше 5% помечается: значит один из источников врёт, и доверять
нельзя ни одному без разбора.

ЗАПУСК
------
  python3 scripts/collectors/price_reference.py
  python3 scripts/collectors/price_reference.py --token ARB,UNI

ВЫХОД
-----
  data/cache/price_reference.json
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "price_reference.json")
HL_ENDPOINT = "https://api.hyperliquid.xyz/info"

# Как токен называется на HL. Префикс k означает контракт на 1000 единиц —
# цену такого контракта делим на 1000, чтобы получить цену токена.
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

# Токены без контракта на HL — только Hive/CoinGecko
NO_HL = ["AKT", "CFG", "GRT", "RPL", "SSV"]

# Расхождение между источниками, после которого цене нельзя доверять
SOURCE_DISAGREE_PCT = 5.0

# Изменение за неделю, которого не бывает у ликвидных активов
IMPLAUSIBLE_WEEKLY_PCT = 300.0

REQUEST_DELAY = 0.12


def load_json(name, default=None):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fetch_candles(hl_name, days=95):
    now_ms = int(time.time() * 1000)
    try:
        r = requests.post(
            HL_ENDPOINT,
            json={"type": "candleSnapshot", "req": {
                "coin": hl_name, "interval": "1d",
                "startTime": now_ms - days * 86400 * 1000,
                "endTime": now_ms,
            }},
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            return None
        d = r.json()
        return d if isinstance(d, list) and d else None
    except Exception:
        return None


def price_from_candles(candles, divisor=1):
    """Достаёт опорные цены. divisor для k-контрактов (1000 единиц)."""
    try:
        closes = [float(c["c"]) / divisor for c in candles]
        highs = [float(c["h"]) / divisor for c in candles]
        lows = [float(c["l"]) / divisor for c in candles]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None

    n = len(closes)
    if n < 2:
        return None

    def back(days):
        idx = n - 1 - days
        return closes[idx] if idx >= 0 else None

    now = closes[-1]
    out = {
        "price_now": now,
        "price_24h_ago": back(1),
        "price_7d_ago": back(7),
        "price_30d_ago": back(30),
        "price_90d_ago": back(90),
        "high_90d": max(highs),
        "low_90d": min(lows),
        "candles": n,
    }

    for label, days in (("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90)):
        prev = out.get(f"price_{label}_ago")
        out[f"change_{label}_pct"] = (
            round((now / prev - 1) * 100, 2) if prev and prev > 0 else None
        )

    return out


def check_sanity(row, hive_price):
    """
    Две проверки: правдоподобность движения и согласие источников.
    Обе нужны — цена может быть правдоподобной и при этом неверной.
    """
    flags = []

    ch7 = row.get("change_7d_pct")
    if ch7 is not None and abs(ch7) > IMPLAUSIBLE_WEEKLY_PCT:
        flags.append({
            "check": "weekly_move",
            "value": ch7,
            "reason": f"изменение {ch7:.0f}% за неделю — для ликвидного актива не бывает",
        })

    if hive_price and row.get("price_now"):
        diff = abs(row["price_now"] / hive_price - 1) * 100
        if diff > SOURCE_DISAGREE_PCT:
            flags.append({
                "check": "source_agreement",
                "value": round(diff, 2),
                "reason": (f"HL даёт ${row['price_now']:.8f}, Hive ${hive_price:.8f} — "
                           f"расхождение {diff:.1f}%, доверять нельзя ни одному"),
            })

    return {"ok": not flags, "flags": flags}


def main(only=None):
    print("=== Price Reference v1.0 (HL candles, 0 кредитов) ===\n")

    hive = (load_json("hive_prices.json", {}) or {}).get("prices", {})

    tokens = {k: v for k, v in TOKEN_MAP.items() if not only or k in only}
    out_tokens = {}
    ok, failed, suspicious = 0, 0, 0

    for symbol, hl_name in sorted(tokens.items()):
        divisor = 1000 if hl_name.startswith("k") else 1
        candles = fetch_candles(hl_name)
        time.sleep(REQUEST_DELAY)

        if not candles:
            out_tokens[symbol] = {"symbol": symbol, "status": "NO_DATA",
                                  "hl_name": hl_name}
            failed += 1
            print(f"  {symbol:8} нет свечей на HL")
            continue

        row = price_from_candles(candles, divisor)
        if not row:
            out_tokens[symbol] = {"symbol": symbol, "status": "BAD_CANDLES"}
            failed += 1
            continue

        hive_p = (hive.get(symbol) or {}).get("price_usd")
        sanity = check_sanity(row, hive_p)

        entry = {
            "symbol": symbol,
            "status": "OK" if sanity["ok"] else "SUSPICIOUS",
            "hl_name": hl_name,
            "source": "hyperliquid_candles",
            "hive_price_usd": hive_p,
            "sanity": sanity,
            **row,
        }
        out_tokens[symbol] = entry

        if sanity["ok"]:
            ok += 1
        else:
            suspicious += 1

        p = row["price_now"]
        p_s = f"{p:.8f}" if p < 0.001 else (f"{p:.6f}" if p < 1 else f"{p:.2f}")
        ch7 = row.get("change_7d_pct")
        ch7_s = f"{ch7:+.1f}%" if ch7 is not None else "—"
        ch30 = row.get("change_30d_pct")
        ch30_s = f"{ch30:+.1f}%" if ch30 is not None else "—"
        flag = "" if sanity["ok"] else "  ⚠"
        print(f"  {symbol:8} ${p_s:>14}  7д {ch7_s:>8}  30д {ch30_s:>8}{flag}")

    # Токены без HL — отмечаем явно, чтобы не искать причину потом
    for symbol in NO_HL:
        if only and symbol not in only:
            continue
        hive_p = (hive.get(symbol) or {}).get("price_usd")
        out_tokens[symbol] = {
            "symbol": symbol,
            "status": "OK" if hive_p else "NO_DATA",
            "source": "hive_prices" if hive_p else None,
            "price_now": hive_p,
            "note": "контракта на Hyperliquid нет, история цен недоступна",
            "sanity": {"ok": bool(hive_p), "flags": []},
        }
        if hive_p:
            ok += 1
            print(f"  {symbol:8} ${hive_p:>14.6f}  только текущая цена (Hive)")

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source_priority": ["hyperliquid_candles", "hive_prices"],
        "cost": "free · 0 credits",
        "why": ("Dune выводит цену из отдельных DEX-сделок и даёт мусор "
                "при неверных decimals. Здесь цены берутся с биржи."),
        "tokens_ok": ok,
        "tokens_suspicious": suspicious,
        "tokens_failed": failed,
        "checks": {
            "implausible_weekly_pct": IMPLAUSIBLE_WEEKLY_PCT,
            "source_disagree_pct": SOURCE_DISAGREE_PCT,
        },
        "tokens": out_tokens,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Готово: {ok} корректных · {suspicious} подозрительных · {failed} без данных")
    if suspicious:
        print("  Подозрительные:")
        for t, r in out_tokens.items():
            if r.get("status") == "SUSPICIOUS":
                for fl in r["sanity"]["flags"]:
                    print(f"    {t}: {fl['reason']}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    only = set(s.strip().upper() for s in a.token.split(",") if s.strip()) or None
    main(only)
