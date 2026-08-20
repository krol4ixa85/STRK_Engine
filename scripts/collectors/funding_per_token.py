#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
funding_per_token.py · v1.0 · 20.08.2026
STRK ENGINE · funding rate + open interest по каждому токену

ПОЧЕМУ БЕСПЛАТНО
----------------
Публичные endpoint'ы OKX не требуют ключа, регистрации и не тарифицируются.
Ни одного кредита Hive, ни одного кредита Dune.

ПОЧЕМУ OKX, А НЕ BINANCE
------------------------
Проверено 20.08.2026: Binance Futures отдаёт HTTP 451 (geo-block) с адресов
дата-центров, включая раннеры GitHub Actions в США. То есть коллектор на
Binance молча возвращал бы пустоту в проде, работая на локальной машине.
OKX отвечает 200 и не имеет таких ограничений.
Bybit отдал 403 — тоже не годится.

Поэтому этот модуль — единственный в проекте, который можно гонять
каждые 30 минут без оглядки на бюджет.

ЧТО СОБИРАЕТ
------------
На каждый токен:
  funding_rate_pct     текущая ставка фандинга (% за период)
  funding_annualised   она же в годовых — понятнее для оценки перекоса
  next_funding_time    когда следующее списание
  open_interest_usd    открытый интерес в долларах
  oi_change_pct        изменение OI против прошлого прогона
  bias                 LONGS_PAY / SHORTS_PAY / NEUTRAL

ЛОГИКА BIAS (простыми словами)
------------------------------
Фандинг положительный  → лонги платят шортам → в лонгах перекос → риск
                          каскадной ликвидации вниз
Фандинг отрицательный  → шорты платят лонгам → в шортах перекос → топливо
                          для сквиза вверх

Порог ±0.01% за период (≈±11% годовых) взят как граница шума.

ЗАПУСК
------
  python3 scripts/collectors/funding_per_token.py

ВЫХОД
-----
  data/cache/funding_per_token.json
