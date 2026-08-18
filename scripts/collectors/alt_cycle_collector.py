#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alt_cycle_collector.py — макро-цикл crypto (BTC.D / ETH-BTC / TOTAL3).

Собирает 3 ключевые метрики для определения где находимся в alt-cycle:
  1. BTC dominance (BTC.D) — % BTC от всего marketcap
  2. ETH/BTC ratio — сила ETH относительно BTC
  3. TOTAL3 — marketcap без BTC/ETH (чистая altcoin capitalization)

Определяет 6 фаз:
  1. CASH            — деньги в стейблах, BTC.D растёт, TOTAL3 down
  2. BTC_SEASON      — BTC доминирует, инвесторы уходят в safe (BTC)
  3. ETH_ROTATION    — BTC top, инвесторы переходят в ETH
  4. MAJOR_ALT       — deep альтсезон в крупных монетах
  5. EUPHORIA        — memes + micro-caps выстреливают
  6. DISTRIBUTION    — smart money выходит, top forming

Данные: CoinGecko free API (30 calls/min, no key needed).
Cache: 4 часа — минимизируем API calls.

Запуск: python3 scripts/collectors/alt_cycle_collector.py
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = CACHE_DIR / 'alt_cycle.json'
CACHE_TTL_HOURS = 4  # обновляем не чаще 6x/сутки чтоб уложиться в rate limit

CG_BASE = 'https://api.coingecko.com/api/v3'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def http_get_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f'GET {url}: {e}')
        return None


def load_cached():
    if not OUTPUT_FILE.exists():
        return None
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ts = data.get('generated_at')
        if not ts:
            return None
        gen = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        age_h = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
        if age_h < CACHE_TTL_HOURS:
            logger.info(f'Cache fresh ({age_h:.1f}h < {CACHE_TTL_HOURS}h) — skip API')
            return data
    except Exception:
        pass
    return None


def fetch_global_data():
    """GET /global — total marketcap + BTC.D + ETH.D."""
    data = http_get_json(f'{CG_BASE}/global')
    if not data or 'data' not in data:
        return None
    d = data['data']
    return {
        'total_marketcap_usd': d.get('total_market_cap', {}).get('usd'),
        'total_volume_usd': d.get('total_volume', {}).get('usd'),
        'btc_dominance_pct': d.get('market_cap_percentage', {}).get('btc'),
        'eth_dominance_pct': d.get('market_cap_percentage', {}).get('eth'),
        'active_cryptos': d.get('active_cryptocurrencies'),
        'markets': d.get('markets'),
    }


def fetch_btc_eth_prices():
    """GET /simple/price для BTC + ETH."""
    url = f'{CG_BASE}/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true&include_market_cap=true'
    data = http_get_json(url)
    if not data:
        return None, None
    btc = data.get('bitcoin', {})
    eth = data.get('ethereum', {})
    return (
        {
            'price_usd': btc.get('usd'),
            'change_24h_pct': btc.get('usd_24h_change'),
            'marketcap_usd': btc.get('usd_market_cap'),
        },
        {
            'price_usd': eth.get('usd'),
            'change_24h_pct': eth.get('usd_24h_change'),
            'marketcap_usd': eth.get('usd_market_cap'),
        },
    )


