#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hl_funding_history.py · v1.0 · 22.08.2026
STRK ENGINE · длинная история фандинга с Hyperliquid

ЗАЧЕМ
-----
Текущий источник фандинга — OKX `public/funding-rate-history` — отдаёт
около 300 записей. На такой глубине проверить гипотезу «фандинг
предсказывает разворот» нельзя: это меньше сорока непересекающихся
наблюдений на горизонте недели, по ОДНОМУ токену.

Hyperliquid — сама по себе биржа перпов — отдаёт почасовой фандинг
бесплатно и без ключа, по всем торгуемым монетам, на годы назад. По
500 записей за запрос, то есть примерно 21 день на страницу.

ЧТО ЭТО ДАЁТ
------------
Возможность проверить наблюдение: «рынком движут перпы, всё ходит по
единой указке». Это не метафора — это проверяемое утверждение. Если
рынок действительно движется перпами, то совокупный перекос фандинга
должен предсказывать разворот. Либо предсказывает, либо нет.

ЗАПУСК
------
  python3 scripts/collectors/hl_funding_history.py
  python3 scripts/collectors/hl_funding_history.py --days 400
  python3 scripts/collectors/hl_funding_history.py --token BTC,ETH,STRK

ВЫХОД
-----
  data/history/hl_funding/<TOKEN>.json
  data/cache/hl_funding_coverage.json

Данные тяжёлые (почасовые за два года), в .gitignore добавлена строка
data/history/hl_funding/ — восстанавливаются одной командой.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
from datetime import datetime, timezone
from collections import defaultdict

API = "https://api.hyperliquid.xyz/info"
OUT_DIR = "data/history/hl_funding"
COVERAGE = "data/cache/hl_funding_coverage.json"
VP_CACHE = "data/cache/volume_profile.json"

DEFAULT_DAYS = 730
PAGE_LIMIT = 500           # столько записей отдаёт один запрос
PAUSE_S = 0.12
MAX_RETRY = 3
HOURS_PER_YEAR = 24 * 365


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def token_map():
    vp = load_json(VP_CACHE, {}) or {}
    return {s.upper(): (d.get("hl_name") or s)
            for s, d in (vp.get("tokens") or {}).items()}


def _call(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.0 * (attempt + 1))
    raise last


def fetch(coin, days):
    """
    Страницы идут ВПЕРЁД от startTime: API отдаёт первые 500 записей
    начиная с указанного момента. Следующая страница начинается сразу
    после последней полученной.
    """
    now = int(time.time() * 1000)
    start = now - days * 86400000
    out, seen, guard = [], set(), 0

    while start < now and guard < 200:
        guard += 1
        page = _call({"type": "fundingHistory", "coin": coin,
                      "startTime": start, "endTime": now})
        if not page:
            break
        fresh = 0
        for r in page:
            t = r.get("time")
            if t is None or t in seen:
                continue
            seen.add(t)
            try:
                out.append((t, float(r["fundingRate"])))
                fresh += 1
            except (KeyError, TypeError, ValueError):
                continue
        if fresh == 0 or len(page) < PAGE_LIMIT:
            break
        start = max(t for t, _ in out) + 1
        time.sleep(PAUSE_S)

    out.sort()
    return out


def to_daily(rows):
    """
    Почасовые ставки → дневные. Годовая ставка считается как средняя
    часовая × часов в году: так число сразу читается человеком
    («+11% годовых»), а не как 0.0000125.
    """
    by_day = defaultdict(list)
    for t, rate in rows:
        d = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")
        by_day[d].append(rate)
    return [{"date": d,
             "funding_hourly_mean": sum(v) / len(v),
             "funding_annual_pct": round(sum(v) / len(v) * HOURS_PER_YEAR * 100, 3),
             "hours": len(v)}
            for d, v in sorted(by_day.items())]


def main(only, days):
    print("=== Hyperliquid · история фандинга ===\n")
    tmap = token_map()
    if not tmap:
        print(f"  Нет {VP_CACHE} — не из чего взять соответствие тикеров.")
        return 1

    syms = [s for s in sorted(tmap) if not only or s in only]
    os.makedirs(OUT_DIR, exist_ok=True)
    cov, failed, total = {}, [], 0

    print(f"{'ТОКЕН':10}{'ДНЕЙ':>7}{'СРЕДНИЙ':>10}  ПЕРИОД")
    print("  " + "─" * 54)

    for sym in syms:
        coin = tmap[sym]
        try:
            rows = fetch(coin, days)
        except Exception as e:
            failed.append({"token": sym, "error": str(e)[:120]})
            print(f"{sym:10}      —        не ответил")
            continue

        daily = to_daily(rows)
        if len(daily) < 120:
            failed.append({"token": sym,
                           "error": f"мало дней: {len(daily)}"})
            print(f"{sym:10}{len(daily):>7}        мало истории, пропуск")
            continue

        with open(os.path.join(OUT_DIR, f"{sym}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "token": sym, "hl_name": coin,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "hyperliquid · fundingHistory",
                "days": len(daily), "series": daily,
            }, f, ensure_ascii=False)

        avg = sum(d["funding_annual_pct"] for d in daily) / len(daily)
        cov[sym] = {"days": len(daily), "first": daily[0]["date"],
                    "last": daily[-1]["date"], "avg_annual_pct": round(avg, 2)}
        total += len(daily)
        print(f"{sym:10}{len(daily):>7}{avg:>+9.1f}%  "
              f"{daily[0]['date']} → {daily[-1]['date']}")

    os.makedirs("data/cache", exist_ok=True)
    with open(COVERAGE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "hyperliquid · публичный API, без ключа",
            "requested_days": days,
            "tokens_ok": len(cov), "tokens_failed": len(failed),
            "total_days": total, "coverage": cov, "failed": failed,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n  Собрано: {len(cov)} токенов, {total} дней суммарно")
    if failed:
        print(f"  Не получилось: {', '.join(x['token'] for x in failed[:8])}")
    print(f"\n✓ {OUT_DIR}/\n✓ {COVERAGE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    a = ap.parse_args()
    sel = {x.strip().upper() for x in a.token.split(",") if x.strip()} or None
    sys.exit(main(sel, a.days))