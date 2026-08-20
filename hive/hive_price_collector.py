"""
Hive Price Collector v1 · Fix STRK Price Discrepancy
=====================================================
Fetches accurate prices from CoinGecko via Hive Intelligence API.
Fixes dashboard showing wrong STRK price ($0.0247 vs real $0.023).

Also fetches for all HOLD + STRONG_BUY tokens.

Sources:
- Hive Intelligence API (managed CoinGecko access)
- Fallback: direct CoinGecko API if Hive fails

Requires:
    HIVE_API_KEY

Output: data/cache/hive_prices.json

Credits: ~5-10 per run (Analyst tier 500K/mo, negligible)
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# CONFIG
# ============================================================
# Tokens to fetch (STRK bottleneck + HOLD + rotation candidates)
TRACKED_TOKENS = {
    # Bottleneck
    'STRK': 'starknet',
    # HOLD (STRONG_BUY)
    'LINK': 'chainlink',
    'ETHFI': 'ether-fi',
    'MORPHO': 'morpho',
    'ONDO': 'ondo-finance',
    # Rotation candidates
    'ARB': 'arbitrum',
    'OP': 'optimism',
    'AAVE': 'aave',
    'PENDLE': 'pendle',
    'LDO': 'lido-dao',
    'EIGEN': 'eigenlayer',
    'CFG': 'centrifuge',
    # Macro
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'SOL': 'solana',
}

# ============================================================
# HIVE API
# ============================================================
HIVE_BASE = 'https://api.hiveintelligence.xyz/v1'

def call_hive(tool_name, arguments, api_key):
    """
    Call Hive Intelligence API tool.
    Returns response dict or None on failure.
    """
    if not requests or not api_key:
        return None
    
    try:
        response = requests.post(
            f'{HIVE_BASE}/execute',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'toolName': tool_name,
                'arguments': arguments,
            },
            timeout=30
        )
        
        if response.status_code != 200:
            print(f'    ⚠ Hive {tool_name} HTTP {response.status_code}: {response.text[:200]}')
            return None
        
        return response.json()
    except Exception as e:
        print(f'    ⚠ Hive {tool_name}: {e}')
        return None

def fetch_hive_price(symbol, coingecko_id, api_key):
    """
    Try to fetch price via Hive Intelligence.
    Tests multiple tool names since Hive API surface may vary.
    """
    # Try common tool names for price fetching
    tool_variations = [
        # Try get_simple_price with coingecko_id
        ('get_simple_price', {'ids': coingecko_id, 'vs_currencies': 'usd'}),
        # Try get_coin_data
        ('get_coin_data', {'id': coingecko_id}),
        # Try get_price
        ('get_price', {'symbol': symbol, 'vs_currency': 'usd'}),
        # Try coingecko_price
        ('coingecko_price', {'coin_id': coingecko_id}),
        # Fallback
        ('get_coin_market_data', {'id': coingecko_id, 'vs_currency': 'usd'}),
    ]
    
    for tool_name, args in tool_variations:
        result = call_hive(tool_name, args, api_key)
        if result is not None:
            # Successfully got response, try to extract price
            price_data = extract_price(result, symbol, coingecko_id)
            if price_data:
                price_data['hive_tool'] = tool_name
                return price_data
    
    return None

def extract_price(response, symbol, coingecko_id):
    """
    Extract price from various Hive response formats.
    """
    if not response:
        return None
    
    # Get the actual data (Hive wraps it)
    data = response.get('data') or response.get('result') or response
    
    # Handle different response shapes
    # 1. {coingecko_id: {"usd": 0.023}}
    if isinstance(data, dict) and coingecko_id in data:
        entry = data[coingecko_id]
        if isinstance(entry, dict):
            price = entry.get('usd') or entry.get('price')
            if price:
                return {
                    'symbol': symbol,
                    'price_usd': float(price),
                    'change_24h_pct': entry.get('usd_24h_change'),
                    'market_cap': entry.get('usd_market_cap'),
                    'volume_24h': entry.get('usd_24h_vol'),
                    'source': 'hive_coingecko',
                }
    
    # 2. {market_data: {current_price: {usd: 0.023}, ...}}
    if isinstance(data, dict) and 'market_data' in data:
        md = data['market_data']
        current = md.get('current_price', {}).get('usd')
        if current:
            return {
                'symbol': symbol,
                'price_usd': float(current),
                'change_24h_pct': md.get('price_change_percentage_24h'),
                'market_cap': md.get('market_cap', {}).get('usd'),
                'volume_24h': md.get('total_volume', {}).get('usd'),
                'ath': md.get('ath', {}).get('usd'),
                'source': 'hive_coingecko',
            }
    
    # 3. Direct price shape {price: 0.023}
    if isinstance(data, dict) and 'price' in data:
        return {
            'symbol': symbol,
            'price_usd': float(data['price']),
            'change_24h_pct': data.get('change_24h') or data.get('change_percentage_24h'),
            'source': 'hive_coingecko',
        }
    
    return None

# ============================================================
# FALLBACK · Direct CoinGecko (public, no key)
# ============================================================
def fetch_coingecko_direct(coingecko_id):
    """Fallback: direct CoinGecko API (no Hive)."""
    if not requests:
        return None
    try:
        url = 'https://api.coingecko.com/api/v3/simple/price'
        params = {
            'ids': coingecko_id,
            'vs_currencies': 'usd',
            'include_24hr_change': 'true',
            'include_24hr_vol': 'true',
            'include_market_cap': 'true',
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if coingecko_id in data:
            entry = data[coingecko_id]
            return {
                'symbol': None,  # set by caller
                'price_usd': float(entry.get('usd', 0)),
                'change_24h_pct': entry.get('usd_24h_change'),
                'market_cap': entry.get('usd_market_cap'),
                'volume_24h': entry.get('usd_24h_vol'),
                'source': 'coingecko_direct',
            }
    except Exception as e:
        print(f'    ⚠ CoinGecko direct {coingecko_id}: {e}')
    return None

# ============================================================
# COMPARE WITH DASHBOARD PRICES (detect discrepancy)
# ============================================================
def load_dashboard_prices():
    """Load current prices from various dashboard cache files."""
    prices = {}
    
    # From STRK LAB
    lab_path = CACHE_DIR / 'strk_lab_report.json'
    if lab_path.exists():
        try:
            with open(lab_path) as f:
                lab = json.load(f)
            strk_price = lab.get('strk_status', {}).get('current_price')
            if strk_price:
                prices['STRK'] = {'source': 'strk_lab_report', 'price': strk_price}
        except:
            pass
    
    # From token_scan cache
    scan_dir = CACHE_DIR / 'token_scan'
    if scan_dir.exists():
        for f in scan_dir.glob('*.json'):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                sym = data.get('token')
                price = data.get('current_price') or data.get('price')
                if sym and price:
                    if sym not in prices:
                        prices[sym] = {'source': 'token_scan', 'price': price}
            except:
                pass
    
    return prices

def detect_discrepancy(actual, dashboard, threshold_pct=2.0):
    """
    Check if actual price differs from dashboard price by more than threshold.
    Returns discrepancy info or None.
    """
    if actual is None or dashboard is None or actual == 0:
        return None
    
    diff_pct = abs(actual - dashboard) / actual * 100
    if diff_pct > threshold_pct:
        return {
            'actual': actual,
            'dashboard': dashboard,
            'diff_pct': round(diff_pct, 2),
            'is_significant': diff_pct > 5,
        }
    return None

# ============================================================
# MAIN
# ============================================================
def main():
    print('=== Hive Price Collector v1 ===\n')
    
    api_key = os.getenv('HIVE_API_KEY')
    if not api_key:
        print('⚠ HIVE_API_KEY not set — using CoinGecko fallback only')
    else:
        print(f'✓ HIVE_API_KEY loaded ({len(api_key)} chars)')
    
    # Load current dashboard prices for comparison
    dashboard_prices = load_dashboard_prices()
    print(f'\nDashboard prices loaded: {len(dashboard_prices)} tokens')
    for sym, info in dashboard_prices.items():
        print(f'  {sym}: ${info["price"]:.6f} (from {info["source"]})')
    
    # Fetch fresh prices
    print(f'\n=== Fetching Fresh Prices ({len(TRACKED_TOKENS)} tokens) ===')
    fresh_prices = {}
    discrepancies = []
    hive_used = 0
    coingecko_used = 0
    
    for symbol, cg_id in TRACKED_TOKENS.items():
        print(f'\n{symbol} ({cg_id})...')
        
        # Try Hive first
        result = None
        if api_key:
            result = fetch_hive_price(symbol, cg_id, api_key)
            if result:
                hive_used += 1
                print(f'  ✓ Hive: ${result["price_usd"]:.6f} (tool: {result.get("hive_tool", "?")})')
        
        # Fallback to CoinGecko direct
        if not result:
            result = fetch_coingecko_direct(cg_id)
            if result:
                result['symbol'] = symbol
                coingecko_used += 1
                print(f'  ✓ CoinGecko fallback: ${result["price_usd"]:.6f}')
        
        if result:
            fresh_prices[symbol] = result
            
            # Check discrepancy vs dashboard
            if symbol in dashboard_prices:
                dashboard_price = dashboard_prices[symbol]['price']
                disc = detect_discrepancy(result['price_usd'], dashboard_price)
                if disc:
                    disc['symbol'] = symbol
                    disc['dashboard_source'] = dashboard_prices[symbol]['source']
                    discrepancies.append(disc)
                    if disc['is_significant']:
                        print(f'  🚨 DISCREPANCY: dashboard ${dashboard_price:.6f} vs actual ${result["price_usd"]:.6f} ({disc["diff_pct"]}%)')
                    else:
                        print(f'  ⚠ Small discrepancy: {disc["diff_pct"]}%')
        else:
            print(f'  ✗ Failed all sources')
    
    # Build output
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'sources_used': {
            'hive': hive_used,
            'coingecko_direct': coingecko_used,
            'total_tokens': len(fresh_prices),
        },
        'prices': fresh_prices,
        'discrepancies': discrepancies,
        'significant_discrepancies_count': sum(1 for d in discrepancies if d.get('is_significant')),
    }
    
    output_path = CACHE_DIR / 'hive_prices.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f'\n=== SUMMARY ===')
    print(f'  Hive success: {hive_used}/{len(TRACKED_TOKENS)}')
    print(f'  CoinGecko fallback: {coingecko_used}')
    print(f'  Discrepancies found: {len(discrepancies)}')
    if discrepancies:
        for d in discrepancies:
            severity = '🚨 CRITICAL' if d.get('is_significant') else '⚠ minor'
            print(f'    {severity} {d["symbol"]}: dashboard ${d["dashboard"]:.6f} vs actual ${d["actual"]:.6f} ({d["diff_pct"]}%)')
    
    print(f'\n✓ Written: {output_path}')

if __name__ == '__main__':
    main()
