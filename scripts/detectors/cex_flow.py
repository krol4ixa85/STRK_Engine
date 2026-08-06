#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cex_flow.py — Directional CEX Flow analysis

Ключевая идея:
  · STRK → CEX = whales готовят продажу (BEARISH)
  · STRK ← CEX = купили и убирают в холод (BULLISH)
  · Net flow направление + persistence (дней подряд) = сила сигнала

Как это дополняет модель:
  · Distribution shape говорит "движется 100M STRK" но не куда
  · CEX flow говорит НАПРАВЛЕНИЕ — это уточнение
  · Дней подряд с одинаковым направлением = confidence

Классификация:
  · STRONG_ACCUMULATION: CEX→custody 3+ дней подряд, net > 5M STRK
  · MILD_ACCUMULATION: CEX→custody 1-2 дней, net > 1M STRK
  · NEUTRAL: балансирует
  · MILD_DISTRIBUTION: custody→CEX 1-2 дней, net > 1M STRK
  · STRONG_DISTRIBUTION: custody→CEX 3+ дней, net > 5M STRK

Пороги калиброваны на STRK - 1M STRK ≈ $25K на текущий момент (значимо).
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
OUTPUT_FILE = CACHE_DIR / 'cex_flow.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

# Verified CEX addresses (hot wallets) that hold STRK
CEX_ADDRESSES = {
    # Binance
    '0x28c6c06298d514db089934071355e5743bf21d60': 'Binance_14',
    '0x21a31ee1afc51d94c2efccaa2092ad1028285549': 'Binance_15',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d': 'Binance_16',
    '0x56eddb7aa87536c09ccc2793473599fd21a8b17f': 'Binance_17',
    '0x9696f59e4d72e237be84ffd425dcad154bf96976': 'Binance_18',
    '0x5a52e96bacdabb82fd05763e25335261b270efcb': 'Binance_19',
    '0xf977814e90da44bfa03b6295a0616a897441acec': 'Binance_20',
    '0xa7efae728d2936e78bda97dc267687568dd593f4': 'Binance_21',
    '0xe93685f3bba03016f02bd1828badd6195988d950': 'Binance_22',
    # OKX
    '0x5041ed759dd4afc3a72b8192c143f72f4724081a': 'OKX_1',
    '0x236f9f97e0e62388479bf9e5ba4889e46b0273c3': 'OKX_2',
    '0x6cc5f688a315f3dc28a7781717a9a798a59fda7b': 'OKX_3',
    # Kraken
    '0x2910543af39aba0cd09dbb2d50200b3e800a63d2': 'Kraken_1',
    '0x0a869d79a7052c7f1b55a8ebabbea3420f0d1e13': 'Kraken_2',
    '0xe853c56864a2ebe4576a807d26fdc4a0ada51919': 'Kraken_3',
    '0xdad4cb8f7f5f1a44e0e0f38e37e6a6dcd7d4d0f8': 'Kraken_4',
    # Bybit
    '0xf89d7b9c864f589bbf53a82105107622b35eaa40': 'Bybit_1',
    '0xee5b5b923ffce93a870b3104b7ca09c3db80047a': 'Bybit_2',
    # Coinbase
    '0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43': 'Coinbase_1',
    '0x71660c4005ba85c37ccec55d0c4493e66fe775d3': 'Coinbase_2',
    '0x503828976d22510aad0201ac7ec88293211d23da': 'Coinbase_3',
    # Bitget
    '0x0639556f03714a74a5feeaf5736a4a64ff70d206': 'Bitget_1',
    # MEXC
    '0x9642b23ed1e01df1092b92641051881a322f5d4e': 'MEXC_1',
    '0xce5485cfb26914c5dce00b9baf0580364dafc7a4': 'MEXC_2',
    # BingX
    '0xa86309988947559b6e72ef716c5058f479386c0f': 'BingX_1',
    '0xb1c561105359f549f6e9438867b435580ba3a6b0': 'BingX_2',
    # Gate.io
    '0x0d0707963952f2fba59dd06f2b425ace40b492fe': 'Gate_1',
    '0x1c4b70a3968436b9a0a9cf5205c787eb81bb558c': 'Gate_2',
}

CEX_LOWER = {addr.lower() for addr in CEX_ADDRESSES.keys()}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('cex_flow')


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


