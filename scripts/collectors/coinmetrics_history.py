#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coinmetrics_history.py · v1.1 · 22.08.2026
STRK ENGINE · длинная история ончейн-метрик

ЗАЧЕМ
-----
Главная проблема движка не в правилах, а в выборке. Dune даёт 26 недель
на токен. На такой истории deflated Sharpe не проходит НИКОГДА, сколько
правила ни улучшай: чтобы отличить преимущество от случайности, нужны
сотни наблюдений, а не десятки.

Coin Metrics Community отдаёт ежедневные метрики без ключа:
  BTC  с 2009 года
  ETH  с 2015
  LINK с сентября 2017
  UNI, AAVE с сентября 2020, LDO с декабря 2020

Это не «ещё один источник сигналов». Это то, на чём существующие
гипотезы можно наконец проверить.

ЧТО ЕСТЬ И ЧЕГО НЕТ · проверено запросами к API
-----------------------------------------------
Доступно на бесплатном тарифе:
  CapMVRVCur      MVRV напрямую
  CapMrktCurUSD   рыночная капитализация
  AdrActCnt       активные адреса
  TxCnt, TxTfrCnt транзакции
  SplyCur         обращающееся предложение
  PriceUSD        цена
  HashRate        только BTC
  FlowInExUSD     приток на биржи — ТОЛЬКО BTC и ETH
  FlowOutExUSD    отток с бирж — ТОЛЬКО BTC и ETH

НЕ доступно (403 на бесплатном тарифе):
  CapRealUSD      реализованная капитализация напрямую
  SOPR            spent output profit ratio
  NVTAdj

Из-за этого:
  MVRV        берётся напрямую ✓
  NUPL        выводится из MVRV: 1 - 1/MVRV ✓
  Realized    выводится: капитализация / MVRV ✓
  SOPR        ПОСЧИТАТЬ НЕЛЬЗЯ. Ему нужны данные о потраченных выходах,
              их на бесплатном тарифе нет. Выдумывать замену не будем.

И честно про ограничение: биржевых потоков по альтам здесь НЕТ. Значит
существующие правила по netflow из Dune этим источником не удлинить —
удлиняются только оценочные метрики (MVRV, NUPL) и активность сети.

ЛИЦЕНЗИЯ ДАННЫХ
---------------
Coin Metrics Community Data — CC BY-NC 4.0, некоммерческая.
Для собственных решений это нормально. Выкладывать саму выгрузку в
публичный репозиторий — вопрос к их лицензии, поэтому по умолчанию
скрипт пишет в data/history/coinmetrics/, а этот путь стоит добавить
в .gitignore. Считанные из неё индикаторы — производная работа,
её публикация тоже под вопросом; уточни у Coin Metrics, если решишь
показывать это публично.

ЛИМИТЫ
------
10 запросов за 6 секунд на IP, ключ не нужен. Скрипт держит паузу
0.7 секунды между запросами и переживает 429 с ожиданием.

ЗАПУСК
------
  python3 scripts/collectors/coinmetrics_history.py            # все доступные
  python3 scripts/collectors/coinmetrics_history.py --asset btc,eth,link
  python3 scripts/collectors/coinmetrics_history.py --since 2019-01-01

ВЫХОД
-----
  data/history/coinmetrics/<ASSET>.json
  data/cache/coinmetrics_coverage.json   что реально удалось получить
