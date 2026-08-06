#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
concentration_metrics.py — HHI + Shannon Entropy для LARGE receivers

Ключевые метрики:
  · HHI = Σ(s_i)² — Herfindahl-Hirschman Index
    Высокий HHI = концентрация капитала на узкий круг (ACCUMULATION)
    Низкий HHI = размытие на много адресов (DISTRIBUTION)
  
  · Shannon Entropy = -Σ(s_i × log(s_i))
    Низкая entropy = концентрация (Accumulation)
    Высокая entropy = размытость (Distribution)
  
  · Gini coefficient — альтернативная мера неравномерности

Как решает проблему Signal Overlap:
  Accumulation: LARGE receivers = 100, но HHI = 0.35 (10-20 адресов держат основной объём)
  Distribution: LARGE receivers = 100, но HHI = 0.05 (100+ адресов, каждый по чуть-чуть)

Пороги (калиброванные):
  HHI >= 0.25 + LARGE >= 30 → Accumulation signal
  HHI < 0.10 + LARGE >= 50 → Distribution signal
  Entropy < 3.0 + LARGE >= 30 → Concentrated (accumulation-like)
  Entropy > 4.5 + LARGE >= 30 → Diluted (distribution-like)
"""

import os
import sys
import json
import math
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = CACHE_DIR / 'concentration_metrics.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

# Known CEX/bridge/team addresses - exclude from concentration analysis
KNOWN_IGNORE = {
    '0x28c6c06298d514db089934071355e5743bf21d60', '0x21a31ee1afc51d94c2efccaa2092ad1028285549',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d', '0x56eddb7aa87536c09ccc2793473599fd21a8b17f',
    '0x9696f59e4d72e237be84ffd425dcad154bf96976', '0x5a52e96bacdabb82fd05763e25335261b270efcb',
    '0xf977814e90da44bfa03b6295a0616a897441acec', '0xa7efae728d2936e78bda97dc267687568dd593f4',
    '0xe93685f3bba03016f02bd1828badd6195988d950', '0xf89d7b9c864f589bbf53a82105107622b35eaa40',
    '0x9642b23ed1e01df1092b92641051881a322f5d4e', '0xce5485cfb26914c5dce00b9baf0580364dafc7a4',
    '0xa86309988947559b6e72ef716c5058f479386c0f', '0xb1c561105359f549f6e9438867b435580ba3a6b0',
    '0x0000000000000000000000000000000000000000',
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('concentration')


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


def fetch_transfers_window(hours_back):
    """Fetch all STRK transfers in window, aggregated per recipient."""
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return {}
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    recipients = defaultdict(float)
    current = from_block
    
    for _ in range(30):
        data = api_call({'chainid': 1, 'module': 'logs', 'action': 'getLogs',
                        'address': STRK_L1, 'topic0': topic,
                        'fromBlock': current, 'toBlock': to_block,
                        'page': 1, 'offset': 1000, 'apikey': ETHERSCAN_API_KEY})
        if not data or data.get('status') != '1' or not data.get('result'):
            break
        logs = data['result']
        max_block = 0
        for log in logs:
            try:
                topics = log['topics']
                if len(topics) < 3: continue
                to_addr = ('0x' + topics[2][-40:]).lower()
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                max_block = max(max_block, block)
                recipients[to_addr] += amount
            except (KeyError, ValueError):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.3)
    
    return dict(recipients)


def compute_hhi(shares):
    """HHI = Σ(s_i)² · Result 0-1, higher = more concentrated."""
    if not shares:
        return 0
    return round(sum(s ** 2 for s in shares), 4)


def compute_shannon_entropy(shares):
    """Shannon entropy in bits. Higher = more diluted."""
    if not shares:
        return 0
    entropy = 0
    for s in shares:
        if s > 0:
            entropy -= s * math.log2(s)
    return round(entropy, 3)


def compute_gini(values):
    """Gini coefficient. 0 = perfect equality, 1 = perfect inequality."""
    if not values:
        return 0
    sorted_values = sorted(values)
    n = len(sorted_values)
    cumsum = 0
    for i, v in enumerate(sorted_values, 1):
        cumsum += i * v
    total = sum(sorted_values)
    if total == 0:
        return 0
    gini = (2 * cumsum) / (n * total) - (n + 1) / n
    return round(gini, 4)


def analyze_concentration(hours_back=336):  # 14 days default
    """Compute all concentration metrics for LARGE receivers in window."""
    logger.info(f"Fetching STRK transfers for {hours_back}h window...")
    recipients = fetch_transfers_window(hours_back)
    logger.info(f"  {len(recipients)} unique recipients")
    
    if not recipients:
        return None
    
    # Filter to LARGE receivers only (>= 1M STRK)
    LARGE_THRESHOLD = 1_000_000
    large_receivers = {
        addr: amt for addr, amt in recipients.items()
        if amt >= LARGE_THRESHOLD and addr not in KNOWN_IGNORE
    }
    
    logger.info(f"  {len(large_receivers)} LARGE receivers (>1M STRK, non-CEX)")
    
    if not large_receivers:
        return {
            'as_of': datetime.now(timezone.utc).isoformat(),
            'window_hours': hours_back,
            'large_count': 0,
            'total_amount': 0,
            'hhi': 0,
            'entropy_bits': 0,
            'gini': 0,
            'concentration_signal': 'INSUFFICIENT_DATA',
        }
    
    # Compute shares
    total = sum(large_receivers.values())
    shares = [amt / total for amt in large_receivers.values()]
    amounts = list(large_receivers.values())
    
    # Metrics
    hhi = compute_hhi(shares)
    entropy = compute_shannon_entropy(shares)
    gini = compute_gini(amounts)
    
    # Top-N shares (concentration analysis)
    sorted_amounts = sorted(amounts, reverse=True)
    top_5_share = sum(sorted_amounts[:5]) / total if len(sorted_amounts) >= 5 else 1.0
    top_10_share = sum(sorted_amounts[:10]) / total if len(sorted_amounts) >= 10 else 1.0
    top_20_share = sum(sorted_amounts[:20]) / total if len(sorted_amounts) >= 20 else 1.0
    
    # === SIGNAL CLASSIFICATION ===
    n_large = len(large_receivers)
    
    signal = 'NEUTRAL'
    interpretation = []
    
    # HHI-based signals
    if hhi >= 0.25 and n_large >= 20:
        signal = 'ACCUMULATION'
        interpretation.append(f'HHI {hhi:.3f} >= 0.25 (concentrated) + {n_large} large receivers → smart money consolidating')
    elif hhi < 0.10 and n_large >= 50:
        signal = 'DISTRIBUTION'
        interpretation.append(f'HHI {hhi:.3f} < 0.10 (diluted) + {n_large} large receivers → capital fragmenting to retail')
    elif 0.10 <= hhi < 0.20 and n_large >= 40:
        signal = 'WEAK_DISTRIBUTION'
        interpretation.append(f'HHI {hhi:.3f} in dilution range + many receivers → mild distribution')
    elif hhi >= 0.20 and n_large < 30:
        signal = 'WEAK_ACCUMULATION'
        interpretation.append(f'HHI {hhi:.3f} concentrated but few receivers → early accumulation')
    
    # Top-N cross-check
    if top_5_share > 0.60:
        interpretation.append(f'Top 5 hold {top_5_share*100:.0f}% of flow — very concentrated')
    elif top_5_share < 0.20:
        interpretation.append(f'Top 5 only {top_5_share*100:.0f}% — highly diluted')
    
    # Entropy cross-check
    if entropy < 3.0 and n_large > 20:
        interpretation.append(f'Low entropy ({entropy:.2f} bits) confirms concentration')
    elif entropy > 5.0 and n_large > 30:
        interpretation.append(f'High entropy ({entropy:.2f} bits) confirms dilution')
    
    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'window_hours': hours_back,
        'large_count': n_large,
        'total_amount_strk': round(total, 2),
        'hhi': hhi,
        'entropy_bits': entropy,
        'gini': gini,
        'top_5_share_pct': round(top_5_share * 100, 2),
        'top_10_share_pct': round(top_10_share * 100, 2),
        'top_20_share_pct': round(top_20_share * 100, 2),
        'concentration_signal': signal,
        'interpretation': interpretation,
        'top_5_addresses': sorted(large_receivers.items(), key=lambda x: -x[1])[:5],
    }
    
    return result


def main():
    logger.info("=" * 60)
    logger.info("CONCENTRATION METRICS · HHI + Entropy for LARGE receivers")
    logger.info("=" * 60)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    # Compute for 14d window
    result = analyze_concentration(hours_back=336)
    if not result:
        return 1
    
    logger.info(f"\nLARGE receivers (14d): {result['large_count']}")
    logger.info(f"Total flow: {result['total_amount_strk']/1e6:.2f}M STRK")
    logger.info(f"HHI: {result['hhi']}")
    logger.info(f"Shannon Entropy: {result['entropy_bits']} bits")
    logger.info(f"Gini: {result['gini']}")
    logger.info(f"Top 5 share: {result['top_5_share_pct']}%")
    logger.info(f"Top 10 share: {result['top_10_share_pct']}%")
    logger.info(f"Signal: {result['concentration_signal']}")
    for i in result['interpretation']:
        logger.info(f"  · {i}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        # Convert top_5_addresses list of tuples to list of lists for JSON
        result_json = dict(result)
        result_json['top_5_addresses'] = [[a, amt] for a, amt in result['top_5_addresses']]
        json.dump(result_json, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
