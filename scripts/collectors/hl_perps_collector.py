#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hl_perps_collector.py · v1.0 · 21.08.2026
STRK ENGINE · сбор перпов с Hyperliquid

ЗАЧЕМ
-----
На HL сидят профессиональные трейдеры, на OKX — розница + профи. Когда
funding на этих двух биржах расходится — есть что читать:

  OKX нейтрально + HL положительно → умные покупают, толпа не заметила
  OKX перегрет   + HL нейтрально   → толпа поздно, умные уже вышли

Плюс HL отдаёт **premium** (mark − oracle) — это давление ПРЯМО СЕЙЧАС,
без задержки в 8 часов между списаниями фандинга. Самый честный
индикатор перекоса.

ПРОФИЛЬ ДАННЫХ
--------------
На каждый актив:
  funding_rate_pct       текущая ставка (за 1 час, HL списывает каждый час)
  funding_annualised     она же в годовых
  premium_pct            (mark − oracle) / oracle в процентах
  open_interest_usd      OI в долларах (переведён из size × mark)
  oi_change_pct          изменение против прошлого замера
  day_volume_usd         оборот за сутки
  volume_share_of_oi     оборот к OI — насколько активно торгуется
  hl_bias                LONG_HEAVY / SHORT_HEAVY / NEUTRAL
  hl_bias_ru             человекочитаемо

ЛОГИКА BIAS
-----------
premium > +0.05% → лонги переплачивают за вход, короткая сторона выгодна
premium < -0.05% → шорты переплачивают, длинная сторона выгодна
funding как подтверждение направления

БЕЗ КЛЮЧЕЙ, БЕЗ ЛИМИТОВ
-----------------------
Публичный endpoint /info с POST body — ноль регистрации, ноль тарифа.
Одним запросом получаем данные по 232 активам сразу.

Покрытие: 42/47 токенов юниверса (проверено 21.08.2026).
BONK и PEPE — через префикс k (kBONK, kPEPE — стандартные скины HL
для мемов, автоматически развёртывается 1000×).

ЗАПУСК
------
  python3 scripts/collectors/hl_perps_collector.py
  python3 scripts/collectors/hl_perps_collector.py --token LINK,STRK

ВЫХОД
-----
  data/cache/hl_perps.json
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

CACHE = "data/cache"
OUT_FILE = os.path.join(CACHE, "hl_perps.json")

HL_ENDPOINT = "https://api.hyperliquid.xyz/info"

# наш юниверс (37 токенов) + мемы через k-префикс.
# Ключ — как токен известен в системе (в token_scan),
# значение — как называется на HL (иногда с префиксом).
TOKEN_MAP = {
    # Мажоры
    "BTC": "BTC", "ETH": "ETH", "SOL": "SOL",

    # Наш утилити-роташн
    "STRK": "STRK", "LINK": "LINK", "ETHFI": "ETHFI",
    "MORPHO": "MORPHO", "ONDO": "ONDO",

    # L2
    "ARB": "ARB", "OP": "OP", "MNT": "MNT", "ZK": "ZK",

    # DeFi
    "AAVE": "AAVE", "PENDLE": "PENDLE", "LDO": "LDO",
    "CRV": "CRV", "COMP": "COMP", "SNX": "SNX",
    "DYDX": "DYDX", "GMX": "GMX", "UNI": "UNI",
    "FXS": "FXS", "ENA": "ENA",

    # LST / Restaking
    "EIGEN": "EIGEN", "JTO": "JTO",

    # AI
    "TAO": "TAO", "FET": "FET", "RNDR": "RNDR",
    "AIXBT": "AIXBT", "VIRTUAL": "VIRTUAL",

    # L1 альты
    "TIA": "TIA", "SEI": "SEI", "SUI": "SUI", "APT": "APT",
    "INJ": "INJ",

    # Мемы (через k-префикс — 1000×)
    "BONK": "kBONK", "PEPE": "kPEPE", "DOGE": "DOGE", "WIF": "WIF",

    # Gaming / NFT
    "AXS": "AXS", "SAND": "SAND",

    # Прочее
    "FIL": "FIL",
}

