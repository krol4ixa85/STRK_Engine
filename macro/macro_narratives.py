"""
Macro Narratives Collector v1
=============================
Собирает macro data которая двигает crypto markets:
1. Fed Funds Rate (текущий + trajectory)
2. US10Y (10-year Treasury Yield)
3. DXY (Dollar Index)
4. VIX (Fear Index)
5. Gold price (safe haven demand)

Sources:
- FRED API (Federal Reserve, free, needs API key)
- yfinance (Yahoo Finance, no key needed)

Output: data/cache/macro_narratives.json

Requires (optional):
    FRED_API_KEY (для точных FED данных, free registration)
    
Fallback: yfinance для всех метрик если FRED недоступен
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    requests = None

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# FRED API series
# ============================================================
FRED_SERIES = {
    'fed_funds_rate': 'DFF',      # Federal Funds Effective Rate
    'us10y': 'DGS10',              # 10-Year Treasury Constant Maturity Rate
    'us2y': 'DGS2',                # 2-Year Treasury
    'cpi_yoy': 'CPIAUCSL',         # Consumer Price Index (YoY calc)
    'unemployment': 'UNRATE',       # Unemployment Rate
    'dxy': 'DTWEXBGS',             # US Dollar Index (Broad)
}

def fetch_fred_series(api_key, series_id, limit=30):
    """Fetch time series from FRED API."""
    if not requests:
        return None
    try:
        url = 'https://api.stlouisfed.org/fred/series/observations'
        params = {
            'series_id': series_id,
            'api_key': api_key,
            'file_type': 'json',
            'sort_order': 'desc',
            'limit': limit,
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        obs = data.get('observations', [])
        return [
            {'date': o['date'], 'value': float(o['value']) if o['value'] != '.' else None}
            for o in obs if o.get('value')
        ]
    except Exception as e:
        print(f'  ⚠ FRED {series_id}: {e}')
        return None

# ============================================================
# YFINANCE FALLBACK (no API key needed)
# ============================================================
def fetch_yfinance_ticker(ticker):
    """
    Fallback: fetch from Yahoo Finance (no key needed).
    Use their chart API directly.
    """
    if not requests:
        return None
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
        params = {
            'interval': '1d',
            'range': '3mo',
        }
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        result = data['chart']['result'][0]
        timestamps = result.get('timestamp', [])
        closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
        
        series = []
        for ts, close in zip(timestamps, closes):
            if close is not None:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                series.append({'date': dt.strftime('%Y-%m-%d'), 'value': close})
        return series[-30:]  # Last 30 days
    except Exception as e:
        print(f'  ⚠ yfinance {ticker}: {e}')
        return None

# ============================================================
# ANALYSIS · TRAJECTORY DETECTION
# ============================================================
def analyze_trajectory(series, lookback=30):
    """Determine trajectory: rising / falling / stable."""
    if not series or len(series) < 5:
        return {'direction': 'unknown', 'change_pct': 0, 'current': None}
    
    current = series[-1]['value'] if series[-1].get('value') else None
    past_idx = min(lookback - 1, len(series) - 1)
    past = series[-past_idx - 1]['value'] if past_idx >= 0 and series[-past_idx - 1].get('value') else None
    
    if current is None or past is None or past == 0:
        return {'direction': 'unknown', 'change_pct': 0, 'current': current}
    
    change_pct = round((current - past) / past * 100, 2)
    
    if abs(change_pct) < 1:
        direction = 'stable'
    elif change_pct > 0:
        direction = 'rising'
    else:
        direction = 'falling'
    
    return {
        'direction': direction,
        'change_pct': change_pct,
        'current': round(current, 4),
        'past_value': round(past, 4),
    }

# ============================================================
# MACRO REGIME DETECTION
# ============================================================
def classify_macro_regime(fed_rate, us10y, dxy, vix):
    """
    Determine current macro regime based on key indicators.
    Returns: risk_on / risk_off / mixed / uncertain
    """
    signals_bullish = 0  # for risk assets (crypto)
    signals_bearish = 0
    
    # Fed rate direction
    if fed_rate and fed_rate.get('direction') == 'falling':
        signals_bullish += 2  # Rate cuts = bullish for risk assets
    elif fed_rate and fed_rate.get('direction') == 'rising':
        signals_bearish += 2
    
    # US10Y direction
    if us10y and us10y.get('direction') == 'falling':
        signals_bullish += 1  # Lower yields = money into risk
    elif us10y and us10y.get('direction') == 'rising':
        signals_bearish += 1
    
    # DXY direction
    if dxy and dxy.get('direction') == 'falling':
        signals_bullish += 1  # Weak dollar = bullish crypto
    elif dxy and dxy.get('direction') == 'rising':
        signals_bearish += 1
    
    # VIX level (>25 = fear, <15 = complacency)
    if vix and vix.get('current'):
        if vix['current'] < 15:
            signals_bullish += 1
        elif vix['current'] > 25:
            signals_bearish += 1
    
    if signals_bullish >= signals_bearish + 2:
        return {
            'regime': 'RISK_ON',
            'label_ru': 'Risk-ON · deньги в риск',
            'layman_ru': 'Ставки снижаются, доллар слабеет, страха нет → капитал ищет доходность в crypto/акциях.',
            'action_ru': 'Bullish setup для crypto. Держать позиции, добавлять на подтверждении.',
            'confidence': 'high' if abs(signals_bullish - signals_bearish) >= 3 else 'medium',
        }
    elif signals_bearish >= signals_bullish + 2:
        return {
            'regime': 'RISK_OFF',
            'label_ru': 'Risk-OFF · деньги из риска',
            'layman_ru': 'Ставки высокие, доллар растёт, страх — капитал бежит в US Treasuries и cash.',
            'action_ru': 'Bearish setup. Сокращать risk exposure. STRK/alt под давлением.',
            'confidence': 'high' if abs(signals_bearish - signals_bullish) >= 3 else 'medium',
        }
    else:
        return {
            'regime': 'MIXED',
            'label_ru': 'Смешанные сигналы · нейтрально',
            'layman_ru': 'Часть сигналов bullish, часть bearish. Рынок в неопределённости.',
            'action_ru': 'Watch mode. Не увеличивать позиции. Wait for clarity.',
            'confidence': 'low',
        }

# ============================================================
# LAYMAN EXPLAINERS (for each metric)
# ============================================================
def explain_fed_rate(rate):
    """Layman explanation for current Fed rate + direction."""
    current = rate.get('current')
    direction = rate.get('direction')
    if current is None:
        return 'Нет данных'
    
    level = ''
    if current < 2:
        level = 'очень низкая (стимул экономики)'
    elif current < 4:
        level = 'низкая-средняя'
    elif current < 5.5:
        level = 'средняя-высокая (тормозит риск)'
    else:
        level = 'очень высокая (давление на все рынки)'
    
    dir_ru = {'rising': 'растёт ↑', 'falling': 'снижается ↓', 'stable': 'стабильна →', 'unknown': '?'}
    dir_str = dir_ru.get(direction, '?')
    
    impact = ''
    if direction == 'falling':
        impact = 'Bullish для crypto — cheap money возвращается'
    elif direction == 'rising':
        impact = 'Bearish для crypto — dollar strength давит на risk'
    else:
        impact = 'Нейтрально'
    
    return f'Fed Rate {current}% ({level}), {dir_str}. Impact: {impact}'

def explain_us10y(us10y):
    """Layman explanation for US10Y."""
    current = us10y.get('current')
    direction = us10y.get('direction')
    if current is None:
        return 'Нет данных'
    
    dir_ru = {'rising': 'растёт ↑', 'falling': 'снижается ↓', 'stable': 'стабильна →', 'unknown': '?'}
    dir_str = dir_ru.get(direction, '?')
    
    if current > 4.5:
        interpretation = 'высокая yield — Treasuries привлекательнее чем risk assets'
    elif current > 3.5:
        interpretation = 'средняя yield — balanced'
    else:
        interpretation = 'низкая yield — капитал ищет альтернативы, bullish для crypto'
    
    return f'US10Y {current}% ({interpretation}), {dir_str}'

# ============================================================
# MAIN
# ============================================================
def main():
    print('=== Macro Narratives Collector v1 ===\n')
    
    fred_key = os.getenv('FRED_API_KEY')
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'sources_used': [],
        'metrics': {},
    }
    
    # ==== FED FUNDS RATE ====
    print('1. Fed Funds Rate...')
    if fred_key:
        series = fetch_fred_series(fred_key, 'DFF')
        if series:
            output['sources_used'].append('fred:DFF')
    else:
        series = None
    
    if not series:
        # Fallback: no direct yfinance for Fed rate, use approximation
        print('  ⚠ No FRED key — Fed rate unavailable via yfinance')
        series = []
    
    if series:
        traj = analyze_trajectory(series[::-1])  # reverse for chronological
        output['metrics']['fed_rate'] = {
            'series': series[:10],
            **traj,
            'layman_ru': explain_fed_rate(traj),
        }
        print(f'  ✓ {traj["current"]}% ({traj["direction"]})')
    
    # ==== US10Y ====
    print('\n2. US 10-Year Treasury...')
    us10y_series = None
    if fred_key:
        us10y_series = fetch_fred_series(fred_key, 'DGS10')
        if us10y_series:
            output['sources_used'].append('fred:DGS10')
    if not us10y_series:
        us10y_series = fetch_yfinance_ticker('^TNX')
        if us10y_series:
            output['sources_used'].append('yfinance:^TNX')
    
    if us10y_series:
        traj = analyze_trajectory(us10y_series[::-1] if fred_key else us10y_series)
        output['metrics']['us10y'] = {
            'series': us10y_series[:10] if fred_key else us10y_series[-10:],
            **traj,
            'layman_ru': explain_us10y(traj),
        }
        print(f'  ✓ {traj["current"]}% ({traj["direction"]})')
    
    # ==== DXY (Dollar Index) ====
    print('\n3. Dollar Index (DXY)...')
    dxy_series = fetch_yfinance_ticker('DX-Y.NYB')  # DXY on Yahoo
    if not dxy_series:
        dxy_series = fetch_yfinance_ticker('DXY')
    if dxy_series:
        output['sources_used'].append('yfinance:DXY')
        traj = analyze_trajectory(dxy_series)
        interpretation = 'слабый доллар · bullish crypto' if traj['direction'] == 'falling' else \
                        'сильный доллар · bearish crypto' if traj['direction'] == 'rising' else 'стабилен'
        output['metrics']['dxy'] = {
            'series': dxy_series[-10:],
            **traj,
            'layman_ru': f'DXY {traj["current"]} ({interpretation}), {traj["change_pct"]}% за 30d',
        }
        print(f'  ✓ {traj["current"]} ({traj["direction"]}, {traj["change_pct"]}%)')
    
    # ==== VIX (Fear Index) ====
    print('\n4. VIX (Volatility Index)...')
    vix_series = fetch_yfinance_ticker('^VIX')
    if vix_series:
        output['sources_used'].append('yfinance:^VIX')
        traj = analyze_trajectory(vix_series)
        vix_level = 'complacency (bullish)' if traj['current'] < 15 else \
                   'moderate' if traj['current'] < 25 else \
                   'fear (bearish)'
        output['metrics']['vix'] = {
            'series': vix_series[-10:],
            **traj,
            'layman_ru': f'VIX {traj["current"]} ({vix_level})',
        }
        print(f'  ✓ {traj["current"]} ({vix_level})')
    
    # ==== GOLD ====
    print('\n5. Gold (safe haven)...')
    gold_series = fetch_yfinance_ticker('GC=F')  # Gold futures
    if gold_series:
        output['sources_used'].append('yfinance:GC=F')
        traj = analyze_trajectory(gold_series)
        output['metrics']['gold'] = {
            'series': gold_series[-10:],
            **traj,
            'layman_ru': f'Gold ${traj["current"]:.0f}/oz ({traj["change_pct"]}% за 30d)',
        }
        print(f'  ✓ ${traj["current"]:.0f} ({traj["change_pct"]}%)')
    
    # ==== MACRO REGIME CLASSIFICATION ====
    print('\n=== Macro Regime Classification ===')
    regime = classify_macro_regime(
        fed_rate=output['metrics'].get('fed_rate'),
        us10y=output['metrics'].get('us10y'),
        dxy=output['metrics'].get('dxy'),
        vix=output['metrics'].get('vix'),
    )
    output['regime'] = regime
    print(f'  Regime: {regime["regime"]} ({regime["confidence"]})')
    print(f'  {regime["layman_ru"]}')
    print(f'  Action: {regime["action_ru"]}')
    
    # ==== SAVE ====
    output_path = CACHE_DIR / 'macro_narratives.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f'\n✓ Written: {output_path}')

if __name__ == '__main__':
    main()
