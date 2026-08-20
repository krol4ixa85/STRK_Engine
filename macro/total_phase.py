"""
TOTAL / TOTAL3 Market Phase Indicator v1
=========================================
Determines global crypto market phase (accumulation/markup/distribution/markdown)
based on TOTAL and TOTAL3 market cap trends.

TOTAL = Total crypto market cap (all coins)
TOTAL3 = Total3 = TOTAL - BTC - ETH (altcoin market cap)
BTC.D = BTC dominance percentage

Wyckoff-style phase detection:
- ACCUMULATION: sideways after markdown, low volatility
- MARKUP: rising trend, higher highs
- DISTRIBUTION: sideways after markup, choppy
- MARKDOWN: falling trend, lower lows

Source: CoinGecko API (free, no key)
Output: data/cache/total_phase.json
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
# COINGECKO API
# ============================================================
def fetch_global_data():
    """Fetch current global crypto data from CoinGecko."""
    if not requests:
        return None
    try:
        url = 'https://api.coingecko.com/api/v3/global'
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get('data')
    except Exception as e:
        print(f'  ⚠ Global data: {e}')
        return None

def fetch_market_cap_history(days=90):
    """Historical total market cap."""
    if not requests:
        return None
    try:
        # Use global market cap endpoint
        url = f'https://api.coingecko.com/api/v3/coins/markets'
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': 1,  # Just BTC for reference
            'sparkline': 'false'
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  ⚠ Market cap: {e}')
        return None

def fetch_btc_market_cap_history(days=90):
    """BTC market cap history."""
    if not requests:
        return None
    try:
        url = 'https://api.coingecko.com/api/v3/coins/bitcoin/market_chart'
        params = {'vs_currency': 'usd', 'days': days, 'interval': 'daily'}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get('market_caps', [])
    except Exception as e:
        print(f'  ⚠ BTC history: {e}')
        return None

def fetch_eth_market_cap_history(days=90):
    """ETH market cap history."""
    if not requests:
        return None
    try:
        url = 'https://api.coingecko.com/api/v3/coins/ethereum/market_chart'
        params = {'vs_currency': 'usd', 'days': days, 'interval': 'daily'}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get('market_caps', [])
    except Exception as e:
        print(f'  ⚠ ETH history: {e}')
        return None

# ============================================================
# PHASE DETECTION (Wyckoff-style)
# ============================================================
def detect_phase(mcap_history, name='TOTAL'):
    """
    Detect market phase using price action.
    Returns: {phase, confidence, layman_ru, action_ru}
    """
    if not mcap_history or len(mcap_history) < 30:
        return {'phase': 'UNKNOWN', 'confidence': 'low', 'layman_ru': 'Недостаточно данных'}
    
    # Extract values (ignore timestamps for simple calc)
    values = [v[1] for v in mcap_history if isinstance(v, list) and len(v) == 2]
    
    if len(values) < 30:
        return {'phase': 'UNKNOWN', 'confidence': 'low', 'layman_ru': 'Недостаточно данных'}
    
    # Compute moving averages
    ma20 = sum(values[-20:]) / 20
    ma50 = sum(values[-50:]) / 50 if len(values) >= 50 else ma20
    current = values[-1]
    
    # 30d change
    past_30d = values[-30]
    change_30d = (current - past_30d) / past_30d * 100
    
    # Volatility (last 20 days std dev / mean)
    recent = values[-20:]
    mean_recent = sum(recent) / len(recent)
    variance = sum((v - mean_recent) ** 2 for v in recent) / len(recent)
    std_recent = variance ** 0.5
    volatility = std_recent / mean_recent * 100  # %
    
    # Trend detection
    if current > ma20 > ma50 and change_30d > 8:
        phase = 'MARKUP'
        confidence = 'high' if change_30d > 15 else 'medium'
    elif current < ma20 < ma50 and change_30d < -8:
        phase = 'MARKDOWN'
        confidence = 'high' if change_30d < -15 else 'medium'
    elif abs(change_30d) < 5 and volatility < 5:
        # Determine ACCUMULATION vs DISTRIBUTION by preceding trend
        pre_change = (past_30d - values[-60]) / values[-60] * 100 if len(values) >= 60 else 0
        if pre_change < -10:
            phase = 'ACCUMULATION'  # sideways after decline
            confidence = 'medium'
        elif pre_change > 10:
            phase = 'DISTRIBUTION'  # sideways after rise
            confidence = 'medium'
        else:
            phase = 'CONSOLIDATION'
            confidence = 'low'
    elif volatility > 8:
        phase = 'CHOPPY'
        confidence = 'medium'
    else:
        phase = 'TRANSITIONAL'
        confidence = 'low'
    
    # Layman explanations
    laymans = {
        'ACCUMULATION': f'{name} в фазе накопления: тихий боковик после падения, крупные тихо покупают. Bullish setup, но нужно ждать импульса.',
        'MARKUP': f'{name} в фазе роста: тренд вверх, +{change_30d:.1f}% за 30d. Быки controle рынок.',
        'DISTRIBUTION': f'{name} в фазе распределения: боковик после роста, крупные тихо продают retail. Осторожно с новыми позициями.',
        'MARKDOWN': f'{name} в фазе падения: тренд вниз, {change_30d:.1f}% за 30d. Медведи controle.',
        'CONSOLIDATION': f'{name} в фазе консолидации: {change_30d:.1f}% за 30d, боковик. Ждём breakout.',
        'CHOPPY': f'{name} в choppy market: высокая волатильность ({volatility:.1f}%). Trader\'s market.',
        'TRANSITIONAL': f'{name} в переходной фазе: {change_30d:.1f}% за 30d. Направление unclear.',
        'UNKNOWN': f'{name} phase unknown',
    }
    
    actions = {
        'ACCUMULATION': 'Позиции держать. Докупать на weakness. Watch для breakout сигнала.',
        'MARKUP': 'Продолжать держать longs. Trail stops. Не FOMO на pumps.',
        'DISTRIBUTION': 'Trim позиций 25-50%. Осторожно с новыми входами. Готовить hedges.',
        'MARKDOWN': 'Reduce risk. Stables buffer. Ждать capitulation signal для новых entries.',
        'CONSOLIDATION': 'Wait mode. Не увеличивать positions. Watch triggers.',
        'CHOPPY': 'Short-term trades. Не hold через choppy fase.',
        'TRANSITIONAL': 'Wait for clarity. Не делать big moves.',
        'UNKNOWN': 'Wait for data.',
    }
    
    return {
        'phase': phase,
        'confidence': confidence,
        'change_30d_pct': round(change_30d, 2),
        'volatility_pct': round(volatility, 2),
        'ma20': round(ma20 / 1e9, 2),  # in $B
        'ma50': round(ma50 / 1e9, 2),
        'current_mcap_b': round(current / 1e9, 2),
        'layman_ru': laymans[phase],
        'action_ru': actions[phase],
    }

# ============================================================
# MAIN
# ============================================================
def main():
    print('=== TOTAL / TOTAL3 Phase Indicator v1 ===\n')
    
    if not requests:
        print('❌ requests not installed')
        sys.exit(1)
    
    # Fetch current global
    print('1. Fetching global crypto data...')
    global_data = fetch_global_data()
    if not global_data:
        print('  ✗ Failed to fetch global data')
        sys.exit(1)
    
    total_mcap = global_data.get('total_market_cap', {}).get('usd', 0)
    btc_dom = global_data.get('market_cap_percentage', {}).get('btc', 0)
    eth_dom = global_data.get('market_cap_percentage', {}).get('eth', 0)
    total3_dom = 100 - btc_dom - eth_dom
    
    print(f'  ✓ TOTAL: ${total_mcap/1e12:.2f}T')
    print(f'  ✓ BTC.D: {btc_dom:.1f}% · ETH.D: {eth_dom:.1f}% · TOTAL3: {total3_dom:.1f}%')
    
    # Fetch history for phase detection
    print('\n2. Fetching BTC market cap history (90d)...')
    btc_history = fetch_btc_market_cap_history(90)
    print(f'  ✓ {len(btc_history) if btc_history else 0} data points')
    
    print('\n3. Fetching ETH market cap history (90d)...')
    eth_history = fetch_eth_market_cap_history(90)
    print(f'  ✓ {len(eth_history) if eth_history else 0} data points')
    
    # Compute TOTAL3 (approximate = using BTC + ETH decline vs TOTAL trend)
    # Since we don't have TOTAL history directly, use BTC as proxy for TOTAL
    # and reverse-calc TOTAL3 trend from dominance changes
    
    print('\n=== PHASE ANALYSIS ===')
    
    # BTC phase (proxy for TOTAL)
    btc_phase = detect_phase(btc_history, 'BTC')
    print(f'\nBTC: {btc_phase["phase"]} ({btc_phase["confidence"]})')
    print(f'  {btc_phase["layman_ru"]}')
    
    # ETH phase (proxy for ETH sector)
    eth_phase = detect_phase(eth_history, 'ETH')
    print(f'\nETH: {eth_phase["phase"]} ({eth_phase["confidence"]})')
    print(f'  {eth_phase["layman_ru"]}')
    
    # Aggregate signal
    if btc_phase['phase'] in ('MARKUP',) and eth_phase['phase'] in ('MARKUP', 'ACCUMULATION'):
        market_signal = 'BULL_MARKET'
        market_layman = 'BTC в markup, ETH догоняет. Это bull market. Alts могут запустить season.'
    elif btc_phase['phase'] in ('MARKDOWN',) and eth_phase['phase'] in ('MARKDOWN',):
        market_signal = 'BEAR_MARKET'
        market_layman = 'BTC и ETH в markdown. Bear market. Cash / stables лучше рискa.'
    elif btc_phase['phase'] == 'ACCUMULATION':
        market_signal = 'PRE_BULL_ACCUMULATION'
        market_layman = 'BTC в accumulation после markdown. Смарт-мани позиционируются к bull.'
    elif btc_phase['phase'] == 'DISTRIBUTION':
        market_signal = 'PRE_BEAR_DISTRIBUTION'
        market_layman = 'BTC в distribution — bull может подходить к концу. Осторожно с alts.'
    else:
        market_signal = 'MIXED'
        market_layman = 'Смешанные сигналы. Wait for clarity.'
    
    # STRK context (using our phase logic)
    strk_market_impact = ''
    if market_signal == 'BULL_MARKET':
        strk_market_impact = 'STRK как L2 бенефициар — high beta в bull market. Wait for Spring signal.'
    elif market_signal == 'PRE_BULL_ACCUMULATION':
        strk_market_impact = 'STRK может консолидироваться дольше — крупные ещё accumulate.'
    elif market_signal in ('BEAR_MARKET', 'PRE_BEAR_DISTRIBUTION'):
        strk_market_impact = 'STRK small-cap — упадёт сильнее в risk-off. Sizing careful.'
    else:
        strk_market_impact = 'STRK follows BTC direction — wait for confirm.'
    
    # BTC dominance analysis
    btc_dom_signal = ''
    if btc_dom > 55:
        btc_dom_signal = 'BTC.D высокая — BTC season, alts подавлены'
    elif btc_dom < 45:
        btc_dom_signal = 'BTC.D низкая — alt season, alts outperform'
    else:
        btc_dom_signal = 'BTC.D нейтральна — rotation в процессе'
    
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'total_mcap_usd': total_mcap,
        'total_mcap_t': round(total_mcap / 1e12, 2),
        'btc_dominance': round(btc_dom, 2),
        'eth_dominance': round(eth_dom, 2),
        'total3_dominance': round(total3_dom, 2),
        'btc_phase': btc_phase,
        'eth_phase': eth_phase,
        'market_signal': market_signal,
        'market_layman_ru': market_layman,
        'strk_market_impact_ru': strk_market_impact,
        'btc_dom_signal_ru': btc_dom_signal,
    }
    
    output_path = CACHE_DIR / 'total_phase.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f'\n=== SUMMARY ===')
    print(f'  Market signal: {market_signal}')
    print(f'  {market_layman}')
    print(f'  BTC dom: {btc_dom_signal}')
    print(f'  STRK impact: {strk_market_impact}')
    print(f'\n✓ Written: {output_path}')

if __name__ == '__main__':
    main()
