#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hive_price_collector.py · v3.0 · 20.08.2026
STRK ENGINE · цены через Hive Intelligence с батч-запросом

ЧТО БЫЛО НЕ ТАК В v1/v2
-----------------------
Логи прогона 20.08.2026 18:00 UTC показали три отдельных дефекта:

1. Перебор имён инструментов вслепую. На каждый токен пробовались 5 вариантов:
   get_simple_price · get_coin_data · coingecko_price · get_coin_market_data
   → все четыре TOOL_NOT_FOUND. В каталоге Hive их нет и никогда не было.

2. Единственный существующий инструмент — get_price — вызывался БЕЗ
   обязательного поля vs_currencies → 422 VALIDATION_ERROR на каждом токене.
   То есть Hive работал, ключ был валиден, а запрос был неполный.

3. 5 вариантов × 15 токенов = 75 запросов за ~30 секунд.
   Лимит Free Demo — 30 запросов/минуту. С четвёртого токена всё улетело
   в 429. Фолбэк на CoinGecko срабатывал в том же темпе и тоже получил 429.
   Итог: Hive 0/15, CoinGecko 5/15, десять токенов без цены вообще.

ЧТО ИЗМЕНИЛОСЬ В v3
-------------------
get_price принимает ids через запятую. Значит все 15 токенов берутся
ОДНИМ вызовом:

  было:  75 запросов, 0 успехов
  стало:  1 запрос,  15 цен,  1 кредит

Фолбэк на CoinGecko — тоже один батч-запрос вместо пятнадцати.
Максимум за прогон: 2 сетевых запроса. 429 стал структурно невозможен.

СТОИМОСТЬ
---------
1 кредит за прогон × 4 прогона в сутки = ~120 кредитов/мес.
Бюджет Free Demo — 10 000. Это 1,2%.

ЗАПУСК
------
  export HIVE_API_KEY=hive_live_...
  python3 hive/hive_price_collector.py

ВЫХОД
-----
  data/cache/hive_prices.json   (схема совместима с v1 — дашборд не трогаем)
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

HIVE_BASE = os.getenv("HIVE_API_URL", "https://mcp.hiveintelligence.xyz").rstrip("/")
EXECUTE_URL = f"{HIVE_BASE}/api/v1/execute"

CACHE_DIR = "data/cache"
OUT_FILE = os.path.join(CACHE_DIR, "hive_prices.json")

# symbol -> coingecko id
TOKENS = {
    "STRK": "starknet",
    "LINK": "chainlink",
    "ETHFI": "ether-fi",
    "MORPHO": "morpho",
    "ONDO": "ondo-finance",
    "ARB": "arbitrum",
    "OP": "optimism",
    "AAVE": "aave",
    "PENDLE": "pendle",
    "LDO": "lido-dao",
    "EIGEN": "eigenlayer",
    "CFG": "centrifuge",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

# Расхождение цен между источниками, при котором стоит насторожиться
DISCREPANCY_PCT = 2.0


def _normalise(cg_id_map, raw, source):
    """CoinGecko-формат {id: {usd, usd_24h_change, ...}} → наша схема."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for symbol, cg_id in cg_id_map.items():
        row = raw.get(cg_id)
        if not isinstance(row, dict):
            continue
        price = row.get("usd")
        if price is None:
            continue
        out[symbol] = {
            "symbol": symbol,
            "price_usd": price,
            "change_24h_pct": row.get("usd_24h_change"),
            "market_cap": row.get("usd_market_cap"),
            "volume_24h": row.get("usd_24h_vol"),
            "source": source,
        }
    return out


def fetch_hive_batch(api_key):
    """Один вызов get_price на все токены. Возвращает (prices, error)."""
    if not api_key:
        return {}, "no_api_key"

    args = {
        "ids": ",".join(TOKENS.values()),
        "vs_currencies": "usd",          # ← обязательное поле, его и не хватало
        "include_24hr_change": True,
        "include_24hr_vol": True,
        "include_market_cap": True,
    }

    try:
        r = requests.post(
            EXECUTE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"tool": "get_price", "args": args},
            timeout=45,
        )
    except requests.RequestException as e:
        return {}, f"network: {e}"

    if r.status_code == 401:
        return {}, "auth: ключ отсутствует или отозван"
    if r.status_code == 402:
        return {}, "payment: кредиты Hive исчерпаны"
    if r.status_code == 429:
        return {}, "rate_limit"

    try:
        body = r.json()
    except ValueError:
        return {}, f"bad json (http {r.status_code})"

    if not body.get("ok", True):
        err = body.get("error", {})
        return {}, f"{err.get('code', 'error')}: {err.get('message', '')[:160]}"

    data = body.get("data", body)
    # Hive может отдать данные как есть либо завернуть в data/result
    for key in ("data", "result", "prices"):
        if isinstance(data, dict) and key in data and isinstance(data[key], dict):
            data = data[key]
            break

    prices = _normalise(TOKENS, data, "hive")
    if not prices:
        return {}, f"пустой ответ (ключи: {list(data)[:6] if isinstance(data, dict) else type(data).__name__})"
    return prices, None


def fetch_coingecko_batch():
    """Один батч-запрос к бесплатному CoinGecko. Возвращает (prices, error)."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ",".join(TOKENS.values()),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
                "include_market_cap": "true",
            },
            timeout=30,
        )
    except requests.RequestException as e:
        return {}, f"network: {e}"

    if r.status_code == 429:
        return {}, "rate_limit"
    if r.status_code != 200:
        return {}, f"http {r.status_code}"

    try:
        return _normalise(TOKENS, r.json(), "coingecko_direct"), None
    except ValueError:
        return {}, "bad json"


