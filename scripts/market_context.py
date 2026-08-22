#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_context.py · v1.0 · 22.08.2026
STRK ENGINE · контекст рынка: сколько в движении токена рынка, а сколько его

ЗАЧЕМ
-----
Измерено на 42 токенах за 5.5 лет: **медианно 57% дневного движения
токена — это движение рынка целиком**, а не самого токена. У ETH 80%,
у ARB 74%, у LINK 73%.

Отсюда вопрос, который возникает каждый раз и на который сейчас нечем
ответить: детектор говорит «LINK выходит вверх на объёме» — это LINK
или это рынок поднял всех, и LINK просто плывёт?

Этот скрипт отвечает числом.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ
-------------------------
Прогноза. Перекос фандинга показывается как КОНТЕКСТ и подписан прямо:
связь с будущей доходностью измерена и равна нулю (корреляция +0.03 /
+0.09 / -0.05 на 7/14/28 днях, t от -0.27 до +0.66).

Соблазн повесить в шапку стрелку «рынок идёт вверх» большой. Но стрелка
без числа — это ровно то, что мы неделю выковыривали из движка.
Синхронность и ширина — измеримые состояния рынка, а не предсказания,
и полезны именно этим.

СТОИМОСТЬ
---------
Ноль. Дневные свечи и текущий фандинг берутся у Hyperliquid — публичный
API без ключа. Dune не трогается.

ЗАПУСК
------
  python3 scripts/market_context.py
  python3 scripts/market_context.py --days 120

ВЫХОД
-----
  data/cache/market_context.json