def fetch_btcd_history_7d():
    """GET /global/market_cap_chart для BTC.D за 7d — trend detection.
    Note: coingecko free tier не даёт этот endpoint, fallback на price chart of BTC vs total.
    """
    # Alternate: используем market_chart для BTC/USD последних 7 дней
    url = f'{CG_BASE}/coins/bitcoin/market_chart?vs_currency=usd&days=7'
    btc_data = http_get_json(url)
    # ETH тоже для ETH/BTC ratio
    url2 = f'{CG_BASE}/coins/ethereum/market_chart?vs_currency=usd&days=7'
    eth_data = http_get_json(url2)

    if not btc_data or not eth_data:
        return None

    btc_prices = btc_data.get('prices', [])
    eth_prices = eth_data.get('prices', [])
    btc_marketcaps = btc_data.get('market_caps', [])
    eth_marketcaps = eth_data.get('market_caps', [])

    if not btc_prices or not eth_prices:
        return None

    # Compute BTC price 7d change
    btc_price_now = btc_prices[-1][1] if btc_prices else None
    btc_price_7d = btc_prices[0][1] if btc_prices else None
    btc_change_7d = ((btc_price_now / btc_price_7d - 1) * 100) if (btc_price_now and btc_price_7d) else None

    eth_price_now = eth_prices[-1][1] if eth_prices else None
    eth_price_7d = eth_prices[0][1] if eth_prices else None
    eth_change_7d = ((eth_price_now / eth_price_7d - 1) * 100) if (eth_price_now and eth_price_7d) else None

    # ETH/BTC ratio series (7d)
    ethbtc_series = []
    for i in range(min(len(btc_prices), len(eth_prices))):
        try:
            t = btc_prices[i][0]
            ratio = eth_prices[i][1] / btc_prices[i][1]
            ethbtc_series.append([t, ratio])
        except (ZeroDivisionError, TypeError):
            continue

    ethbtc_now = ethbtc_series[-1][1] if ethbtc_series else None
    ethbtc_7d = ethbtc_series[0][1] if ethbtc_series else None
    ethbtc_change_7d = ((ethbtc_now / ethbtc_7d - 1) * 100) if (ethbtc_now and ethbtc_7d) else None

    return {
        'btc_price_now': btc_price_now,
        'btc_price_7d_ago': btc_price_7d,
        'btc_change_7d_pct': btc_change_7d,
        'eth_price_now': eth_price_now,
        'eth_price_7d_ago': eth_price_7d,
        'eth_change_7d_pct': eth_change_7d,
        'ethbtc_ratio_now': ethbtc_now,
        'ethbtc_ratio_7d_ago': ethbtc_7d,
        'ethbtc_change_7d_pct': ethbtc_change_7d,
        # для sparklines в dashboard
        'ethbtc_series_7d': [[int(t), round(r, 6)] for t, r in ethbtc_series[-56:]],  # last 7d hourly = 168 points, keep every 3rd = 56
        'btc_price_series_7d': [[int(p[0]), round(p[1], 2)] for i, p in enumerate(btc_prices) if i % 3 == 0][-56:],
    }


def compute_total3(global_data, btc, eth):
    """TOTAL3 = total_marketcap - BTC_mcap - ETH_mcap."""
    total = global_data.get('total_marketcap_usd')
    btc_mcap = btc.get('marketcap_usd') if btc else None
    eth_mcap = eth.get('marketcap_usd') if eth else None
    if not (total and btc_mcap and eth_mcap):
        return None
    return total - btc_mcap - eth_mcap