def compare(hive_prices, cg_prices):
    """Расхождения между источниками — сигнал, что один из них врёт."""
    out = []
    for symbol in set(hive_prices) & set(cg_prices):
        h = hive_prices[symbol].get("price_usd")
        c = cg_prices[symbol].get("price_usd")
        if not h or not c:
            continue
        diff = abs(h - c) / c * 100
        if diff >= DISCREPANCY_PCT:
            out.append({
                "symbol": symbol,
                "hive_price": h,
                "coingecko_price": c,
                "diff_pct": round(diff, 2),
            })
    return sorted(out, key=lambda x: -x["diff_pct"])


def main():
    print("=== Hive Price Collector v3 · батч-режим ===\n")

    api_key = os.getenv("HIVE_API_KEY", "").strip()
    if api_key:
        print(f"✓ HIVE_API_KEY загружен ({len(api_key)} символов)")
    else:
        print("⚠ HIVE_API_KEY не задан — работаем только на CoinGecko")

    print(f"  Токенов в запросе: {len(TOKENS)}")
    print(f"  Сетевых запросов за прогон: максимум 2\n")

    # 1 · Hive · один вызов, один кредит
    hive_prices, hive_err = fetch_hive_batch(api_key)
    if hive_err:
        print(f"  Hive: ✗ {hive_err}")
    else:
        print(f"  Hive: ✓ {len(hive_prices)}/{len(TOKENS)} цен · 1 кредит")

    time.sleep(2)  # вежливая пауза между источниками

    # 2 · CoinGecko · бесплатно, служит и фолбэком, и сверкой
    cg_prices, cg_err = fetch_coingecko_batch()
    if cg_err:
        print(f"  CoinGecko: ✗ {cg_err}")
    else:
        print(f"  CoinGecko: ✓ {len(cg_prices)}/{len(TOKENS)} цен")

    # Слияние: Hive приоритетнее, CoinGecko добирает пропуски
    prices = dict(cg_prices)
    prices.update(hive_prices)

    discrepancies = compare(hive_prices, cg_prices)

    missing = sorted(set(TOKENS) - set(prices))

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "sources_used": {
            "hive": len(hive_prices),
            "coingecko_direct": len([p for p in prices.values()
                                     if p["source"] == "coingecko_direct"]),
            "total_tokens": len(prices),
        },
        "prices": prices,
        "discrepancies": discrepancies,
        "significant_discrepancies_count": len(discrepancies),
        "missing_tokens": missing,
        "errors": {"hive": hive_err, "coingecko": cg_err},
        "collector_version": "3.0",
    }

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("\n=== ИТОГ ===")
    print(f"  Цен собрано: {len(prices)}/{len(TOKENS)}")
    print(f"  Из них через Hive: {len(hive_prices)}")
    if missing:
        print(f"  Без цены: {', '.join(missing)}")
    if discrepancies:
        print(f"  ⚠ Расхождения ≥{DISCREPANCY_PCT}%: "
              f"{', '.join(d['symbol'] for d in discrepancies)}")
    print(f"\n✓ {OUT_FILE}")

    # Не роняем workflow: без цен движок деградирует, но не падает
    return 0


if __name__ == "__main__":
    sys.exit(main())