"""

import os
import json
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

CACHE_DIR = "data/cache"
OUT_FILE = os.path.join(CACHE_DIR, "funding_per_token.json")

# symbol -> instId бессрочного контракта на OKX
SYMBOLS = {s: f"{s}-USDT-SWAP" for s in [
    "BTC", "ETH", "SOL", "STRK", "LINK", "ETHFI", "MORPHO", "ONDO",
    "ARB", "OP", "AAVE", "PENDLE", "LDO", "EIGEN", "UNI",
]}

OKX = "https://www.okx.com/api/v5"

NOISE_THRESHOLD_PCT = 0.01   # ±0.01% за период — ниже это шум
TIMEOUT = 20


def _get(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_okx_open_interest():
    """Один запрос — открытый интерес по всем 450+ контрактам сразу."""
    d = _get(f"{OKX}/public/open-interest", {"instType": "SWAP"})
    if not d or d.get("code") != "0":
        return {}
    return {r["instId"]: r for r in d.get("data", []) if "instId" in r}


def fetch_okx_tickers():
    """Один запрос — цены по всем контрактам сразу."""
    d = _get(f"{OKX}/market/tickers", {"instType": "SWAP"})
    if not d or d.get("code") != "0":
        return {}
    return {r["instId"]: r for r in d.get("data", []) if "instId" in r}


def fetch_okx_funding(inst_id):
    """Ставка фандинга по одному контракту — bulk-эндпоинта у OKX нет."""
    d = _get(f"{OKX}/public/funding-rate", {"instId": inst_id})
    if not d or d.get("code") != "0":
        return None
    rows = d.get("data") or []
    return rows[0] if rows else None


def load_previous():
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            return json.load(f).get("tokens", {})
    except Exception:
        return {}


def classify(rate_pct):
    if rate_pct is None:
        return "UNKNOWN", "нет данных"
    if rate_pct > NOISE_THRESHOLD_PCT:
        return "LONGS_PAY", "лонги платят шортам — перекос в лонги, риск каскада вниз"
    if rate_pct < -NOISE_THRESHOLD_PCT:
        return "SHORTS_PAY", "шорты платят лонгам — перекос в шорты, топливо для сквиза вверх"
    return "NEUTRAL", "фандинг близок к нулю — перекоса нет"


def main():
    print("=== Funding per token · v1.0 · OKX (бесплатно, 0 кредитов) ===\n")

    prev = load_previous()
    oi_bulk = fetch_okx_open_interest()
    tickers = fetch_okx_tickers()

    if not tickers:
        print("✗ OKX недоступен — выходим, кэш не перезаписываем")
        return

    print(f"  Контрактов у OKX: {len(tickers)} · ищем {len(SYMBOLS)}\n")

    tokens = {}
    for symbol, inst in SYMBOLS.items():
        if inst not in tickers:
            tokens[symbol] = {"symbol": symbol, "status": "NO_PERP",
                              "note": f"{inst} нет на OKX"}
            print(f"  {symbol:8} — бессрочного контракта нет")
            continue

        fr = fetch_okx_funding(inst)
        time.sleep(0.12)   # OKX public: 20 запросов / 2 сек, идём с запасом

        if not fr:
            tokens[symbol] = {"symbol": symbol, "status": "NO_FUNDING"}
            print(f"  {symbol:8} — фандинг недоступен")
            continue

        try:
            rate = float(fr.get("fundingRate", 0)) * 100     # доля → проценты
            mark = float(tickers[inst].get("last", 0))
        except (TypeError, ValueError):
            tokens[symbol] = {"symbol": symbol, "status": "BAD_DATA"}
            continue

        oi_row = oi_bulk.get(inst) or {}
        try:
            oi_usd = round(float(oi_row.get("oiUsd"))) if oi_row.get("oiUsd") else None
        except (TypeError, ValueError):
            oi_usd = None

        prev_oi = (prev.get(symbol) or {}).get("open_interest_usd")
        oi_change = None
        if oi_usd and prev_oi:
            oi_change = round((oi_usd - prev_oi) / prev_oi * 100, 2)

        bias, bias_ru = classify(rate)

        next_funding = None
        nft = fr.get("nextFundingTime")
        if nft:
            try:
                next_funding = datetime.fromtimestamp(
                    int(nft) / 1000, timezone.utc).isoformat()
            except Exception:
                pass

        tokens[symbol] = {
            "symbol": symbol,
            "status": "OK",
            "inst_id": inst,
            "mark_price": mark,
            "funding_rate_pct": round(rate, 5),
            # OKX списывает фандинг каждые 8 часов → 3 раза в сутки
            "funding_annualised_pct": round(rate * 3 * 365, 2),
            "next_funding_time": next_funding,
            "open_interest_usd": oi_usd,
            "oi_change_pct": oi_change,
            "bias": bias,
            "bias_ru": bias_ru,
        }

        ann = tokens[symbol]["funding_annualised_pct"]
        oi_str = f"OI ${oi_usd/1e6:.1f}M" if oi_usd else "OI n/a"
        chg = f" ({oi_change:+.1f}%)" if oi_change is not None else ""
        print(f"  {symbol:8} {rate:+.4f}% ({ann:+.0f}% годовых) · {oi_str}{chg} · {bias}")

    ok = sum(1 for t in tokens.values() if t.get("status") == "OK")

    extremes = sorted(
        [t for t in tokens.values()
         if t.get("status") == "OK" and abs(t.get("funding_rate_pct", 0)) > 0.03],
        key=lambda t: -abs(t["funding_rate_pct"]),
    )

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "okx_public_swap",
        "cost": "free · 0 credits",
        "tokens_ok": ok,
        "tokens_total": len(SYMBOLS),
        "extremes": [
            {"symbol": t["symbol"],
             "funding_rate_pct": t["funding_rate_pct"],
             "annualised_pct": t["funding_annualised_pct"],
             "bias": t["bias"]}
            for t in extremes[:5]
        ],
        "tokens": tokens,
    }

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Собрано: {ok}/{len(SYMBOLS)}")
    if extremes:
        print("  Перекосы: " + ", ".join(
            f"{t['symbol']} {t['funding_rate_pct']:+.3f}%" for t in extremes[:5]))
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    main()
