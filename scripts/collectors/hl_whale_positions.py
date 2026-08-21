#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hl_whale_positions.py · v1.0 · 21.08.2026
STRK ENGINE · позиции крупных игроков Hyperliquid

ЗАЧЕМ
-----
Агрегат по перпам (funding, premium, OI) показывает перекос толпы,
но не отвечает на вопрос «а что делают те, кто реально двигает рынок».

Nansen отдаёт такое, но только внутри интерактивного чата — из GitHub
Actions его дёрнуть нельзя без платного x402. Поэтому watchlist строим
сами: hl_trade_collector с 21.08 копит публичные сделки, а в каждой
сделке HL отдаёт адреса ОБЕИХ сторон.

Дальше просто: ранжируем адреса по обороту, для верхних запрашиваем
clearinghouseState и получаем их настоящие открытые позиции.

ПОЧЕМУ ЭТО РАБОТАЕТ БЕЗ КЛЮЧЕЙ
------------------------------
Hyperliquid публикует состояние счёта любого адреса. Это не утечка,
а свойство биржи на блокчейне: позиции видны всем. Ключ нужен только
чтобы торговать, а чтобы смотреть — нет.

ЧТО СЧИТАЕМ
-----------
Для каждого отслеживаемого токена:

  whales_long / whales_short   сколько крупных стоит в каждую сторону
  long_usd / short_usd         суммарный размер позиций
  net_bias                     перевес в долларах и в процентах
  crowd_vs_whales              совпадает ли толпа с крупными

Последнее — главное. Когда premium показывает перекос толпы в лонги,
а крупные стоят в шортах, это расхождение важнее любого из двух
сигналов по отдельности.

ЧЕСТНО ОБ ОГРАНИЧЕНИЯХ
----------------------
Адрес с большим оборотом не обязательно умный. Маркетмейкер крутит
объём в обе стороны и его «позиция» ничего не предсказывает. Поэтому:

  отсекаем адреса с более чем MAX_POSITIONS открытыми позициями —
  это признак маркетмейкера, а не направленного трейдера;

  считаем только позиции по нашим токенам, а не весь их портфель;

  показываем число адресов рядом с суммой, чтобы было видно,
  когда весь «перевес» это один кошелёк.

ЗАПУСК
------
  python3 scripts/collectors/hl_whale_positions.py
  python3 scripts/collectors/hl_whale_positions.py --top 30

ВЫХОД
-----
  data/cache/hl_whale_positions.json
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
HISTORY = "data/history"
TRADES_LOG = os.path.join(HISTORY, "hl_trades.jsonl")
OUT_FILE = os.path.join(CACHE, "hl_whale_positions.json")

HL_ENDPOINT = "https://api.hyperliquid.xyz/info"

# Сколько верхних адресов проверять. Каждый — один запрос, лимитов нет,
# но и смысла брать хвост тоже нет: там розница.
DEFAULT_TOP_N = 40

# Окно, за которое считаем оборот адреса
LOOKBACK_DAYS = 7

# Минимальный оборот, ниже которого адрес не рассматриваем
MIN_VOLUME_USD = 20_000

# Больше этого числа одновременных позиций — почти наверняка
# маркетмейкер. Его «направление» не сигнал, а следствие котирования.
MAX_POSITIONS = 25

# Позиции меньше этой суммы игнорируем: у крупного счёта это пыль,
# а не выражение мнения
MIN_POSITION_USD = 5_000

# Токены, по которым сводим картину
TRACKED = [
    "BTC", "ETH", "SOL", "LINK", "STRK", "ETHFI",
    "MORPHO", "ONDO", "ARB", "AAVE", "OP", "PENDLE",
    "LDO", "CRV", "UNI", "ENA", "TAO", "SUI",
]

