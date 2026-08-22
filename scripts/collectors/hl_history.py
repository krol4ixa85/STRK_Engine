#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hl_history.py · v1.0 · 22.08.2026
STRK ENGINE · длинная история дневных свечей с Hyperliquid

ЗАЧЕМ
-----
Детекторы, которые считаются из цены и объёма — technical_momentum,
effort_result, volume_profile, cross_token_correlation — можно
восстановить задним числом за любой период, если есть свечи. Своей
истории свечей у платформы нет: volume_profile держит 90 дней и
перезаписывает их каждые 6 часов.

Hyperliquid отдаёт дневные свечи с объёмом бесплатно и без ключа:
5.5 лет по мажорам, 2.5 года по STRK. Этого хватает на настоящий
бэктест — сотни непересекающихся наблюдений вместо шести.

Сравнение источников:
  Dune             потоки на биржи   истории нет, только снапшоты
  Coin Metrics     9 лет, но БЕЗ ОБЪЁМА — правила по объёму не проверить
  Hyperliquid      2.5-5.5 лет с объёмом ← этот

ЗАПУСК
------
  python3 scripts/collectors/hl_history.py
  python3 scripts/collectors/hl_history.py --token LINK,STRK
  python3 scripts/collectors/hl_history.py --days 1200

ВЫХОД
-----
  data/history/hl/<TOKEN>.json     — свечи
  data/cache/hl_history_coverage.json

ЛИЦЕНЗИЯ ДАННЫХ
---------------
Публичный API Hyperliquid, ключ не нужен. В .gitignore добавлена
строка data/history/hl/ — файлы тяжёлые и растут.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
from datetime import datetime, timezone

API = "https://api.hyperliquid.xyz/info"
OUT_DIR_BASE = "data/history/hl"
COVERAGE_BASE = "data/cache/hl_history_coverage"
VP_CACHE = "data/cache/volume_profile.json"

DEFAULT_DAYS = 2000
PAUSE_S = 0.35          # вежливая пауза между запросами
MAX_RETRY = 3


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def token_map():
    """
    TOKEN → имя на Hyperliquid. Берётся из volume_profile.json, который
    это соответствие уже установил и проверил. Гадать по тикеру нельзя:
    на этом уже обжигались — коллектор с выдуманными именами падал
    целиком из-за одного неизвестного тикера.
    """
    vp = load_json(VP_CACHE, {}) or {}
    out = {}
    for sym, d in (vp.get("tokens") or {}).items():
        hl = d.get("hl_name") or sym
        out[sym.upper()] = hl
    return out