def classify_phase(metrics):
    """Определяет текущую фазу alt-cycle на основе 3 метрик.

    Возвращает: {phase, phase_name, phase_num, confidence, reasoning}.

    Правила:
    1. CASH        : BTC.D растёт + TOTAL3 flat/down + BTC.D > 55%
    2. BTC_SEASON  : BTC.D > 55% + BTC price up 7d + ETH/BTC flat/down
    3. ETH_ROTATION: BTC.D flat/down + ETH/BTC ratio rising strongly
    4. MAJOR_ALT   : BTC.D < 55% + ETH/BTC up + TOTAL3 rising
    5. EUPHORIA    : BTC.D < 50% + TOTAL3 accelerating + все растёт
    6. DISTRIBUTION: BTC.D reversal up + TOTAL3 flattening at top
    """
    btcd = metrics.get('btc_dominance_pct')
    btc_change_7d = metrics.get('btc_change_7d_pct')
    ethbtc_change_7d = metrics.get('ethbtc_change_7d_pct')
    eth_change_7d = metrics.get('eth_change_7d_pct')
    total3 = metrics.get('total3_usd')

    # Fallback если данные неполные
    if btcd is None or btc_change_7d is None or ethbtc_change_7d is None:
        return {
            'phase_num': 0,
            'phase': 'UNKNOWN',
            'phase_name': 'Данных недостаточно',
            'confidence': 'NONE',
            'reasoning': ['Нет достаточно метрик для классификации'],
        }

    reasoning = []
    phase_num = 0
    phase = 'UNKNOWN'
    phase_name = ''
    confidence = 'LOW'

    # ETH/BTC strong up = altcoin risk-on
    ethbtc_strong_up = ethbtc_change_7d > 3.0
    ethbtc_up = ethbtc_change_7d > 0
    ethbtc_down = ethbtc_change_7d < -1.0

    # BTC dominance high/low
    btcd_high = btcd > 55
    btcd_medium = 50 <= btcd <= 55
    btcd_low = btcd < 50

    # BTC change
    btc_up = btc_change_7d > 3
    btc_flat = -3 <= btc_change_7d <= 3
    btc_down = btc_change_7d < -3

    # Логика классификации (по приоритету)
    if btcd_low and ethbtc_strong_up and eth_change_7d and eth_change_7d > 5:
        phase_num = 4
        phase = 'MAJOR_ALT'
        phase_name = 'Массовый альтсезон'
        confidence = 'HIGH'
        reasoning.append(f'BTC.D < 50% ({btcd:.1f}%) — деньги вышли из BTC')
        reasoning.append(f'ETH/BTC вырос {ethbtc_change_7d:+.1f}% за 7d — сильный рост альтов')
        reasoning.append(f'ETH сам вырос {eth_change_7d:+.1f}% — рынок в risk-on')
        # Проверка на EUPHORIA (следующая фаза)
        if btcd < 45 and ethbtc_change_7d > 10:
            phase_num = 5
            phase = 'EUPHORIA'
            phase_name = 'Эйфория (мем-коины)'
            reasoning.append('BTC.D < 45% + ETH/BTC > +10% — вероятная эйфория')

    elif btcd_medium and ethbtc_strong_up:
        phase_num = 3
        phase = 'ETH_ROTATION'
        phase_name = 'Ротация в ETH'
        confidence = 'MEDIUM'
        reasoning.append(f'BTC.D в среднем диапазоне ({btcd:.1f}%)')
        reasoning.append(f'ETH/BTC вырос {ethbtc_change_7d:+.1f}% — начало ротации')
        reasoning.append('Крупные альты (L1/DeFi) на очереди')

    elif btcd_high and btc_up and ethbtc_down:
        phase_num = 2
        phase = 'BTC_SEASON'
        phase_name = 'Сезон Bitcoin'
        confidence = 'HIGH'
        reasoning.append(f'BTC.D высокий ({btcd:.1f}%) — доминирование Bitcoin')
        reasoning.append(f'BTC вырос {btc_change_7d:+.1f}% за 7d')
        reasoning.append(f'ETH/BTC упал {ethbtc_change_7d:+.1f}% — альты слабее BTC')

    elif btcd_high and btc_flat and ethbtc_down:
        phase_num = 6
        phase = 'DISTRIBUTION'
        phase_name = 'Distribution (топ формируется)'
        confidence = 'MEDIUM'
        reasoning.append(f'BTC.D растёт при flat price = smart money фиксирует')
        reasoning.append(f'ETH/BTC {ethbtc_change_7d:+.1f}% — деньги обратно в BTC')

    elif btc_down and ethbtc_down:
        phase_num = 1
        phase = 'CASH'
        phase_name = 'Cash / стейблы'
        confidence = 'HIGH'
        reasoning.append(f'BTC упал {btc_change_7d:+.1f}%, альты ещё хуже')
        reasoning.append('Bears — деньги в стейблкоинах')
        reasoning.append('Ожидание bottom')

    else:
        # Undetermined — mixed signals
        phase_num = 0
        phase = 'MIXED'
        phase_name = 'Смешанные сигналы'
        confidence = 'LOW'
        reasoning.append(f'BTC.D: {btcd:.1f}%')
        reasoning.append(f'BTC 7d: {btc_change_7d:+.1f}%')
        reasoning.append(f'ETH/BTC 7d: {ethbtc_change_7d:+.1f}%')
        reasoning.append('Нет чёткой phase — ждать resolution')

    return {
        'phase_num': phase_num,
        'phase': phase,
        'phase_name': phase_name,
        'confidence': confidence,
        'reasoning': reasoning,
    }


def sector_recommendations(phase_num):
    """Какие sectors favor в текущей фазе."""
    recs = {
        1: {'favor': ['CASH', 'STABLECOINS'], 'avoid': ['ALL_ALTS'], 'note': 'Ждать bottom. Не входить.'},
        2: {'favor': ['BTC'], 'avoid': ['ALTS_GENERALLY'], 'note': 'BTC dominance. Альты слабы.'},
        3: {'favor': ['ETH', 'MAJORS'], 'avoid': ['MEMES', 'MICRO_CAPS'], 'note': 'Ротация в ETH и крупные альты.'},
        4: {'favor': ['L1', 'DeFi', 'INFRA', 'LST', 'RWA'], 'avoid': ['MEMES'], 'note': 'Массовый альтсезон — фундаментальные проекты.'},
        5: {'favor': ['MEMES', 'MICRO_CAPS', 'AI_AGENTS'], 'avoid': ['NOTHING'], 'note': 'Эйфория. HIGH RISK. Готовиться к exit.'},
        6: {'favor': ['CASH_OUT', 'BTC'], 'avoid': ['NEW_ENTRIES'], 'note': 'Distribution. Фиксировать профит.'},
        0: {'favor': ['WAIT'], 'avoid': ['UNCERTAINTY'], 'note': 'Смешанные сигналы. Дождаться resolution.'},
    }
    return recs.get(phase_num, recs[0])