"""

import os
import sys
import json
import time
import math
import argparse
import statistics
import urllib.request
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:
    raise SystemExit("ERROR: pip install numpy")

API = "https://api.hyperliquid.xyz/info"
VP_CACHE = "data/cache/volume_profile.json"
LOCAL_HIST = "data/history/hl"
OUT_FILE = "data/cache/market_context.json"

DEFAULT_DAYS = 180
R2_WINDOW = 120           # на скольких днях считать долю рынка в токене
MIN_TOKENS = 10
PAUSE_S = 0.25


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def post(body, timeout=25):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def token_map():
    vp = load_json(VP_CACHE, {}) or {}
    return {s.upper(): (d.get("hl_name") or s)
            for s, d in (vp.get("tokens") or {}).items()}


def candles(coin, days):
    now = int(time.time() * 1000)
    raw = post({"type": "candleSnapshot",
                "req": {"coin": coin, "interval": "1d",
                        "startTime": now - days * 86400000, "endTime": now}})
    out = {}
    for c in raw or []:
        try:
            cl = float(c["c"])
        except (KeyError, TypeError, ValueError):
            continue
        if cl > 0:
            d = datetime.fromtimestamp(c["t"] / 1000, timezone.utc)
            out[d.strftime("%Y-%m-%d")] = cl
    return out


def gather(days):
    """Сначала пробуем локальную историю, иначе тянем с биржи."""
    tmap = token_map()
    if not tmap:
        return {}, "нет volume_profile.json"

    prices, src = {}, "hyperliquid (живой запрос)"
    local_ok = 0
    for sym in tmap:
        j = load_json(os.path.join(LOCAL_HIST, f"{sym}.json"))
        if j and len(j.get("candles") or []) > R2_WINDOW:
            prices[sym] = {r["date"]: r["c"] for r in j["candles"][-days:]}
            local_ok += 1
    if local_ok >= len(tmap) * 0.8:
        return prices, f"локальная история ({local_ok} токенов)"

    prices = {}
    for sym, coin in sorted(tmap.items()):
        try:
            p = candles(coin, days)
        except Exception:
            continue
        if len(p) > 30:
            prices[sym] = p
        time.sleep(PAUSE_S)
    return prices, src


def daily_returns(prices):
    dates = sorted({d for m in prices.values() for d in m})
    rets = {}
    for t, m in prices.items():
        r = {}
        for i in range(1, len(dates)):
            a, b = m.get(dates[i - 1]), m.get(dates[i])
            if a and b:
                x = b / a - 1
                if abs(x) < 1:
                    r[dates[i]] = x
        rets[t] = r
    return dates, rets


def market_index(rets, dates):
    mkt = {}
    for d in dates:
        vals = [r[d] for r in rets.values() if d in r]
        if len(vals) >= MIN_TOKENS:
            mkt[d] = statistics.median(vals)
    return mkt


def r2_share(rets, mkt):
    """Доля дисперсии токена, объяснимая рынком, на последних R2_WINDOW днях."""
    out = {}
    recent = sorted(mkt)[-R2_WINDOW:]
    for t, r in rets.items():
        pairs = [(mkt[d], r[d]) for d in recent if d in r]
        if len(pairs) < 60:
            continue
        x = np.array([a for a, _ in pairs])
        y = np.array([b for _, b in pairs])
        if x.std() == 0 or y.std() == 0:
            continue
        c = float(np.corrcoef(x, y)[0, 1])
        if not math.isnan(c):
            out[t] = round(c * c * 100, 1)
    return out


def synchrony(rets, mkt, dates, window=1):
    """Какая доля токенов шла в ту же сторону, что и рынок."""
    day = dates[-1]
    if window > 1:
        day = dates[-1]
    vals = {t: r.get(day) for t, r in rets.items()}
    vals = {t: v for t, v in vals.items() if v is not None}
    if len(vals) < MIN_TOKENS:
        return None
    m = mkt.get(day)
    if m is None:
        return None
    same = sum(1 for v in vals.values() if (v > 0) == (m > 0))
    up = sum(1 for v in vals.values() if v > 0)
    pct = same / len(vals) * 100
    if pct >= 85:
        state, text = "ОДНИМ КУСКОМ", "рынок идёт одним куском — персональные сигналы по токенам сейчас почти ничего не значат"
    elif pct >= 70:
        state, text = "ВЫСОКАЯ", "движение общее, вклад отдельного токена небольшой"
    elif pct >= 55:
        state, text = "СРЕДНЯЯ", "рынок тянет, но токены расходятся"
    else:
        state, text = "НИЗКАЯ", "токены разошлись — выбор актива сегодня имеет смысл"
    return {"date": day, "pct_with_market": round(pct, 1),
            "tokens": len(vals), "up": up, "down": len(vals) - up,
            "market_move_pct": round(m * 100, 2),
            "state": state, "text_ru": text}


def breadth(prices, dates):
    """Сколько токенов выше своей середины за 30 дней."""
    day = dates[-1]
    above = total = 0
    for t, m in prices.items():
        hist = [m[d] for d in dates[-31:] if d in m]
        if len(hist) < 20 or day not in m:
            continue
        total += 1
        if m[day] > statistics.median(hist):
            above += 1
    if not total:
        return None
    pct = above / total * 100
    return {"above_30d_median": above, "tokens": total,
            "pct": round(pct, 1),
            "text_ru": ("ВЕСЬ рынок выше своей месячной середины — так широко "
                        "бывает редко, обычно ближе к концу движения, а не к началу"
                        if pct >= 95 else
                        "почти весь рынок выше своей месячной середины"
                        if pct >= 75 else
                        "большинство выше месячной середины" if pct >= 55 else
                        "большинство ниже месячной середины" if pct >= 25 else
                        "почти весь рынок ниже своей месячной середины")}


def funding_now():
    """Один запрос отдаёт фандинг и открытый интерес сразу по всем монетам."""
    try:
        meta, ctxs = post({"type": "metaAndAssetCtxs"})
    except Exception as e:
        return None, str(e)[:100]
    rows = []
    for u, c in zip(meta.get("universe", []), ctxs):
        try:
            rows.append({"coin": u["name"],
                         "annual_pct": float(c["funding"]) * 24 * 365 * 100,
                         "oi": float(c.get("openInterest") or 0),
                         "px": float(c.get("markPx") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None, "пустой ответ"
    med = statistics.median([r["annual_pct"] for r in rows])
    longs = sum(1 for r in rows if r["annual_pct"] > 0)
    if med >= 20:
        skew = "толпа сильно в лонге"
    elif med >= 5:
        skew = "толпа в лонге"
    elif med > -5:
        skew = "перекоса нет"
    elif med > -20:
        skew = "толпа в шорте"
    else:
        skew = "толпа сильно в шорте"
    return {
        "median_annual_pct": round(med, 2),
        "coins": len(rows),
        "positive_share_pct": round(longs / len(rows) * 100, 1),
        "oi_total_usd": round(sum(r["oi"] * r["px"] for r in rows)),
        "skew_ru": skew,
        "not_a_forecast": "связь с будущей доходностью измерена: "
                          "корреляция +0.03 / +0.09 / -0.05 на 7/14/28 днях. "
                          "Это контекст, а не прогноз",
    }, None


def main(days):
    prices, src = gather(days)
    if not prices:
        print(f"  Нет данных о ценах ({src})")
        return 1

    dates, rets = daily_returns(prices)
    mkt = market_index(rets, dates)
    if not mkt:
        print("  Не хватило токенов для индекса рынка")
        return 1

    shares = r2_share(rets, mkt)
    sync = synchrony(rets, mkt, sorted(mkt))
    brd = breadth(prices, dates)
    fund, ferr = funding_now()

    print("=== Контекст рынка ===\n")
    print(f"  Источник цен: {src} · токенов {len(prices)}\n")

    if sync:
        print(f"  Синхронность: {sync['pct_with_market']:.0f}% токенов идут "
              f"с рынком ({sync['state']})")
        print(f"    {sync['up']} вверх · {sync['down']} вниз · "
              f"рынок {sync['market_move_pct']:+.2f}%")
        print(f"    {sync['text_ru']}\n")
    if brd:
        print(f"  Ширина: {brd['above_30d_median']} из {brd['tokens']} выше "
              f"месячной середины ({brd['pct']:.0f}%)")
        print(f"    {brd['text_ru']}\n")
    if fund:
        print(f"  Фандинг: медиана {fund['median_annual_pct']:+.1f}% годовых — "
              f"{fund['skew_ru']}")
        print(f"    открытый интерес всего "
              f"${fund['oi_total_usd'] / 1e9:.1f} млрд по {fund['coins']} монетам")
        print(f"    ЭТО КОНТЕКСТ, НЕ ПРОГНОЗ\n")
    elif ferr:
        print(f"  Фандинг недоступен: {ferr}\n")

    if shares:
        med = statistics.median(list(shares.values()))
        print(f"  Доля рынка в движении токена · медиана {med:.0f}%")
        top = sorted(shares.items(), key=lambda z: -z[1])
        print(f"    сильнее всех привязаны: " +
              ", ".join(f"{t} {v:.0f}%" for t, v in top[:4]))
        print(f"    живут своей жизнью:     " +
              ", ".join(f"{t} {v:.0f}%" for t, v in top[-4:]))

    # разложение последнего движения по каждому токену
    day = sorted(mkt)[-1]
    per_token = {}
    for t, r in rets.items():
        if day not in r or t not in shares:
            continue
        move = r[day] * 100
        share = shares[t] / 100
        per_token[t] = {
            "market_share_pct": shares[t],
            "move_pct": round(move, 2),
            "market_part_pct": round(mkt[day] * 100, 2),
            "own_part_pct": round(move - mkt[day] * 100, 2),
            "text_ru": (f"из {move:+.2f}% сегодня рынок дал "
                        f"{mkt[day] * 100:+.2f}%, сам токен "
                        f"{move - mkt[day] * 100:+.2f}%"),
        }
        _ = share

    os.makedirs("data/cache", exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": f"hyperliquid · {src}",
            "cost": "free · 0 credits",
            "measured": "медианно 57% дневного движения токена — это рынок "
                        "(42 токена, 5.5 лет)",
            "synchrony": sync,
            "breadth": brd,
            "funding": fund,
            "market_share_of_movement": shares,
            "tokens": per_token,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n✓ {OUT_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    a = ap.parse_args()
    sys.exit(main(a.days))