"""

import os
import sys
import json
import re
import time
import argparse
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

BASE = "https://community-api.coinmetrics.io/v4"
OUT_DIR = "data/history/coinmetrics"
COVERAGE_FILE = "data/cache/coinmetrics_coverage.json"

# Пауза между запросами. Лимит 10/6с — берём с запасом.
REQUEST_DELAY = 0.7
MAX_RETRIES = 4

# Токены твоей вселенной в написании Coin Metrics.
# Список сознательно шире, чем покрытие: чего нет, скрипт отбросит сам
# и напишет об этом, а не притворится что данные есть.
ASSETS = [
    "btc", "eth", "sol",
    "link", "uni", "aave", "ldo", "crv", "comp", "snx", "mkr",
    "arb", "op", "matic",
    "doge", "shib", "pepe",
    "fil", "grt", "sand", "axs", "eigen", "ondo", "ena", "pendle",
    "apt", "sui", "sei", "tia", "inj", "rndr", "fet", "tao",
]

# Метрики, которые пытаемся забрать. Реально доступный набор
# определяется по каталогу для каждого актива отдельно — иначе
# один недоступный идентификатор роняет весь запрос в 403.
WANTED = [
    "CapMVRVCur",
    "CapMrktCurUSD",
    "PriceUSD",
    "AdrActCnt",
    "TxCnt",
    "TxTfrCnt",
    "SplyCur",
    "FlowInExUSD",
    "FlowOutExUSD",
    "HashRate",
]


def api_get(path, params, attempt=1):
    """
    Запрос с разбором кодов. 403 здесь означает не «нет доступа
    вообще», а «в списке есть метрика не из бесплатного тарифа» —
    поэтому набор метрик мы выясняем заранее по каталогу.
    """
    url = f"{BASE}/{path}"
    try:
        r = requests.get(url, params=params, timeout=45)
    except Exception as e:
        if attempt <= MAX_RETRIES:
            time.sleep(2 * attempt)
            return api_get(path, params, attempt + 1)
        return None, f"сеть: {e}"

    if r.status_code == 200:
        return r.json(), None
    if r.status_code == 429:
        if attempt <= MAX_RETRIES:
            time.sleep(6 * attempt)
            return api_get(path, params, attempt + 1)
        return None, "429 — лимит запросов, не дождались"
    if r.status_code == 403:
        return None, "403 — метрика вне бесплатного тарифа"
    if r.status_code == 400:
        return None, f"400 — неверный запрос: {r.text[:120]}"
    if 500 <= r.status_code < 600:
        if attempt <= MAX_RETRIES:
            time.sleep(3 * attempt)
            return api_get(path, params, attempt + 1)
    return None, f"HTTP {r.status_code}: {r.text[:120]}"


# Сколько активов спрашиваем за один запрос. Меньше — больше запросов,
# но один неподдерживаемый актив портит меньший кусок.
CHUNK = 8

# Сколько раз готовы выбросить неподдерживаемый актив и повторить.
# Больше числа активов в чанке смысла не имеет.
UNSUPPORTED_RETRIES = CHUNK


def _unsupported_from_error(msg):
    """
    Coin Metrics на неизвестный актив отвечает 400 и НАЗЫВАЕТ его:
      Bad parameter 'assets'. Value 'tao' is not supported.
    Достаём имя, чтобы выбросить именно его, а не гадать.
    """
    m = re.search(r"Value '([^']+)' is not supported", msg or "")
    return m.group(1) if m else None


def discover(assets):
    """
    Что реально доступно по каждому активу.

    ФИКС 22.08.2026 · раньше все активы спрашивались одним запросом,
    и ОДИН неподдерживаемый ронял весь сбор:

        ✗ каталог недоступен: 400 — Value 'tao' is not supported

    Список из 33 тикеров я взял из вселенной движка, не сверив с тем,
    что Coin Metrics вообще знает. Это ровно та ошибка «угадал имена
    вместо того чтобы спросить», на которой в августе погорел ценовой
    коллектор Hive.

    Теперь: спрашиваем пачками, а на 400 выбрасываем именно тот актив,
    который назвал сервер, и повторяем. Неизвестные тикеры отсеиваются
    сами и попадают в отчёт, а сбор идёт дальше.
    """
    cov = {}
    unsupported = []

    for i in range(0, len(assets), CHUNK):
        chunk = list(assets[i:i + CHUNK])
        tries = 0
        while chunk and tries <= UNSUPPORTED_RETRIES:
            data, err = api_get("catalog-v2/asset-metrics",
                                {"assets": ",".join(chunk), "page_size": 1000})
            time.sleep(REQUEST_DELAY)
            if not err:
                for row in (data or {}).get("data", []):
                    asset = row.get("asset")
                    mets = {}
                    for m in row.get("metrics", []):
                        mid = m.get("metric")
                        if mid not in WANTED:
                            continue
                        freqs = m.get("frequencies") or []
                        daily = [f for f in freqs if f.get("frequency") == "1d"]
                        if daily:
                            mets[mid] = daily[0].get("min_time")
                    if mets:
                        cov[asset] = mets
                break

            bad = _unsupported_from_error(err)
            if bad and bad in chunk:
                chunk.remove(bad)
                unsupported.append(bad)
                tries += 1
                continue

            # Ошибка не про неизвестный актив — этот кусок пропускаем,
            # но остальные всё равно собираем.
            print(f"  ! чанк {i//CHUNK + 1}: {err}")
            break

    return cov, unsupported


def fetch_series(asset, metrics, since):
    """Вся история по активу, страницами."""
    rows = []
    params = {
        "assets": asset,
        "metrics": ",".join(metrics),
        "frequency": "1d",
        "start_time": since,
        "page_size": 10000,
    }
    path = "timeseries/asset-metrics"
    while True:
        data, err = api_get(path, params)
        time.sleep(REQUEST_DELAY)
        if err:
            return rows, err
        rows.extend((data or {}).get("data", []))
        nxt = (data or {}).get("next_page_url")
        if not nxt:
            return rows, None
        # next_page_token проще, чем разбирать URL
        tok = (data or {}).get("next_page_token")
        if not tok:
            return rows, None
        params = {"next_page_token": tok, "page_size": 10000}


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main(only=None, since="2015-01-01"):
    print("=== Coin Metrics Community · длинная история ===\n")
    assets = [a for a in ASSETS if not only or a in only]
    print(f"  Запрошено активов: {len(assets)}")

    cov, unsupported = discover(assets)
    if not cov:
        print("  ✗ ни по одному активу каталог не ответил")
        return 1
    print(f"  Покрытие есть по: {len(cov)} активам")
    if unsupported:
        print(f"  Coin Metrics не знает: {', '.join(sorted(unsupported))}")

    missing = [a for a in assets if a not in cov and a not in unsupported]
    if missing:
        print(f"  Знает, но нужных метрик нет: {', '.join(missing)}")
    print()

    os.makedirs(OUT_DIR, exist_ok=True)
    summary = {}
    ok_cnt = 0

    for asset in sorted(cov):
        mets = sorted(cov[asset])
        rows, err = fetch_series(asset, mets, since)
        if err:
            print(f"  {asset.upper():8} ✗ {err}")
            summary[asset] = {"status": "FAILED", "error": err}
            continue
        if not rows:
            print(f"  {asset.upper():8} ✗ пусто")
            summary[asset] = {"status": "NO_DATA"}
            continue

        series = []
        for r in rows:
            t = (r.get("time") or "")[:10]
            if not t:
                continue
            item = {"date": t}
            for m in mets:
                v = to_float(r.get(m))
                if v is not None:
                    item[m] = v
            if len(item) > 1:
                series.append(item)

        series.sort(key=lambda x: x["date"])
        payload = {
            "asset": asset,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source": "coinmetrics_community",
            "license": "CC BY-NC 4.0 · некоммерческое использование",
            "metrics": mets,
            "days": len(series),
            "first_date": series[0]["date"] if series else None,
            "last_date": series[-1]["date"] if series else None,
            "series": series,
        }
        with open(os.path.join(OUT_DIR, f"{asset.upper()}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        years = len(series) / 365.25
        print(f"  {asset.upper():8} {len(series):>5} дней ({years:>4.1f} лет) "
              f"с {payload['first_date']} · метрик {len(mets)}")
        summary[asset] = {
            "status": "OK", "days": len(series),
            "first_date": payload["first_date"],
            "last_date": payload["last_date"],
            "metrics": mets,
        }
        ok_cnt += 1

    os.makedirs("data/cache", exist_ok=True)
    with open(COVERAGE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "source": "coinmetrics_community",
            "assets_ok": ok_cnt,
            "assets_missing": missing,
            "assets_unsupported": unsupported,
            "note": "SOPR и реализованная капитализация напрямую недоступны "
                    "на бесплатном тарифе; MVRV есть, NUPL выводится из него",
            "by_asset": summary,
        }, f, indent=2, ensure_ascii=False)

    total_days = sum(v.get("days", 0) for v in summary.values() if v.get("days"))
    print(f"\n  Собрано: {ok_cnt} активов, {total_days} дней суммарно")
    print(f"\n✓ {OUT_DIR}/")
    print(f"✓ {COVERAGE_FILE}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", type=str, default="")
    ap.add_argument("--since", type=str, default="2015-01-01")
    a = ap.parse_args()
    only = [x.strip().lower() for x in a.asset.split(",") if x.strip()] or None
    sys.exit(main(only, a.since))