# Не покрыто на HL (проверено 21.08.2026):
#   AKT, CFG, GRT, RPL, SSV — редкие или неликвидные для перпов


PREMIUM_NOISE_PCT = 0.05   # ниже — шум, не сигнал
FUNDING_NOISE_PCT = 0.005  # 0.005% за час = ~44% годовых, солидный сигнал


def fetch_hl_market():
    """
    Один POST /info с типом metaAndAssetCtxs возвращает пару:
      [meta, ctxs] где meta.universe параллелен ctxs.
    Значит один HTTP-вызов даёт данные по ВСЕМ ~230 контрактам.
    """
    try:
        r = requests.post(
            HL_ENDPOINT,
            json={"type": "metaAndAssetCtxs"},
            timeout=20,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            print(f"✗ HL API HTTP {r.status_code}")
            return None, None
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            print("✗ HL API вернул неожиданный формат")
            return None, None
        meta, ctxs = data[0], data[1]
        universe = meta.get("universe", [])
        # индексируем по имени актива
        by_name = {}
        for i, coin in enumerate(universe):
            name = coin.get("name")
            if name and i < len(ctxs):
                by_name[name] = {"meta": coin, "ctx": ctxs[i]}
        return universe, by_name
    except Exception as e:
        print(f"✗ HL fetch error: {e}")
        return None, None


def load_previous():
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            return json.load(f).get("tokens", {})
    except Exception:
        return {}


def classify_bias(premium_pct, funding_pct):
    """
    Premium — главный сигнал, funding — подтверждение.

    LONG_HEAVY  premium > +0.05%      → лонги переплачивают за вход
    SHORT_HEAVY premium < -0.05%      → шорты переплачивают
    NEUTRAL     |premium| < 0.05%     → баланса нет
    """
    if premium_pct is None:
        return "UNKNOWN", "нет данных о премии"

    if premium_pct > PREMIUM_NOISE_PCT:
        base = "LONG_HEAVY"
        ru = f"лонги переплачивают: премия +{premium_pct:.3f}%"
        if funding_pct is not None and funding_pct > FUNDING_NOISE_PCT:
            ru += " · фандинг подтверждает"
        return base, ru

    if premium_pct < -PREMIUM_NOISE_PCT:
        base = "SHORT_HEAVY"
        ru = f"шорты переплачивают: премия {premium_pct:.3f}%"
        if funding_pct is not None and funding_pct < -FUNDING_NOISE_PCT:
            ru += " · фандинг подтверждает"
        return base, ru

    return "NEUTRAL", f"перекоса нет: премия {premium_pct:+.3f}%"


def analyze_token(symbol, hl_name, hl_data, prev_rows):
    """Возвращает entry для одного токена или None если данных нет."""
    row = hl_data.get(hl_name)
    if not row:
        return {"symbol": symbol, "hl_name": hl_name, "status": "NOT_LISTED",
                "note": f"нет контракта {hl_name} на HL"}

    ctx = row["ctx"]
    meta = row["meta"]

    try:
        funding = float(ctx.get("funding", 0)) * 100   # доля → %
        mark = float(ctx.get("markPx", 0))
        oracle = float(ctx.get("oraclePx", 0)) or mark
        premium_raw = ctx.get("premium")
        premium = float(premium_raw) * 100 if premium_raw is not None else None
        oi_base = float(ctx.get("openInterest", 0))
        day_vol = float(ctx.get("dayNtlVlm", 0))
    except (TypeError, ValueError):
        return {"symbol": symbol, "hl_name": hl_name, "status": "BAD_DATA"}

    # OI в USD: контрактная единица × mark price
    oi_usd = round(oi_base * mark) if oi_base and mark else None

    # Изменение OI против прошлого замера
    prev_oi = (prev_rows.get(symbol) or {}).get("open_interest_usd")
    oi_change = None
    if oi_usd and prev_oi:
        oi_change = round((oi_usd - prev_oi) / prev_oi * 100, 2)

    # Оборот к OI — активность рынка
    vol_share = None
    if oi_usd and day_vol:
        vol_share = round(day_vol / oi_usd, 2)

    bias, bias_ru = classify_bias(premium, funding)

    return {
        "symbol": symbol,
        "hl_name": hl_name,
        "status": "OK",
        "mark_price": mark,
        "oracle_price": oracle,
        "funding_rate_pct": round(funding, 5),
        # HL списывает фандинг КАЖДЫЙ ЧАС (в отличие от OKX/Binance 8ч)
        "funding_annualised_pct": round(funding * 24 * 365, 2),
        "premium_pct": round(premium, 4) if premium is not None else None,
        "open_interest_usd": oi_usd,
        "open_interest_base": round(oi_base, 2),
        "oi_change_pct": oi_change,
        "day_volume_usd": round(day_vol),
        "volume_share_of_oi": vol_share,
        "hl_bias": bias,
        "hl_bias_ru": bias_ru,
    }


def main(only=None):
    print("=== HL Perps Collector v1.0 (публичный API, 0 кредитов) ===\n")

    universe, by_name = fetch_hl_market()
    if not by_name:
        print("✗ HL недоступен, кэш не перезаписан")
        sys.exit(1)

    print(f"  HL контрактов доступно: {len(by_name)}")

    prev = load_previous()

    # если only задан — фильтруем
    tokens = {k: v for k, v in TOKEN_MAP.items()
              if not only or k in only}

    print(f"  Собираем: {len(tokens)}\n")

    result_tokens = {}
    ok_count = 0
    for symbol, hl_name in tokens.items():
        entry = analyze_token(symbol, hl_name, by_name, prev)
        result_tokens[symbol] = entry

        if entry.get("status") == "OK":
            ok_count += 1
            oi = entry.get("open_interest_usd") or 0
            oi_str = f"OI ${oi/1e6:.1f}M" if oi else "OI n/a"
            premium = entry.get("premium_pct")
            prem_str = f"prem {premium:+.3f}%" if premium is not None else "prem —"
            oi_chg = entry.get("oi_change_pct")
            chg_str = f" ({oi_chg:+.1f}%)" if oi_chg is not None else ""
            print(f"  {symbol:8} {entry['hl_bias']:12} {prem_str:14} {oi_str}{chg_str}")
        elif entry.get("status") == "NOT_LISTED":
            print(f"  {symbol:8} нет на HL")

    # выявим ЭКСТРЕМАЛЬНЫЕ премии — с них начинается сигнал
    extremes = sorted(
        [t for t in result_tokens.values()
         if t.get("status") == "OK" and t.get("premium_pct") is not None
         and abs(t["premium_pct"]) > 0.1],
        key=lambda t: -abs(t["premium_pct"]),
    )

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "hyperliquid_public",
        "cost": "free · 0 credits",
        "tokens_ok": ok_count,
        "tokens_total": len(tokens),
        "extremes": [
            {"symbol": t["symbol"], "premium_pct": t["premium_pct"],
             "hl_bias": t["hl_bias"]}
            for t in extremes[:8]
        ],
        "tokens": result_tokens,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"\n  Собрано: {ok_count}/{len(tokens)}")
    if extremes:
        print("  Экстремумы (перекосы >0.1%):")
        for t in extremes[:5]:
            print(f"    {t['symbol']:8} premium {t['premium_pct']:+.3f}% · {t['hl_bias']}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    a = ap.parse_args()
    only = set(s.strip().upper() for s in a.token.split(",") if s.strip()) or None
    main(only)