def _call(coin, interval, start_ms, end_ms):
    body = json.dumps({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval,
                "startTime": start_ms, "endTime": end_ms},
    }).encode()
    req = urllib.request.Request(API, data=body,
                                 headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def fetch(coin, days, interval="1d"):
    """
    За один запрос API отдаёт не больше 5000 свечей. Для дневных этого
    хватает на 13 лет, для 4-часовых — только на 833 дня, поэтому здесь
    страничная выборка назад по времени.
    """
    now = int(time.time() * 1000)
    start = now - days * 86400000
    if interval == "1d":
        return _call(coin, interval, start, now)

    step_ms = {"4h": 4, "1h": 1, "12h": 12}.get(interval, 4) * 3600 * 1000
    page_ms = 4500 * step_ms
    out, end = [], now
    while end > start:
        chunk = _call(coin, interval, max(start, end - page_ms), end)
        if not chunk:
            break
        out = chunk + out
        oldest = min(c["t"] for c in chunk)
        if oldest >= end:
            break
        end = oldest - 1
        time.sleep(PAUSE_S)
    return out


def normalize(raw, interval="1d"):
    """Свечи Hyperliquid → компактный вид. Кривые записи отбрасываются."""
    fmt = "%Y-%m-%d" if interval == "1d" else "%Y-%m-%dT%H:%M"
    out = []
    for c in raw or []:
        try:
            o, h, l, cl = float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])
            v = float(c.get("v") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not all(x > 0 for x in (o, h, l, cl)):
            continue
        out.append({
            "date": datetime.fromtimestamp(c["t"] / 1000, timezone.utc)
                            .strftime(fmt),
            "o": o, "h": h, "l": l, "c": cl, "v": v,
        })
    out.sort(key=lambda r: r["date"])
    # дубли по дате — оставляем последнюю
    dedup = {}
    for r in out:
        dedup[r["date"]] = r
    return [dedup[k] for k in sorted(dedup)]


def main(only, days, interval):
    print("=== Hyperliquid · длинная история дневных свечей ===\n")
    tmap = token_map()
    if not tmap:
        print(f"  Нет {VP_CACHE} — не из чего взять соответствие тикеров.")
        print("  Сначала: python3 scripts/collectors/volume_profile_collector.py")
        return 1

    syms = [s for s in sorted(tmap) if not only or s in only]
    if not syms:
        print("  Ни один из запрошенных токенов не найден в volume_profile.json")
        return 1

    out_dir = OUT_DIR_BASE if interval == "1d" else f"{OUT_DIR_BASE}_{interval}"
    os.makedirs(out_dir, exist_ok=True)
    cov, total, failed = {}, 0, []

    print(f"{'ТОКЕН':10}{'ДНЕЙ':>7}{'ЛЕТ':>7}  ПЕРИОД")
    print("  " + "─" * 52)

    for sym in syms:
        coin = tmap[sym]
        try:
            rows = normalize(fetch(coin, days, interval), interval)
        except Exception as e:
            failed.append({"token": sym, "hl_name": coin, "error": str(e)[:120]})
            print(f"{sym:10}      —        не ответил: {str(e)[:40]}")
            time.sleep(PAUSE_S)
            continue

        min_rows = 120 if interval == "1d" else 400
        if len(rows) < min_rows:
            failed.append({"token": sym, "hl_name": coin,
                           "error": f"слишком мало свечей: {len(rows)}"})
            print(f"{sym:10}{len(rows):>7}        мало истории, пропуск")
            time.sleep(PAUSE_S)
            continue

        with open(os.path.join(out_dir, f"{sym}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "token": sym, "hl_name": coin,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "interval": interval, "source": "hyperliquid",
                "days": len(rows), "candles": rows,
            }, f, ensure_ascii=False)

        cov[sym] = {"days": len(rows), "first": rows[0]["date"],
                    "last": rows[-1]["date"], "hl_name": coin}
        total += len(rows)
        per_day = 1 if interval == "1d" else 24 // int(interval.rstrip("h"))
        print(f"{sym:10}{len(rows):>7}{len(rows)/365/per_day:>7.1f}  {rows[0]['date']} → {rows[-1]['date']}")
        time.sleep(PAUSE_S)

    os.makedirs("data/cache", exist_ok=True)
    coverage = f"{COVERAGE_BASE}.json" if interval == "1d" else f"{COVERAGE_BASE}_{interval}.json"
    with open(coverage, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "hyperliquid · public API, без ключа",
            "requested_days": days, "interval": interval,
            "tokens_ok": len(cov), "tokens_failed": len(failed),
            "total_days": total,
            "coverage": cov, "failed": failed,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n  Собрано: {len(cov)} токенов, {total} дней суммарно")
    if failed:
        print(f"  Не получилось: {len(failed)} — "
              f"{', '.join(x['token'] for x in failed[:8])}")
    print(f"\n✓ {out_dir}/")
    print(f"✓ {coverage}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", type=str, default="")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--interval", type=str, default="1d",
                    help="1d или 4h — 4h воспроизводит разрешение technical_momentum")
    a = ap.parse_args()
    sel = {x.strip().upper() for x in a.token.split(",") if x.strip()} or None
    sys.exit(main(sel, a.days, a.interval))