REQUEST_DELAY = 0.15


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def read_trades():
    recs = []
    try:
        with open(TRADES_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return recs


def rank_addresses(trades, lookback_days=LOOKBACK_DAYS):
    """Обороты адресов за окно. Обе стороны сделки считаются."""
    cutoff = int((datetime.now(timezone.utc) -
                  timedelta(days=lookback_days)).timestamp() * 1000)

    volume = defaultdict(float)
    count = defaultdict(int)
    coins = defaultdict(set)

    for r in trades:
        if r.get("ts", 0) < cutoff:
            continue
        usd = r.get("usd") or 0
        coin = r.get("coin")
        for side in ("buyer", "seller"):
            addr = r.get(side)
            if not addr:
                continue
            volume[addr] += usd
            count[addr] += 1
            if coin:
                coins[addr].add(coin)

    ranked = [
        {"address": a, "volume_usd": round(v), "trades": count[a],
         "coins": sorted(coins[a])}
        for a, v in volume.items() if v >= MIN_VOLUME_USD
    ]
    ranked.sort(key=lambda x: -x["volume_usd"])
    return ranked


def fetch_state(address):
    try:
        r = requests.post(
            HL_ENDPOINT,
            json={"type": "clearinghouseState", "user": address},
            timeout=15,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def parse_positions(state):
    """Извлекает позиции и отсекает пыль."""
    if not state:
        return None

    ms = state.get("marginSummary") or {}
    try:
        account_value = float(ms.get("accountValue", 0))
    except (TypeError, ValueError):
        account_value = 0.0

    raw = state.get("assetPositions") or []
    positions = []
    for ap in raw:
        p = ap.get("position") or {}
        coin = p.get("coin")
        if not coin:
            continue
        try:
            szi = float(p.get("szi", 0))
            value = abs(float(p.get("positionValue", 0)))
            entry = float(p.get("entryPx") or 0)
            pnl = float(p.get("unrealizedPnl", 0))
        except (TypeError, ValueError):
            continue
        if value < MIN_POSITION_USD:
            continue
        positions.append({
            "coin": coin,
            "side": "LONG" if szi > 0 else "SHORT",
            "value_usd": round(value),
            "entry_price": entry,
            "unrealized_pnl_usd": round(pnl),
        })

    return {
        "account_value_usd": round(account_value),
        "positions_total": len(raw),
        "positions_material": len(positions),
        "positions": positions,
    }


def is_market_maker(parsed):
    """
    Признак маркетмейкера — много одновременных позиций по разным
    активам. Такой адрес котирует рынок, а не выражает мнение,
    и его «перевес» ничего не предсказывает.
    """
    return (parsed or {}).get("positions_total", 0) > MAX_POSITIONS


def aggregate_by_token(whales):
    """Сводит позиции крупных по каждому токену."""
    agg = {}
    for token in TRACKED:
        longs, shorts = [], []
        for w in whales:
            for p in w.get("positions", []):
                if p["coin"] != token:
                    continue
                row = {"address": w["address"], "value_usd": p["value_usd"],
                       "entry_price": p["entry_price"],
                       "pnl_usd": p["unrealized_pnl_usd"]}
                (longs if p["side"] == "LONG" else shorts).append(row)

        long_usd = sum(x["value_usd"] for x in longs)
        short_usd = sum(x["value_usd"] for x in shorts)
        total = long_usd + short_usd

        if total == 0:
            agg[token] = {"status": "NO_POSITIONS",
                          "text_ru": "никто из отслеживаемых адресов не держит позицию"}
            continue

        net = long_usd - short_usd
        net_pct = net / total * 100

        # Перевес считаем по деньгам, но проверяем и по головам.
        # Один крупный лонг может перевесить четыре шорта — арифметически
        # верно, читается как ошибка. Такой случай называем прямо:
        # это мнение одного адреса, а не «крупных» вообще.
        n_long, n_short = len(longs), len(shorts)
        money_long = net > 0
        heads_long = n_long > n_short

        if abs(net_pct) < 20:
            bias = "BALANCED"
            ru = "крупные стоят по обе стороны примерно поровну"
        elif money_long != heads_long:
            # Деньги и головы спорят — перевес держится на одном-двух счетах
            big_side = "лонг" if money_long else "шорт"
            big_n = n_long if money_long else n_short
            other_n = n_short if money_long else n_long
            bias = "SINGLE_WHALE_LONG" if money_long else "SINGLE_WHALE_SHORT"
            ru = (f"перевес в {big_side} держат {big_n} адрес(а) против "
                  f"{other_n} с другой стороны — это мнение одного счёта, "
                  f"а не крупных в целом")
        elif money_long:
            bias = "WHALES_LONG"
            ru = (f"{n_long} адресов в лонгах на ${long_usd/1e6:.1f}M "
                  f"против {n_short} в шортах на ${short_usd/1e6:.1f}M")
        else:
            bias = "WHALES_SHORT"
            ru = (f"{n_short} адресов в шортах на ${short_usd/1e6:.1f}M "
                  f"против {n_long} в лонгах на ${long_usd/1e6:.1f}M")

        agg[token] = {
            "status": "OK",
            "whales_long": len(longs),
            "whales_short": len(shorts),
            "long_usd": long_usd,
            "short_usd": short_usd,
            "net_usd": net,
            "net_pct": round(net_pct, 1),
            "bias": bias,
            "text_ru": ru,
            # Сколько адресов делают этот перевес — если один,
            # то это мнение одного человека, а не крупных вообще
            "concentration_note": (
                "перевес делает один адрес" if max(len(longs), len(shorts)) == 1
                else None
            ),
            "top_long": sorted(longs, key=lambda x: -x["value_usd"])[:3],
            "top_short": sorted(shorts, key=lambda x: -x["value_usd"])[:3],
        }

    return agg


def compare_with_crowd(agg, perps):
    """
    Расхождение крупных и толпы. Premium показывает, куда наклонилась
    толпа; позиции показывают, где стоят крупные. Когда они смотрят
    в разные стороны, это сильнее любого из двух сигналов.
    """
    tokens = (perps or {}).get("tokens") or {}
    for token, a in agg.items():
        if a.get("status") != "OK":
            continue
        hl = tokens.get(token) or {}
        crowd = hl.get("hl_bias")
        prem = hl.get("premium_pct")
        if not crowd or crowd == "UNKNOWN":
            continue

        whales = a["bias"]
        a["crowd_bias"] = crowd
        a["crowd_premium_pct"] = prem

        if whales in ("BALANCED", "SINGLE_WHALE_LONG", "SINGLE_WHALE_SHORT"):
            # Перевес одного счёта не считаем позицией «крупных»:
            # сравнивать с настроением толпы тут нечего
            a["crowd_vs_whales"] = "NEUTRAL"
            continue

        crowd_long = crowd == "LONG_HEAVY"
        whales_long = whales == "WHALES_LONG"

        if crowd_long == whales_long:
            a["crowd_vs_whales"] = "ALIGNED"
            a["crowd_vs_whales_ru"] = (
                "толпа и крупные смотрят в одну сторону — движение подтверждено"
            )
        else:
            a["crowd_vs_whales"] = "DIVERGENT"
            side_crowd = "лонги" if crowd_long else "шорты"
            side_whales = "лонгах" if whales_long else "шортах"
            a["crowd_vs_whales_ru"] = (
                f"толпа набрала {side_crowd}, а крупные стоят в {side_whales} — "
                f"расхождение, обычно правы крупные"
            )

    return agg


def main(top_n=DEFAULT_TOP_N):
    print("=== HL Whale Positions v1.0 (публичный API, 0 кредитов) ===\n")

    trades = read_trades()
    if not trades:
        print("✗ Лог сделок пуст. Запусти hl_trades хотя бы раз.")
        sys.exit(0)

    ranked = rank_addresses(trades)
    print(f"  Сделок в логе: {len(trades)}")
    print(f"  Адресов с оборотом ≥${MIN_VOLUME_USD:,}: {len(ranked)}")

    if not ranked:
        print("\n  Пока мало данных. База копится с каждым прогоном hl_trades,")
        print("  через сутки-двое адресов станет достаточно.")
        out = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "status": "NOT_ENOUGH_DATA",
            "trades_in_log": len(trades),
            "note": "нужно больше сделок в логе",
        }
        os.makedirs(CACHE, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        return

    check = ranked[:top_n]
    print(f"  Проверяем позиции у топ-{len(check)}\n")

    whales, skipped_mm, empty = [], 0, 0
    for i, row in enumerate(check, 1):
        state = fetch_state(row["address"])
        time.sleep(REQUEST_DELAY)
        parsed = parse_positions(state)

        if not parsed or parsed["account_value_usd"] == 0:
            empty += 1
            continue

        if is_market_maker(parsed):
            skipped_mm += 1
            continue

        if not parsed["positions"]:
            empty += 1
            continue

        whales.append({
            "address": row["address"],
            "volume_usd": row["volume_usd"],
            "trades": row["trades"],
            **parsed,
        })

        a = row["address"]
        print(f"  {a[:10]}... счёт ${parsed['account_value_usd']:>10,} · "
              f"{parsed['positions_material']} позиций · "
              f"оборот ${row['volume_usd']:>9,}")

    print(f"\n  Отобрано направленных: {len(whales)}")
    print(f"  Отсеяно маркетмейкеров: {skipped_mm} · пустых счетов: {empty}")

    agg = aggregate_by_token(whales)
    perps = load_json(os.path.join(CACHE, "hl_perps.json"))
    agg = compare_with_crowd(agg, perps)

    with_pos = {t: a for t, a in agg.items() if a.get("status") == "OK"}
    if with_pos:
        print(f"\n  {'ТОКЕН':8}{'ПЕРЕВЕС':20}{'лонг':>14}{'шорт':>14}  расхождение")
        print("  " + "─" * 74)
        for t, a in sorted(with_pos.items(),
                           key=lambda x: -abs(x[1].get("net_usd", 0))):
            div = a.get("crowd_vs_whales", "")
            mark = "⚠ DIVERGENT" if div == "DIVERGENT" else (
                "согласны" if div == "ALIGNED" else "")
            l_m = a["long_usd"] / 1e6
            s_m = a["short_usd"] / 1e6
            print(f"  {t:8}{a['bias']:20}"
                  f"{a['whales_long']}шт/${l_m:.1f}M".rjust(14) +
                  f"{a['whales_short']}шт/${s_m:.1f}M".rjust(14) + f"  {mark}")

    divergent = [t for t, a in agg.items()
                 if a.get("crowd_vs_whales") == "DIVERGENT"]

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "source": "hyperliquid_public",
        "cost": "free · 0 credits",
        "method": ("watchlist построен из публичных сделок, накопленных "
                   "hl_trade_collector; позиции читаются через clearinghouseState"),
        "trades_in_log": len(trades),
        "addresses_ranked": len(ranked),
        "addresses_checked": len(check),
        "whales_directional": len(whales),
        "market_makers_skipped": skipped_mm,
        "filters": {
            "lookback_days": LOOKBACK_DAYS,
            "min_volume_usd": MIN_VOLUME_USD,
            "max_positions_for_directional": MAX_POSITIONS,
            "min_position_usd": MIN_POSITION_USD,
        },
        "caveat": ("Большой оборот не означает ум. Маркетмейкеры отсеяны "
                   "по числу одновременных позиций, но фильтр грубый. "
                   "Смотри на число адресов рядом с суммой."),
        "divergent_tokens": divergent,
        "by_token": agg,
        "whales": whales,
    }

    os.makedirs(CACHE, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    if divergent:
        print(f"\n  Расхождение толпы и крупных: {', '.join(divergent)}")
    print(f"\n✓ {OUT_FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    a = ap.parse_args()
    main(a.top)
