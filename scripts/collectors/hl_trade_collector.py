#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hl_trade_collector.py · v1.0 · 21.08.2026
STRK ENGINE · накопление публичных сделок Hyperliquid

ЗАЧЕМ
-----
Nansen MCP работает только внутри интерактивного чата — из GitHub Actions
дёргать нельзя без платного x402. Значит **watchlist топ-адресов HL
собираем сами**, накапливая публичные сделки в append-only лог.

recentTrades отдаёт всего 10 последних трейдов в окне ~20 секунд.
За каждый запуск (10 токенов) получаем ~100 сделок. При cron каждые
15 минут — 9600 сделок в сутки. Из них крупных (>$10K) сегодня по
LINK было ~50-100, значит база строится быстро.

ЧТО ПИШЕМ В ЛОГ
---------------
data/history/hl_trades.jsonl · строки вида:
  {"ts": ..., "coin": "LINK", "side": "B", "px": 11.48, "sz": 8.6,
   "usd": 98.7, "buyer": "0xe99a...eba8", "seller": "0xa382...f1f7",
   "hash": "0x..."}

Дедупликация по hash — один и тот же трейд не запишется дважды даже
если следующий прогон захватит окно с пересечением.

СЛЕДУЮЩИЙ ШАГ (через 2-3 дня)
-----------------------------
hl_whale_positions.py прочитает лог за 7 дней, соберёт топ-50 адресов
по объёму и запросит их текущие позиции через clearinghouseState.
В модалке появится: "8 из 30 топ-трейдеров сейчас в лонге по LINK".

БЕЗ КЛЮЧЕЙ, БЕЗ ЛИМИТОВ
-----------------------
Публичный /info endpoint. Один POST на токен. Rate limits щедрые.

ЗАПУСК
------
  python3 scripts/collectors/hl_trade_collector.py
"""

import os
import sys
import json
from datetime import datetime, timezone, timedelta
from collections import Counter

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

HISTORY = "data/history"
CACHE = "data/cache"
LOG_FILE = os.path.join(HISTORY, "hl_trades.jsonl")
STATUS_FILE = os.path.join(CACHE, "hl_trades_status.json")
SEEN_FILE = os.path.join(CACHE, ".hl_seen_hashes.json")

HL_ENDPOINT = "https://api.hyperliquid.xyz/info"

# ─── 10 ключевых токенов: наши позиции + мажоры ─────────────────
# Начинаем с узкого списка чтобы не спамить лог. Расширим когда
# станет ясно как быстро накапливаются данные.
COINS_TO_TRACK = [
    "BTC", "ETH", "SOL",
    "LINK", "STRK", "ETHFI", "MORPHO", "ONDO",
    "ARB", "AAVE",
]

# Мелкие трейды не пишем — они забьют лог розницей. Порог $500.
# Для крупных мемов (BONK, PEPE) порог не меняется — это доллары.
MIN_TRADE_USD = 500

# Хранение хешей от повторных записей: держим последние 5000
SEEN_HASH_LIMIT = 5000

# Старее — удаляем из лога (30 дней хватает для 7-дневного окна анализа)
LOG_RETAIN_DAYS = 30


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch_recent_trades(coin):
    try:
        r = requests.post(
            HL_ENDPOINT,
            json={"type": "recentTrades", "coin": coin},
            timeout=15,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception:
        return None


def process_trade(t, coin):
    """Нормализует сделку в компактный формат."""
    try:
        px = float(t.get("px", 0))
        sz = float(t.get("sz", 0))
        usd = px * sz
    except (TypeError, ValueError):
        return None

    if usd < MIN_TRADE_USD:
        return None

    users = t.get("users") or []
    if len(users) < 2:
        return None

    side = t.get("side")  # "B" = таker купил, "A" = taker продал
    # На HL при side="B" первый юзер это taker (покупатель), второй maker (продавец)
    # При side="A" — наоборот
    if side == "B":
        buyer, seller = users[0], users[1]
    elif side == "A":
        buyer, seller = users[1], users[0]
    else:
        return None

    return {
        "ts": int(t.get("time", 0)),
        "coin": coin,
        "side": side,
        "px": round(px, 6),
        "sz": round(sz, 4),
        "usd": round(usd, 2),
        "buyer": buyer.lower() if buyer else None,
        "seller": seller.lower() if seller else None,
        "hash": t.get("hash", "")[:66],
    }


def rotate_log():
    """Удаляет записи старше LOG_RETAIN_DAYS."""
    if not os.path.exists(LOG_FILE):
        return 0, 0

    cutoff_ms = int((datetime.now(timezone.utc) -
                     timedelta(days=LOG_RETAIN_DAYS)).timestamp() * 1000)

    kept, dropped = [], 0
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("ts", 0) >= cutoff_ms:
                    kept.append(line)
                else:
                    dropped += 1
            except json.JSONDecodeError:
                dropped += 1

    if dropped > 0:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")
    return len(kept), dropped


def main():
    print("=== HL Trade Collector v1.0 (публичный API, 0 кредитов) ===\n")

    now = datetime.now(timezone.utc)
    seen = set(load_json(SEEN_FILE, {}).get("hashes", []))

    total_seen, total_new, total_usd = 0, 0, 0.0
    per_coin = Counter()

    os.makedirs(HISTORY, exist_ok=True)

    # append-only запись
    with open(LOG_FILE, "a", encoding="utf-8") as log:
        for coin in COINS_TO_TRACK:
            trades = fetch_recent_trades(coin)
            if trades is None:
                print(f"  {coin:8} ✗ HL API не ответил")
                continue

            total_seen += len(trades)
            new_for_coin = 0
            usd_for_coin = 0.0

            for t in trades:
                rec = process_trade(t, coin)
                if not rec:
                    continue
                h = rec["hash"]
                if h in seen:
                    continue
                seen.add(h)
                log.write(json.dumps(rec, ensure_ascii=False) + "\n")
                new_for_coin += 1
                usd_for_coin += rec["usd"]

            per_coin[coin] = new_for_coin
            total_new += new_for_coin
            total_usd += usd_for_coin
            print(f"  {coin:8} +{new_for_coin:3} новых из {len(trades)} · ${usd_for_coin:,.0f}")

    # Обрезка списка хешей — держим только последние SEEN_HASH_LIMIT
    seen_list = list(seen)
    if len(seen_list) > SEEN_HASH_LIMIT:
        seen_list = seen_list[-SEEN_HASH_LIMIT:]
    save_json(SEEN_FILE, {"hashes": seen_list, "updated": now.isoformat()})

    # Ротация старых записей
    kept, dropped = rotate_log()

    status = {
        "computed_at": now.isoformat(),
        "coins_tracked": COINS_TO_TRACK,
        "min_trade_usd": MIN_TRADE_USD,
        "retain_days": LOG_RETAIN_DAYS,
        "this_run": {
            "trades_seen": total_seen,
            "trades_new": total_new,
            "usd_volume_new": round(total_usd),
            "per_coin": dict(per_coin),
        },
        "log_stats": {
            "total_records": kept,
            "dropped_this_run": dropped,
        },
    }
    save_json(STATUS_FILE, status)

    print(f"\n  Записано новых: {total_new} на ${total_usd:,.0f}")
    print(f"  Всего в логе: {kept} записей")
    if dropped:
        print(f"  Ротация: удалено {dropped} записей старше {LOG_RETAIN_DAYS} дней")
    print(f"\n✓ {LOG_FILE}")
    print(f"✓ {STATUS_FILE}")


if __name__ == "__main__":
    main()