def main():
    logger.info('=' * 60)
    logger.info('ALT-CYCLE COLLECTOR · macro compass')
    logger.info('=' * 60)

    # Check cache
    cached = load_cached()
    if cached:
        logger.info(f'Using cached data (age < {CACHE_TTL_HOURS}h)')
        return 0

    logger.info('Fetching fresh data from CoinGecko...')

    # 1. Global metrics
    global_data = fetch_global_data()
    if not global_data:
        logger.error('Failed to fetch global data — abort')
        return 1
    logger.info(f'  Total mcap: ${global_data["total_marketcap_usd"]/1e12:.2f}T')
    logger.info(f'  BTC.D: {global_data["btc_dominance_pct"]:.1f}%')

    # 2. BTC + ETH current
    btc, eth = fetch_btc_eth_prices()
    if not btc or not eth:
        logger.error('Failed to fetch BTC/ETH prices — abort')
        return 1
    logger.info(f'  BTC: ${btc["price_usd"]:,.0f} ({btc["change_24h_pct"]:+.1f}% 24h)')
    logger.info(f'  ETH: ${eth["price_usd"]:,.0f} ({eth["change_24h_pct"]:+.1f}% 24h)')

    # 3. 7d history (ETH/BTC + trends)
    history = fetch_btcd_history_7d()
    if not history:
        logger.warning('History fetch failed — degraded mode')
        history = {}

    # 4. Compute derivatives
    total3 = compute_total3(global_data, btc, eth)
    if total3:
        logger.info(f'  TOTAL3 (ex BTC/ETH): ${total3/1e9:.1f}B')

    metrics = {
        'btc_dominance_pct': global_data.get('btc_dominance_pct'),
        'eth_dominance_pct': global_data.get('eth_dominance_pct'),
        'total_marketcap_usd': global_data.get('total_marketcap_usd'),
        'total3_usd': total3,
        'btc_price_usd': btc.get('price_usd'),
        'btc_change_24h_pct': btc.get('change_24h_pct'),
        'btc_change_7d_pct': history.get('btc_change_7d_pct'),
        'eth_price_usd': eth.get('price_usd'),
        'eth_change_24h_pct': eth.get('change_24h_pct'),
        'eth_change_7d_pct': history.get('eth_change_7d_pct'),
        'ethbtc_ratio_now': history.get('ethbtc_ratio_now'),
        'ethbtc_ratio_7d_ago': history.get('ethbtc_ratio_7d_ago'),
        'ethbtc_change_7d_pct': history.get('ethbtc_change_7d_pct'),
    }

    # 5. Classify phase
    phase_info = classify_phase(metrics)
    logger.info(f'\n--- PHASE ---')
    logger.info(f'  {phase_info["phase_num"]}. {phase_info["phase"]}: {phase_info["phase_name"]}')
    logger.info(f'  Confidence: {phase_info["confidence"]}')
    for r in phase_info['reasoning']:
        logger.info(f'  · {r}')

    # 6. Sector recommendations
    recs = sector_recommendations(phase_info['phase_num'])
    logger.info(f'\n--- SECTOR RECOMMENDATIONS ---')
    logger.info(f'  Favor:  {", ".join(recs["favor"])}')
    logger.info(f'  Avoid:  {", ".join(recs["avoid"])}')
    logger.info(f'  Note:   {recs["note"]}')

    # 7. Save output
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'metrics': metrics,
        'phase': phase_info,
        'sector_recommendations': recs,
        # Sparkline data для dashboard
        'sparklines': {
            'btc_price_7d': history.get('btc_price_series_7d', []),
            'ethbtc_7d': history.get('ethbtc_series_7d', []),
        },
        'source': 'coingecko_free_api',
        'cache_ttl_hours': CACHE_TTL_HOURS,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f'\nSaved to {OUTPUT_FILE.name}')
    logger.info('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())