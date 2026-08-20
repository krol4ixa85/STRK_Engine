#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hive_health_collector.py · v1.0 · 20.08.2026
STRK ENGINE · Hive Intelligence integration

ЗАЧЕМ ЭТОТ ФАЙЛ СУЩЕСТВУЕТ
--------------------------
Не для цен. Цены бесплатно даёт CoinGecko / ccxt — тратить на них кредиты Hive
бессмысленно.

Hive здесь закрывает ДВЕ метрики, которые MASTER_INSTRUCTION требует
(§0.13, MUST #18 и MUST #19), но которые движок до сих пор не умел считать:

  MUST #18 · концентрация топ-10 держателей  → get_token_top_holders
  MUST #19 · глубина рынка на 2%             → get_coin_tickers(depth=true)

Обе метрики закрываются одним прогоном по юниверсу.

СТОИМОСТЬ
---------
Тариф Free Demo: 10 000 кредитов/мес, 5 ключей, 30 запросов/мин.
Один material-вызов = 1 кредит. GET /api/v1/tools кредитов НЕ стоит.

  2 кредита на токен (depth + holders)
  15 токенов = 30 кредитов за прогон
  еженедельно = ~120/мес (1,2% бюджета)
  ежедневно   = ~900/мес (9% бюджета)

Резолв контрактных адресов идёт через бесплатный CoinGecko — 0 кредитов Hive
и всегда актуальный адрес вместо захардкоженного списка.

ЗАЩИТА БЮДЖЕТА
--------------
Счётчик кредитов в data/cache/hive_credits.json, сброс по календарному месяцу.
При достижении HIVE_MONTHLY_BUDGET скрипт останавливается сам и пишет
budget_exhausted в отчёт, а не молча жжёт лимит.

ЗАПУСК
------
  export HIVE_API_KEY=hive_live_...
  python3 hive/hive_health_collector.py            # прогон по юниверсу
  python3 hive/hive_health_collector.py --catalog  # бесплатно: список инструментов
  python3 hive/hive_health_collector.py --dry-run  # без единого вызова Hive

ВЫХОД
-----
  data/cache/hive_token_health.json
  data/cache/hive_credits.json
  data/cache/hive_catalog.json   (только при --catalog)
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
    print("ERROR: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
# КОНФИГ
# ─────────────────────────────────────────────────────────────

HIVE_BASE = os.getenv("HIVE_API_URL", "https://mcp.hiveintelligence.xyz").rstrip("/")
EXECUTE_URL = f"{HIVE_BASE}/api/v1/execute"
TOOLS_URL = f"{HIVE_BASE}/api/v1/tools"

CACHE_DIR = "data/cache"
OUT_FILE = os.path.join(CACHE_DIR, "hive_token_health.json")
CREDITS_FILE = os.path.join(CACHE_DIR, "hive_credits.json")
CATALOG_FILE = os.path.join(CACHE_DIR, "hive_catalog.json")

# Free Demo = 10 000. Держим запас 20% на ручные эксперименты.
MONTHLY_BUDGET = int(os.getenv("HIVE_MONTHLY_BUDGET", "8000"))

# 30 req/min на Free Demo → 2.2 сек между вызовами с запасом
RATE_SLEEP = float(os.getenv("HIVE_RATE_SLEEP", "2.2"))

# Юниверс: symbol -> coingecko id. Адреса резолвятся автоматически.
UNIVERSE = {
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
    "UNI": "uniswap",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

# Токены без собственного ERC-20 контракта — holders не запрашиваем
NO_CONTRACT = {"BTC", "ETH", "SOL"}

# CoinGecko platform id -> Hive/GeckoTerminal network id
NETWORK_MAP = {
    "ethereum": "eth",
    "arbitrum-one": "arbitrum",
    "optimistic-ethereum": "optimism",
    "base": "base",
    "polygon-pos": "polygon_pos",
    "binance-smart-chain": "bsc",
    "starknet": "starknet",
    "solana": "solana",
}

PREFERRED_PLATFORMS = ["ethereum", "arbitrum-one", "base", "optimistic-ethereum", "starknet", "solana"]


# ─────────────────────────────────────────────────────────────
# УЧЁТ КРЕДИТОВ
# ─────────────────────────────────────────────────────────────

class CreditGuard:
    """Считает материальные вызовы и не даёт выйти за месячный бюджет."""

    def __init__(self, budget=MONTHLY_BUDGET):
        self.budget = budget
        self.month = datetime.now(timezone.utc).strftime("%Y-%m")
        self.used = 0
        self._load()

    def _load(self):
        try:
            with open(CREDITS_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("month") == self.month:
                self.used = int(d.get("used", 0))
            else:
                print(f"  ℹ новый месяц ({self.month}) — счётчик кредитов сброшен")
        except Exception:
            pass

    def remaining(self):
        return max(0, self.budget - self.used)

    def can_spend(self, n=1):
        return self.used + n <= self.budget

    def spend(self, n=1):
        self.used += n

    def save(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CREDITS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "month": self.month,
                "used": self.used,
                "budget": self.budget,
                "remaining": self.remaining(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)


# ─────────────────────────────────────────────────────────────
# КЛИЕНТ HIVE
# ─────────────────────────────────────────────────────────────

class HiveClient:
    """
    Тонкий REST-клиент поверх POST /api/v1/execute.

    Формат подтверждён документацией Hive (Client Setup · REST API):
      POST https://mcp.hiveintelligence.xyz/api/v1/execute
      Authorization: Bearer <key>
      {"tool": "get_price", "args": {...}}
    """

    def __init__(self, api_key, guard, dry_run=False):
        self.api_key = api_key
        self.guard = guard
        self.dry_run = dry_run
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "Content-Type": "application/json",
            "User-Agent": "STRK-Engine/1.0",
        })
        self.errors = []

    def list_tools(self):
        """Бесплатно. Кредитов не стоит — можно звать сколько угодно."""
        r = self.s.get(TOOLS_URL, timeout=30)
        r.raise_for_status()
        return r.json().get("data", [])

    def call(self, tool, args, retries=3):
        """Материальный вызов = 1 кредит. Возвращает (data, error)."""
        if self.dry_run:
            return None, "dry_run"

        if not self.guard.can_spend(1):
            return None, "budget_exhausted"

        payload = {"tool": tool, "args": args}
        backoff = 3

        for attempt in range(1, retries + 1):
            try:
                r = self.s.post(EXECUTE_URL, json=payload, timeout=45)
            except requests.RequestException as e:
                if attempt == retries:
                    return None, f"network: {e}"
                time.sleep(backoff)
                backoff *= 2
                continue

            # 401 — ключ отсутствует/невалиден. Повтор бессмыслен.
            if r.status_code == 401:
                return None, "auth: ключ отсутствует или отозван"

            # 402 — кредиты кончились на стороне Hive
            if r.status_code == 402:
                return None, "payment: кредиты Hive исчерпаны"

            # 429 — rate limit. Ждём и повторяем.
            if r.status_code == 429:
                if attempt == retries:
                    return None, "rate_limit"
                time.sleep(60)
                continue

            if r.status_code >= 500:
                if attempt == retries:
                    return None, f"server {r.status_code}"
                time.sleep(backoff)
                backoff *= 2
                continue

            self.guard.spend(1)  # вызов состоялся — кредит списан

            try:
                body = r.json()
            except ValueError:
                return None, f"bad json (http {r.status_code})"

            if not body.get("ok", True):
                err = body.get("error", {})
                return None, f"{err.get('code', 'error')}: {err.get('message', '')[:120]}"

            return body.get("data", body), None

        return None, "exhausted"


# ─────────────────────────────────────────────────────────────
# БЕСПЛАТНЫЙ РЕЗОЛВ АДРЕСОВ (CoinGecko, 0 кредитов Hive)
# ─────────────────────────────────────────────────────────────

def resolve_contract(cg_id):
    """Возвращает (network_id, address) или (None, None)."""
    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}"
    params = {
        "localization": "false", "tickers": "false", "market_data": "false",
        "community_data": "false", "developer_data": "false", "sparkline": "false",
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return None, None
        platforms = r.json().get("platforms", {}) or {}
    except Exception:
        return None, None

    for p in PREFERRED_PLATFORMS:
        addr = platforms.get(p)
        if addr and str(addr).strip():
            return NETWORK_MAP.get(p, p), str(addr).strip()

    for p, addr in platforms.items():
        if addr and str(addr).strip() and p in NETWORK_MAP:
            return NETWORK_MAP[p], str(addr).strip()

    return None, None


# ─────────────────────────────────────────────────────────────
# MUST #19 · ГЛУБИНА НА 2%
# ─────────────────────────────────────────────────────────────

def collect_depth(hive, symbol, cg_id):
    """
    get_coin_tickers(depth=true) отдаёт cost_to_move_up_usd /
    cost_to_move_down_usd по каждой торговой паре — это ровно
    «сколько долларов двигает цену на 2%» из MUST #19.
    """
    data, err = hive.call("get_coin_tickers", {
        "id": cg_id,
        "depth": True,
        "order": "volume_desc",
    })
    if err:
        return {"status": "ERROR", "error": err}

    tickers = data.get("tickers", data) if isinstance(data, dict) else data
    if not isinstance(tickers, list) or not tickers:
        return {"status": "NO_DATA"}

    up_total = down_total = 0.0
    venues = []

    for t in tickers[:25]:
        if not isinstance(t, dict):
            continue
        up = t.get("cost_to_move_up_usd")
        down = t.get("cost_to_move_down_usd")
        if up is None and down is None:
            continue
        up = float(up or 0)
        down = float(down or 0)
        up_total += up
        down_total += down
        venues.append({
            "market": (t.get("market") or {}).get("name", "?"),
            "pair": f"{t.get('base', '?')}/{t.get('target', '?')}",
            "up_usd": round(up),
            "down_usd": round(down),
            "volume_usd": round(float(t.get("converted_volume", {}).get("usd", 0) or 0)),
        })

    if not venues:
        return {"status": "NO_DEPTH_FIELD",
                "note": "тикеры получены, но поля глубины пусты для этого актива"}

    venues.sort(key=lambda v: v["up_usd"] + v["down_usd"], reverse=True)
    total = up_total + down_total

    # Порог из MUST #19: чем меньше $ двигает цену на 2%, тем тоньше рынок
    if total < 50_000:
        grade = "THIN"
    elif total < 250_000:
        grade = "MEDIUM"
    else:
        grade = "DEEP"

    return {
        "status": "OK",
        "cost_to_move_2pct_up_usd": round(up_total),
        "cost_to_move_2pct_down_usd": round(down_total),
        "total_2pct_depth_usd": round(total),
        "depth_grade": grade,
        "venues_counted": len(venues),
        "top_venues": venues[:5],
    }


# ─────────────────────────────────────────────────────────────
# MUST #18 · КОНЦЕНТРАЦИЯ ДЕРЖАТЕЛЕЙ
# ─────────────────────────────────────────────────────────────

def collect_holders(hive, symbol, network, address):
    data, err = hive.call("get_token_top_holders", {
        "network": network,
        "address": address,
        "holders": "20",
    })
    if err:
        return {"status": "ERROR", "error": err}

    holders = data.get("holders", data) if isinstance(data, dict) else data
    if not isinstance(holders, list) or not holders:
        return {"status": "NO_DATA"}

    def pct(h):
        for k in ("percentage", "percent", "share", "pct", "balance_percentage"):
            if h.get(k) is not None:
                try:
                    return float(h[k])
                except (TypeError, ValueError):
                    pass
        return None

    shares = [p for p in (pct(h) for h in holders if isinstance(h, dict)) if p is not None]
    if not shares:
        return {"status": "NO_PCT_FIELD", "holders_returned": len(holders)}

    top10 = round(sum(shares[:10]), 2)
    top1 = round(shares[0], 2)

    # Порог из MUST #18
    if top10 > 60:
        grade = "HIGH_CONCENTRATION"
    elif top10 > 35:
        grade = "MEDIUM_CONCENTRATION"
    else:
        grade = "DISTRIBUTED"

    return {
        "status": "OK",
        "network": network,
        "address": address,
        "top1_pct": top1,
        "top10_pct": top10,
        "top20_pct": round(sum(shares[:20]), 2),
        "concentration_grade": grade,
        "holders_analysed": len(shares),
    }


# ─────────────────────────────────────────────────────────────
# ГЛАВНЫЙ ПРОГОН
# ─────────────────────────────────────────────────────────────

def run(dry_run=False, only=None):
    api_key = os.getenv("HIVE_API_KEY", "").strip()

    if not api_key and not dry_run:
        print("✗ HIVE_API_KEY не задан.")
        print("  GitHub → Settings → Secrets and variables → Actions → New repository secret")
        print("  Имя: HIVE_API_KEY · Значение: hive_live_...")
        sys.exit(1)

    if api_key:
        print(f"✓ HIVE_API_KEY загружен ({len(api_key)} символов, "
              f"префикс {api_key[:9]}…)")

    guard = CreditGuard()
    hive = HiveClient(api_key, guard, dry_run=dry_run)

    tokens = {k: v for k, v in UNIVERSE.items() if not only or k in only}
    planned = sum(2 if s not in NO_CONTRACT else 1 for s in tokens)

    print(f"\n📊 Юниверс: {len(tokens)} токенов · план ~{planned} кредитов")
    print(f"   Бюджет: {guard.used}/{guard.budget} использовано · "
          f"{guard.remaining()} осталось\n")

    if not guard.can_spend(planned):
        print(f"⛔ Бюджет не позволяет: нужно {planned}, доступно {guard.remaining()}")
        print("   Прогон отменён, чтобы не оставить данные наполовину собранными.")
        return

    results = {}
    for i, (symbol, cg_id) in enumerate(tokens.items(), 1):
        print(f"[{i}/{len(tokens)}] {symbol}")
        entry = {"symbol": symbol, "coingecko_id": cg_id}

        # MUST #19 · глубина
        entry["depth"] = collect_depth(hive, symbol, cg_id)
        d = entry["depth"]
        if d["status"] == "OK":
            print(f"   глубина 2%: ${d['total_2pct_depth_usd']:,} · {d['depth_grade']}")
        else:
            print(f"   глубина: {d['status']} {d.get('error', '')}")
            if d.get("error") in ("budget_exhausted", "auth: ключ отсутствует или отозван"):
                break
        time.sleep(RATE_SLEEP)

        # MUST #18 · концентрация
        if symbol in NO_CONTRACT:
            entry["holders"] = {"status": "SKIPPED", "reason": "нет ERC-20 контракта"}
        else:
            network, address = resolve_contract(cg_id)  # бесплатно
            if not address:
                entry["holders"] = {"status": "NO_CONTRACT_RESOLVED"}
                print("   holders: контракт не разрешён через CoinGecko")
            else:
                entry["holders"] = collect_holders(hive, symbol, network, address)
                h = entry["holders"]
                if h["status"] == "OK":
                    print(f"   топ-10: {h['top10_pct']}% · {h['concentration_grade']}")
                else:
                    print(f"   holders: {h['status']} {h.get('error', '')}")
                time.sleep(RATE_SLEEP)

        results[symbol] = entry

    guard.save()

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "hive_intelligence",
        "endpoint": EXECUTE_URL,
        "must_covered": ["MUST_18_holder_concentration", "MUST_19_market_depth_2pct"],
        "credits": {
            "month": guard.month,
            "used_this_month": guard.used,
            "budget": guard.budget,
            "remaining": guard.remaining(),
            "spent_this_run": planned if not dry_run else 0,
        },
        "tokens": results,
    }

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    ok_depth = sum(1 for v in results.values() if v.get("depth", {}).get("status") == "OK")
    ok_hold = sum(1 for v in results.values() if v.get("holders", {}).get("status") == "OK")

    print(f"\n✓ {OUT_FILE}")
    print(f"  глубина собрана: {ok_depth}/{len(results)}")
    print(f"  держатели собраны: {ok_hold}/{len(results)}")
    print(f"  кредитов за месяц: {guard.used}/{guard.budget}")


def show_catalog():
    """Бесплатно. С ключом отдаёт полный каталог, без ключа — публичную выборку."""
    api_key = os.getenv("HIVE_API_KEY", "").strip()
    guard = CreditGuard()
    hive = HiveClient(api_key, guard)
    tools = hive.list_tools()

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(),
                   "authenticated": bool(api_key),
                   "count": len(tools),
                   "tools": tools}, f, indent=2, ensure_ascii=False)

    print(f"Инструментов доступно: {len(tools)} "
          f"({'с ключом' if api_key else 'публичная выборка, без ключа'})")
    print(f"Сохранено: {CATALOG_FILE}\n")
    for t in sorted(tools, key=lambda x: x.get("name", "")):
        print(f"  {t.get('name')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", action="store_true",
                    help="показать доступные инструменты (бесплатно, 0 кредитов)")
    ap.add_argument("--dry-run", action="store_true",
                    help="прогон без единого вызова Hive")
    ap.add_argument("--only", type=str, default="",
                    help="только эти токены, через запятую: STRK,LINK")
    a = ap.parse_args()

    if a.catalog:
        show_catalog()
    else:
        only = {s.strip().upper() for s in a.only.split(",") if s.strip()} or None
        run(dry_run=a.dry_run, only=only)