def fetch_cex_flows_window(hours_back):
    """Fetch STRK transfers involving CEX addresses in window.
    Returns list of {ts, from, to, amount, direction} where
    direction = 'CEX_INFLOW' (to CEX) or 'CEX_OUTFLOW' (from CEX)."""
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    from_block = get_block_at_time(from_ts); time.sleep(0.3)
    to_block = get_block_at_time(to_ts); time.sleep(0.3)
    if not from_block or not to_block:
        return []
    
    topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    cex_txs = []
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
                from_addr = ('0x' + topics[1][-40:]).lower()
                to_addr = ('0x' + topics[2][-40:]).lower()
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                ts = int(log['timeStamp'], 16)
                max_block = max(max_block, block)
                
                # Filter: keep only CEX-involving transactions above 100K STRK
                if amount < 100_000:
                    continue
                
                from_is_cex = from_addr in CEX_LOWER
                to_is_cex = to_addr in CEX_LOWER
                
                if to_is_cex and not from_is_cex:
                    cex_txs.append({
                        'ts': ts, 'from': from_addr, 'to': to_addr,
                        'amount': amount, 'direction': 'CEX_INFLOW',
                        'cex_name': CEX_ADDRESSES.get(next(k for k in CEX_ADDRESSES if k.lower() == to_addr), '?')
                    })
                elif from_is_cex and not to_is_cex:
                    cex_txs.append({
                        'ts': ts, 'from': from_addr, 'to': to_addr,
                        'amount': amount, 'direction': 'CEX_OUTFLOW',
                        'cex_name': CEX_ADDRESSES.get(next(k for k in CEX_ADDRESSES if k.lower() == from_addr), '?')
                    })
                # CEX-to-CEX (rebalance) - track separately
                elif from_is_cex and to_is_cex:
                    cex_txs.append({
                        'ts': ts, 'from': from_addr, 'to': to_addr,
                        'amount': amount, 'direction': 'CEX_REBALANCE',
                        'cex_name': f"{CEX_ADDRESSES.get(next(k for k in CEX_ADDRESSES if k.lower() == from_addr), '?')} → {CEX_ADDRESSES.get(next(k for k in CEX_ADDRESSES if k.lower() == to_addr), '?')}"
                    })
            except (KeyError, ValueError, StopIteration):
                continue
        if len(logs) < 1000: break
        current = max_block + 1
        time.sleep(0.3)
    
    return cex_txs


def aggregate_by_day(txs):
    """Group flows by day."""
    days = defaultdict(lambda: {'inflow': 0, 'outflow': 0, 'rebalance': 0,
                                  'inflow_count': 0, 'outflow_count': 0})
    for tx in txs:
        day_key = datetime.fromtimestamp(tx['ts'], tz=timezone.utc).strftime('%Y-%m-%d')
        if tx['direction'] == 'CEX_INFLOW':
            days[day_key]['inflow'] += tx['amount']
            days[day_key]['inflow_count'] += 1
        elif tx['direction'] == 'CEX_OUTFLOW':
            days[day_key]['outflow'] += tx['amount']
            days[day_key]['outflow_count'] += 1
        elif tx['direction'] == 'CEX_REBALANCE':
            days[day_key]['rebalance'] += tx['amount']
    
    result = []
    for day, data in sorted(days.items()):
        net = data['outflow'] - data['inflow']  # positive = accumulation direction
        result.append({
            'day': day,
            'inflow_strk': round(data['inflow'], 2),
            'outflow_strk': round(data['outflow'], 2),
            'rebalance_strk': round(data['rebalance'], 2),
            'net_strk': round(net, 2),  # positive = bullish (leaving CEX)
            'net_direction': 'BULLISH' if net > 1_000_000 else ('BEARISH' if net < -1_000_000 else 'NEUTRAL'),
            'inflow_count': data['inflow_count'],
            'outflow_count': data['outflow_count'],
        })
    return result


