#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge_activity.py — L1 to L2 bridge activity through StarkGate

StarkGate bridge адреса (L1 side):
  · 0x0437465dfb5b79726e35f08559b0cbea55bb585c — ETH bridge (main)
  · 0x9F96fE0633eE838D0298E8b8980E6716bE81388d — USDC bridge  
  · 0xBB3400F107804DFB482565FF1Ec8D8aE66747605 — DAI bridge
  · 0x0d5C36f3F19D46339B33e7FfB2a29b4D0D4c1AEd — STRK bridge (L1→L2)
  · Legacy: 0xca14007eff0db1f8135f4c25b34de49ab0d42766 — STRK token (already tracked)

Метрики:
  · Deposits volume (L1→L2, USD)
  · Withdrawals volume (L2→L1, USD)
  · Net flow (positive = users bringing funds to Starknet)
  · Unique addresses count (adoption)

Сигнал:
  · Deposits >> withdrawals + new users → BULLISH (adoption growing)
  · Withdrawals >> deposits → BEARISH (exodus)
  · Both high → HIGH_ACTIVITY (potential catalyst approaching)
  · Both low → CALM
"""

import os
import sys
import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'bridge_activity.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')

# StarkGate bridge addresses (L1 side)
BRIDGE_ADDRESSES = {
    '0x0437465dfb5b79726e35f08559b0cbea55bb585c': 'ETH_bridge',
    '0x9f96fe0633ee838d0298e8b8980e6716be81388d': 'USDC_bridge',
    '0xbb3400f107804dfb482565ff1ec8d8ae66747605': 'DAI_bridge',
    '0xf6080d9fbeebcd44d89affbfd42f098cbff92816': 'USDT_bridge',
    '0x283751a21eafbfcd52297820d27c1f1963d9b5b4': 'WBTC_bridge',
    '0xcd21d6b6f7f26cb60a2d0dfbca87bb00ceb8f79f': 'wstETH_bridge',
    '0x0d5c36f3f19d46339b33e7ffb2a29b4d0d4c1aed': 'STRK_bridge',
}

BRIDGE_LOWER = {addr.lower(): name for addr, name in BRIDGE_ADDRESSES.items()}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('bridge')


def api_call(params, timeout=30):
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error(f"API error: {e}")
        return None


def get_block_at_time(ts):
    d = api_call({'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
                  'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY})
    return int(d['result']) if d and d.get('status') == '1' else None


def fetch_bridge_txs(bridge_addr, hours_back):
    """Fetch normal transactions to/from bridge address."""
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    # Normal ETH transactions
    result = api_call({
        'chainid': 1, 'module': 'account', 'action': 'txlist',
        'address': bridge_addr,
        'startblock': from_block, 'endblock': to_block,
        'page': 1, 'offset': 1000, 'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    })
    
    if not result or result.get('status') != '1':
        return []
    
    return result.get('result', [])


def fetch_erc20_bridge_txs(bridge_addr, hours_back):
    """Fetch ERC-20 token transfers involving bridge."""
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    result = api_call({
        'chainid': 1, 'module': 'account', 'action': 'tokentx',
        'address': bridge_addr,
        'startblock': from_block, 'endblock': to_block,
        'page': 1, 'offset': 1000, 'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    })
    
    if not result or result.get('status') != '1':
        return []
    
    return result.get('result', [])


def analyze_bridge_flows():
    """Analyze all bridge flows for last 7 days."""
    hours_back = 168  # 7 days
    
    results = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'window_hours': hours_back,
        'bridges': {},
    }
    
    total_deposits = defaultdict(float)  # {token: usd_value_approx}
    total_withdrawals = defaultdict(float)
    unique_depositors = set()
    unique_withdrawers = set()
    
    # Approximate USD prices (would use API in production)
    PRICES = {
        'ETH': 3200, 'STRK': 0.026, 'USDC': 1, 'DAI': 1, 'USDT': 1,
        'WBTC': 65000, 'wstETH': 3700,
    }
    
    for bridge_addr, bridge_name in BRIDGE_ADDRESSES.items():
        logger.info(f"\n  Fetching {bridge_name} ({bridge_addr[:12]}...)")
        
        # For ETH bridge - normal txs
        if bridge_name == 'ETH_bridge':
            txs = fetch_bridge_txs(bridge_addr, hours_back)
            deposits = 0  # to bridge = deposit
            withdrawals = 0  # from bridge = withdrawal
            depositors = set()
            withdrawers = set()
            
            for tx in txs:
                try:
                    value_wei = int(tx.get('value', 0))
                    value_eth = value_wei / 1e18
                    
                    if tx.get('to', '').lower() == bridge_addr.lower():
                        deposits += value_eth
                        depositors.add(tx.get('from', '').lower())
                        total_deposits['ETH'] += value_eth
                        unique_depositors.add(tx.get('from', '').lower())
                    elif tx.get('from', '').lower() == bridge_addr.lower():
                        withdrawals += value_eth
                        withdrawers.add(tx.get('to', '').lower())
                        total_withdrawals['ETH'] += value_eth
                        unique_withdrawers.add(tx.get('to', '').lower())
                except (ValueError, KeyError):
                    continue
            
            results['bridges'][bridge_name] = {
                'deposits_eth': round(deposits, 4),
                'withdrawals_eth': round(withdrawals, 4),
                'deposits_usd': round(deposits * PRICES['ETH'], 2),
                'withdrawals_usd': round(withdrawals * PRICES['ETH'], 2),
                'net_usd': round((deposits - withdrawals) * PRICES['ETH'], 2),
                'unique_depositors': len(depositors),
                'unique_withdrawers': len(withdrawers),
                'total_txs': len(txs),
            }
            logger.info(f"    Deposits: {deposits:.2f} ETH · Withdrawals: {withdrawals:.2f} ETH")
        else:
            # ERC-20 tokens
            txs = fetch_erc20_bridge_txs(bridge_addr, hours_back)
            deposits = 0
            withdrawals = 0
            depositors = set()
            withdrawers = set()
            token_symbol = 'UNKNOWN'
            
            for tx in txs:
                try:
                    value = float(tx.get('value', 0)) / (10 ** int(tx.get('tokenDecimal', 18)))
                    token_symbol = tx.get('tokenSymbol', 'UNKNOWN')
                    
                    if tx.get('to', '').lower() == bridge_addr.lower():
                        deposits += value
                        depositors.add(tx.get('from', '').lower())
                        total_deposits[token_symbol] += value
                        unique_depositors.add(tx.get('from', '').lower())
                    elif tx.get('from', '').lower() == bridge_addr.lower():
                        withdrawals += value
                        withdrawers.add(tx.get('to', '').lower())
                        total_withdrawals[token_symbol] += value
                        unique_withdrawers.add(tx.get('to', '').lower())
                except (ValueError, KeyError):
                    continue
            
            price = PRICES.get(token_symbol, 1)
            results['bridges'][bridge_name] = {
                'token': token_symbol,
                'deposits': round(deposits, 4),
                'withdrawals': round(withdrawals, 4),
                'deposits_usd': round(deposits * price, 2),
                'withdrawals_usd': round(withdrawals * price, 2),
                'net_usd': round((deposits - withdrawals) * price, 2),
                'unique_depositors': len(depositors),
                'unique_withdrawers': len(withdrawers),
                'total_txs': len(txs),
            }
            logger.info(f"    {token_symbol}: deposits {deposits:.2f} · withdrawals {withdrawals:.2f}")
    
    # === Aggregates ===
    total_dep_usd = sum(b['deposits_usd'] for b in results['bridges'].values())
    total_wd_usd = sum(b['withdrawals_usd'] for b in results['bridges'].values())
    net_usd = total_dep_usd - total_wd_usd
    
    results['aggregate'] = {
        'total_deposits_usd_7d': round(total_dep_usd, 2),
        'total_withdrawals_usd_7d': round(total_wd_usd, 2),
        'net_flow_usd_7d': round(net_usd, 2),
        'unique_depositors_7d': len(unique_depositors),
        'unique_withdrawers_7d': len(unique_withdrawers),
    }
    
    # === Classification ===
    signal = 'CALM'
    interpretation = ''
    
    if total_dep_usd > 10_000_000 and net_usd > 1_000_000:
        signal = 'BULLISH_ADOPTION'
        interpretation = f'Net +${net_usd/1e6:.1f}M inflow · {len(unique_depositors)} depositors — adoption growing'
    elif total_wd_usd > 10_000_000 and net_usd < -1_000_000:
        signal = 'BEARISH_EXODUS'
        interpretation = f'Net -${abs(net_usd)/1e6:.1f}M outflow · {len(unique_withdrawers)} withdrawers — exodus signal'
    elif total_dep_usd > 5_000_000 and total_wd_usd > 5_000_000:
        signal = 'HIGH_ACTIVITY'
        interpretation = f'High two-way flow (${total_dep_usd/1e6:.1f}M in, ${total_wd_usd/1e6:.1f}M out) — potential catalyst'
    elif total_dep_usd + total_wd_usd < 1_000_000:
        signal = 'LOW_ACTIVITY'
        interpretation = f'Very low bridge activity (${(total_dep_usd + total_wd_usd)/1e6:.1f}M total) — no user interest'
    else:
        signal = 'NORMAL_ACTIVITY'
        interpretation = f'Normal bridge activity (net ${net_usd/1e6:+.1f}M)'
    
    results['classification'] = {
        'signal': signal,
        'interpretation': interpretation,
    }
    
    logger.info(f"\n=== AGGREGATE ===")
    logger.info(f"Total deposits (USD): ${total_dep_usd:,.0f}")
    logger.info(f"Total withdrawals (USD): ${total_wd_usd:,.0f}")
    logger.info(f"Net flow: ${net_usd:+,.0f}")
    logger.info(f"Unique depositors: {len(unique_depositors)}")
    logger.info(f"Unique withdrawers: {len(unique_withdrawers)}")
    logger.info(f"\nSignal: {signal}")
    logger.info(f"Interpretation: {interpretation}")
    
    return results


def main():
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    logger.info("=" * 60)
    logger.info("BRIDGE ACTIVITY · L1↔L2 flows via StarkGate")
    logger.info("=" * 60)
    
    results = analyze_bridge_flows()
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
