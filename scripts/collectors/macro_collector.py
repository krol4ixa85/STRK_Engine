#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macro_collector.py — собирает macro-level signals из surf.io + alternative.me.

Что собираем:
  1. BTC MVRV (surf.io on-chain indicator)
  2. BTC NUPL (surf.io on-chain indicator)
  3. BTC SOPR (surf.io on-chain indicator)
  4. BTC ETF flows 7d (surf.io ETF endpoint)
  5. Fear & Greed Index (alternative.me — FREE, no API key)
  6. BTC regime derivation (наш composite based on above)

Cost:
  - surf.io: ~5 credits/day (3 indicators + ETF)
  - alternative.me: FREE, no limit
  - Итого: ~150 credits/mo (в бюджете free tier)

Runs 1x/day через workflow (cron 12:00 MSK).

Outputs:
  - data/cache/macro_signals.json
"""
import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'

# =============================================================
# API endpoints
# =============================================================
SURF_API_BASE = 'https://api.asksurf.ai/v1'  # public API
SURF_API_KEY = os.getenv('SURF_API_KEY', '')  # optional; unauthenticated works with limits

ALT_FEAR_GREED_URL = 'https://api.alternative.me/fng/?limit=7'

# =============================================================
# Fear & Greed (free, no API key)
# =============================================================
def fetch_fear_greed():
    """Fetch F&G from alternative.me. No auth needed."""
    try:
        r = requests.get(ALT_FEAR_GREED_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        rows = data.get('data', [])
        if not rows:
            return None
        
        current = int(rows[0].get('value', 50))
        classification = rows[0].get('value_classification', 'Neutral')
        
        # Trend 7d
        values = [int(r.get('value', 50)) for r in rows[:7]]
        avg_7d = sum(values) / len(values)
        change_7d = current - values[-1] if len(values) >= 7 else 0
        
        return {
            'current': current,
            'classification': classification,
            'avg_7d': round(avg_7d, 1),
            'change_7d': change_7d,
            'history': [{'day': r.get('timestamp'), 'value': int(r.get('value', 50)), 'class': r.get('value_classification')} for r in rows[:7]]
        }
    except Exception as e:
        print(f'[F&G] Error: {e}')
        return None


# =============================================================
# Surf.io API — On-chain indicators
# =============================================================
def surf_get(endpoint, params=None):
    """Generic GET к surf.io API."""
    url = f'{SURF_API_BASE}/{endpoint}'
    headers = {}
    if SURF_API_KEY:
        headers['X-API-KEY'] = SURF_API_KEY
    
    try:
        r = requests.get(url, params=params or {}, headers=headers, timeout=30)
        if r.status_code == 402:
            print(f'[SURF] 402 Payment required — API key missing or budget exceeded')
            return None
        if r.status_code == 401:
            print(f'[SURF] 401 Unauthorized — API key required for {endpoint}')
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'[SURF {endpoint}] Error: {e}')
        return None


def fetch_btc_indicator(metric='mvrv'):
    """Fetch BTC on-chain indicator (mvrv, nupl, sopr, puell-multiple)."""
    data = surf_get('market/onchain-indicator', {
        'symbol': 'BTC',
        'metric': metric,
        'granularity': 'day',
    })
    if not data:
        return None
    
    points = data.get('data', [])
    if not points:
        return None
    
    # Latest value
    latest = points[0].get('value')
    ts = points[0].get('timestamp')
    
    # 30-day range
    values = [p.get('value') for p in points[:30] if p.get('value') is not None]
    if not values:
        return {'value': latest, 'timestamp': ts}
    
    return {
        'value': latest,
        'timestamp': ts,
        'min_30d': min(values),
        'max_30d': max(values),
        'avg_30d': sum(values) / len(values),
    }


def fetch_btc_etf_flow():
    """Fetch BTC ETF flow history — get last 7 days net."""
    data = surf_get('market/etf', {
        'symbol': 'BTC',
        'order': 'desc',
        'sort_by': 'timestamp',
    })
    if not data:
        return None
    
    points = data.get('data', [])
    if not points:
        return None
    
    # Sum flows for last 7 days
    total_7d = 0
    positive_days = 0
    negative_days = 0
    days_data = []
    
    for p in points[:7]:
        flow = p.get('flow_usd', 0) or 0
        total_7d += flow
        if flow > 0:
            positive_days += 1
        elif flow < 0:
            negative_days += 1
        days_data.append({
            'ts': p.get('timestamp'),
            'flow_usd': flow,
            'price': p.get('price_usd'),
        })
    
    # Determine signal
    if total_7d > 500_000_000:
        signal = 'STRONG_INFLOW'
    elif total_7d > 0:
        signal = 'NET_INFLOW'
    elif total_7d > -500_000_000:
        signal = 'NET_OUTFLOW'
    else:
        signal = 'STRONG_OUTFLOW'
    
    return {
        'total_7d_usd': round(total_7d, 0),
        'positive_days': positive_days,
        'negative_days': negative_days,
        'signal': signal,
        'last_7_days': days_data,
    }


# =============================================================
# Interpretation layer — derive composite regime
# =============================================================
def interpret_mvrv(mvrv):
    """MVRV interpretation."""
    if mvrv is None:
        return {'state': 'unknown', 'signal': 'NEUTRAL'}
    if mvrv < 1.0:
        return {'state': 'capitulation', 'signal': 'BULLISH', 'note': 'Ниже cost basis — исторически accumulation zone'}
    elif mvrv < 1.5:
        return {'state': 'accumulation', 'signal': 'BULLISH', 'note': 'Средняя зона, ближе к дну'}
    elif mvrv < 2.5:
        return {'state': 'neutral', 'signal': 'NEUTRAL', 'note': 'Normal zone'}
    elif mvrv < 3.5:
        return {'state': 'euphoria', 'signal': 'BEARISH', 'note': 'Euphoria — начать фиксировать'}
    else:
        return {'state': 'top', 'signal': 'STRONG_BEARISH', 'note': 'Cycle top — фиксировать сейчас'}


def interpret_fear_greed(fg):
    """F&G interpretation."""
    if fg is None:
        return {'state': 'unknown', 'signal': 'NEUTRAL'}
    v = fg['current']
    if v < 25:
        return {'state': 'extreme_fear', 'signal': 'BULLISH', 'note': 'Extreme fear — contrarian buy'}
    elif v < 47:
        return {'state': 'fear', 'signal': 'BULLISH', 'note': 'Fear — accumulation zone'}
    elif v < 55:
        return {'state': 'neutral', 'signal': 'NEUTRAL', 'note': 'Neutral'}
    elif v < 75:
        return {'state': 'greed', 'signal': 'BEARISH', 'note': 'Greed — начать осторожнее'}
    else:
        return {'state': 'extreme_greed', 'signal': 'STRONG_BEARISH', 'note': 'Extreme greed — фиксировать'}


def interpret_etf(etf):
    """ETF flow interpretation."""
    if etf is None:
        return {'state': 'unknown', 'signal': 'NEUTRAL'}
    signal = etf.get('signal', 'NEUTRAL')
    total = etf.get('total_7d_usd', 0)
    mapping = {
        'STRONG_INFLOW': ('STRONG_BULLISH', 'Институционалы активно покупают'),
        'NET_INFLOW': ('BULLISH', 'Институционалы покупают'),
        'NET_OUTFLOW': ('BEARISH', 'Институционалы продают'),
        'STRONG_OUTFLOW': ('STRONG_BEARISH', 'Институционалы активно продают'),
    }
    verdict, note = mapping.get(signal, ('NEUTRAL', ''))
    return {'state': signal.lower(), 'signal': verdict, 'note': f'{note} · 7d: ${total/1e6:+.0f}M'}


def compose_regime(interpretations):
    """Combine all interpretations to overall regime label."""
    signals = [i.get('signal', 'NEUTRAL') for i in interpretations.values() if i]
    bullish = sum(1 for s in signals if 'BULL' in s)
    bearish = sum(1 for s in signals if 'BEAR' in s)
    
    if bullish >= 3 and bearish == 0:
        return 'STRONG_ACCUMULATION_SETUP'
    elif bullish > bearish:
        return 'ACCUMULATION_BIAS'
    elif bearish > bullish:
        return 'DISTRIBUTION_BIAS'
    else:
        return 'NEUTRAL'


# =============================================================
# Main
# =============================================================
def main():
    print('=' * 70)
    print('MACRO SIGNALS COLLECTOR · surf.io + alternative.me')
    print('=' * 70)
    print(f'Run at: {datetime.now(timezone.utc).isoformat()}\n')
    
    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'sources': {},
        'interpretations': {},
        'overall': {},
    }
    
    # 1. Fear & Greed (free)
    print('[1/4] Fetching Fear & Greed Index...')
    fg = fetch_fear_greed()
    if fg:
        print(f'  ✓ Current: {fg["current"]} ({fg["classification"]}) · 7d avg: {fg["avg_7d"]}')
        result['sources']['fear_greed'] = fg
        result['interpretations']['fear_greed'] = interpret_fear_greed(fg)
    else:
        print(f'  ✗ Failed')
    
    # 2. BTC MVRV (surf.io)
    print('\n[2/4] Fetching BTC MVRV...')
    mvrv = fetch_btc_indicator('mvrv')
    if mvrv:
        print(f'  ✓ Current: {mvrv["value"]:.3f} · 30d range: {mvrv.get("min_30d", 0):.2f}-{mvrv.get("max_30d", 0):.2f}')
        result['sources']['btc_mvrv'] = mvrv
        result['interpretations']['btc_mvrv'] = interpret_mvrv(mvrv['value'])
    else:
        print(f'  ✗ Failed (может нужен API key)')
    
    # 3. BTC NUPL (surf.io)
    print('\n[3/4] Fetching BTC NUPL...')
    nupl = fetch_btc_indicator('nupl')
    if nupl:
        print(f'  ✓ NUPL: {nupl["value"]:.3f}')
        result['sources']['btc_nupl'] = nupl
    
    # 4. BTC ETF flows (surf.io)
    print('\n[4/4] Fetching BTC ETF flows...')
    etf = fetch_btc_etf_flow()
    if etf:
        total = etf['total_7d_usd']
        sign = '+' if total >= 0 else ''
        print(f'  ✓ 7d net flow: {sign}${total/1e6:.1f}M ({etf["signal"]})')
        print(f'    Positive days: {etf["positive_days"]}/7 · Negative: {etf["negative_days"]}/7')
        result['sources']['btc_etf_flow'] = etf
        result['interpretations']['btc_etf_flow'] = interpret_etf(etf)
    else:
        print(f'  ✗ Failed')
    
    # 5. Compose overall regime
    regime = compose_regime(result['interpretations'])
    result['overall'] = {
        'regime': regime,
        'summary': generate_summary(regime, result['interpretations']),
    }
    
    print(f'\n{"=" * 70}')
    print(f'COMPOSITE REGIME: {regime}')
    print(f'  {result["overall"]["summary"]}')
    print(f'{"=" * 70}')
    
    # Save
    output_path = CACHE_DIR / 'macro_signals.json'
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n✓ Saved: {output_path}')
    
    return 0


def generate_summary(regime, interpretations):
    """Human-readable summary."""
    signals = []
    for name, data in interpretations.items():
        if not data:
            continue
        note = data.get('note', '')
        if note:
            label = {
                'fear_greed': 'F&G',
                'btc_mvrv': 'MVRV',
                'btc_etf_flow': 'ETF',
            }.get(name, name)
            signals.append(f'{label}: {note}')
    return ' · '.join(signals) if signals else 'Insufficient data'


if __name__ == '__main__':
    sys.exit(main())