def classify_flow_signal(days_data):
    """Multi-day directional analysis."""
    if not days_data:
        return {
            'signal': 'INSUFFICIENT_DATA',
            'confidence': 'NONE',
            'interpretation': 'No CEX flow data available',
        }
    
    # Recent 7 days
    recent = days_data[-7:] if len(days_data) >= 7 else days_data
    
    # Count directional days
    bullish_days = sum(1 for d in recent if d['net_direction'] == 'BULLISH')
    bearish_days = sum(1 for d in recent if d['net_direction'] == 'BEARISH')
    neutral_days = sum(1 for d in recent if d['net_direction'] == 'NEUTRAL')
    
    # Consecutive days check (last N)
    consecutive_bullish = 0
    consecutive_bearish = 0
    for d in reversed(recent):
        if d['net_direction'] == 'BULLISH':
            if consecutive_bearish > 0: break
            consecutive_bullish += 1
        elif d['net_direction'] == 'BEARISH':
            if consecutive_bullish > 0: break
            consecutive_bearish += 1
        else:
            break
    
    # Total net
    total_net = sum(d['net_strk'] for d in recent)
    total_inflow = sum(d['inflow_strk'] for d in recent)
    total_outflow = sum(d['outflow_strk'] for d in recent)
    
    # === CLASSIFICATION ===
    signal = 'NEUTRAL'
    confidence = 'LOW'
    interpretation = ''
    
    if consecutive_bullish >= 3 and total_net > 5_000_000:
        signal = 'STRONG_ACCUMULATION'
        confidence = 'HIGH'
        interpretation = f'{consecutive_bullish} days consecutive CEX outflow, net +{total_net/1e6:.1f}M STRK — whales pulling to cold storage (BULLISH)'
    elif consecutive_bearish >= 3 and total_net < -5_000_000:
        signal = 'STRONG_DISTRIBUTION'
        confidence = 'HIGH'
        interpretation = f'{consecutive_bearish} days consecutive CEX inflow, net {total_net/1e6:.1f}M STRK — whales sending to exchanges (BEARISH)'
    elif bullish_days >= 4 and total_net > 2_000_000:
        signal = 'MILD_ACCUMULATION'
        confidence = 'MEDIUM'
        interpretation = f'{bullish_days}/7 days net outflow, +{total_net/1e6:.1f}M STRK — mild accumulation'
    elif bearish_days >= 4 and total_net < -2_000_000:
        signal = 'MILD_DISTRIBUTION'
        confidence = 'MEDIUM'
        interpretation = f'{bearish_days}/7 days net inflow, {total_net/1e6:.1f}M STRK — mild distribution'
    elif abs(total_net) < 1_000_000:
        signal = 'NEUTRAL'
        confidence = 'MEDIUM'
        interpretation = f'Balanced flows (net ±{abs(total_net)/1e6:.1f}M STRK)'
    else:
        signal = 'MIXED'
        confidence = 'LOW'
        interpretation = f'Mixed signals: {bullish_days}B/{bearish_days}D days, net {total_net/1e6:+.1f}M'
    
    return {
        'signal': signal,
        'confidence': confidence,
        'interpretation': interpretation,
        'stats': {
            'days_analyzed': len(recent),
            'bullish_days': bullish_days,
            'bearish_days': bearish_days,
            'neutral_days': neutral_days,
            'consecutive_bullish': consecutive_bullish,
            'consecutive_bearish': consecutive_bearish,
            'total_net_strk': round(total_net, 2),
            'total_inflow_strk': round(total_inflow, 2),
            'total_outflow_strk': round(total_outflow, 2),
        }
    }


def main():
    logger.info("=" * 60)
    logger.info("CEX FLOW DIRECTIONAL ANALYSIS")
    logger.info("=" * 60)
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    # 7-day window
    logger.info("\nFetching 7-day CEX flows...")
    txs = fetch_cex_flows_window(hours_back=168)
    logger.info(f"  Found {len(txs)} CEX-involving transactions (>100K STRK)")
    
    # Direction breakdown
    inflow_count = sum(1 for t in txs if t['direction'] == 'CEX_INFLOW')
    outflow_count = sum(1 for t in txs if t['direction'] == 'CEX_OUTFLOW')
    rebalance_count = sum(1 for t in txs if t['direction'] == 'CEX_REBALANCE')
    logger.info(f"  Inflows: {inflow_count} · Outflows: {outflow_count} · Rebalance: {rebalance_count}")
    
    # Aggregate by day
    days_data = aggregate_by_day(txs)
    logger.info(f"  Days with activity: {len(days_data)}")
    
    for d in days_data[-7:]:
        marker = "🟢" if d['net_direction'] == 'BULLISH' else ("🔴" if d['net_direction'] == 'BEARISH' else "⚪")
        logger.info(f"    {d['day']} {marker} in:{d['inflow_strk']/1e6:.1f}M · out:{d['outflow_strk']/1e6:.1f}M · net:{d['net_strk']/1e6:+.1f}M")
    
    # Classify
    classification = classify_flow_signal(days_data)
    logger.info(f"\n=== SIGNAL ===")
    logger.info(f"  {classification['signal']} · {classification['confidence']}")
    logger.info(f"  {classification['interpretation']}")
    
    # Top movers
    top_inflows = sorted([t for t in txs if t['direction'] == 'CEX_INFLOW'],
                        key=lambda x: -x['amount'])[:3]
    top_outflows = sorted([t for t in txs if t['direction'] == 'CEX_OUTFLOW'],
                         key=lambda x: -x['amount'])[:3]
    
    if top_inflows:
        logger.info("\nTop inflows (to CEX):")
        for t in top_inflows:
            logger.info(f"  {t['amount']/1e6:.2f}M → {t['cex_name']} from {t['from'][:12]}...")
    
    if top_outflows:
        logger.info("\nTop outflows (from CEX):")
        for t in top_outflows:
            logger.info(f"  {t['amount']/1e6:.2f}M ← {t['cex_name']} to {t['to'][:12]}...")
    
    # Save
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'window_hours': 168,
        'total_transactions': len(txs),
        'inflow_count': inflow_count,
        'outflow_count': outflow_count,
        'rebalance_count': rebalance_count,
        'days_data': days_data,
        'classification': classification,
        'top_inflows': [{'amount': t['amount'], 'to_cex': t['cex_name'], 'from': t['from']} for t in top_inflows],
        'top_outflows': [{'amount': t['amount'], 'from_cex': t['cex_name'], 'to': t['to']} for t in top_outflows],
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
