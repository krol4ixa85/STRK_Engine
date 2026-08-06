#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whale_auto_analysis.py — Автоматический анализ топ-3 whale events за 24h

Читает cex_flow.json + whale_monitor cache и находит топ-3 крупных transfer.
Для каждого события классифицирует:
  · REBALANCE     — Binance ↔ BingX (routing между CEX)
  · ACCUMULATION  — CEX → cold storage / smart accumulator
  · DISTRIBUTION  — cold storage → CEX (whale продаёт)
  · BRIDGE        — L1 ↔ L2 (StarkGate)
  · DEX_ROUTING   — через Uniswap/DEX
  · UNKNOWN       — не удаётся классифицировать

Результат в единый cohort read: "N/3 DISTRIBUTION events → whales are selling"
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'whale_auto_analysis.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('whale_auto')


# Known address patterns
CEX_PATTERNS = ['binance', 'coinbase', 'okx', 'bybit', 'bingx', 'kraken', 'kucoin', 'gate']
BRIDGE_ADDRESSES = {
    '0x0437465dfb5b79726e35f08559b0cbea55bb585c': 'StarkGate_ETH',
    '0x9f96fe0633ee838d0298e8b8980e6716be81388d': 'StarkGate_USDC',
    '0xbb3400f107804dfb482565ff1ec8d8ae66747605': 'StarkGate_DAI',
    '0x0d5c36f3f19d46339b33e7ffb2a29b4d0d4c1aed': 'StarkGate_STRK',
}
DEX_PATTERNS = ['uniswap', 'sushiswap', 'curve', 'pancake', '1inch']


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def load_seeds():
    p = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def label_address(addr, seeds):
    """Return (label, kind) for an address."""
    addr_lower = addr.lower()
    
    # Bridge check
    if addr_lower in BRIDGE_ADDRESSES:
        return (BRIDGE_ADDRESSES[addr_lower], 'BRIDGE')
    
    # Search in seeds
    SKIP = {'_meta', '_phantoms'}
    for cat, data in seeds.items():
        if cat in SKIP or not isinstance(data, dict):
            continue
        for name, entry in data.items():
            if name.startswith('_') or not isinstance(entry, dict):
                continue
            if entry.get('address', '').lower() == addr_lower:
                name_lower = name.lower()
                # Classify kind based on name/category
                if any(p in name_lower for p in CEX_PATTERNS):
                    return (name, 'CEX')
                elif any(p in name_lower for p in DEX_PATTERNS):
                    return (name, 'DEX')
                elif 'custody' in cat.lower() or 'cold' in name_lower or 'vault' in name_lower:
                    return (name, 'CUSTODY')
                elif 'smart' in name_lower or 'accumulator' in name_lower:
                    return (name, 'SMART')
                else:
                    return (name, 'WATCHLIST')
    
    return (f'{addr[:8]}...', 'UNKNOWN')


def classify_event(from_kind, to_kind, from_label, to_label):
    """Classify whale movement based on source and destination."""
    # Both CEX = REBALANCE
    if from_kind == 'CEX' and to_kind == 'CEX':
        return {
            'type': 'REBALANCE',
            'meaning': 'Инфраструктурный переток между биржами (не market signal)',
            'sentiment': 'NEUTRAL',
        }
    
    # CEX → non-CEX = потенциально ACCUMULATION
    if from_kind == 'CEX' and to_kind in ('CUSTODY', 'SMART', 'WATCHLIST', 'UNKNOWN'):
        return {
            'type': 'ACCUMULATION',
            'meaning': f'Токены уходят с биржи ({from_label}) на приватный кошелёк → smart money buying',
            'sentiment': 'BULLISH',
        }
    
    # non-CEX → CEX = DISTRIBUTION
    if to_kind == 'CEX' and from_kind in ('CUSTODY', 'SMART', 'WATCHLIST', 'UNKNOWN'):
        return {
            'type': 'DISTRIBUTION',
            'meaning': f'Крупный отправитель шлёт токены на биржу ({to_label}) → whale selling',
            'sentiment': 'BEARISH',
        }
    
    # Bridge
    if from_kind == 'BRIDGE' or to_kind == 'BRIDGE':
        return {
            'type': 'BRIDGE',
            'meaning': 'L1 ↔ L2 bridging (StarkGate)',
            'sentiment': 'NEUTRAL',
        }
    
    # DEX
    if from_kind == 'DEX' or to_kind == 'DEX':
        return {
            'type': 'DEX_ROUTING',
            'meaning': 'Транзит через DEX (Uniswap/swap)',
            'sentiment': 'NEUTRAL',
        }
    
    # Non-CEX to non-CEX
    if from_kind in ('CUSTODY', 'SMART') and to_kind in ('CUSTODY', 'SMART'):
        return {
            'type': 'INTERNAL',
            'meaning': 'Перевод между приватными кошельками (не market signal)',
            'sentiment': 'NEUTRAL',
        }
    
    return {
        'type': 'UNKNOWN',
        'meaning': 'Не удаётся классифицировать без дополнительного контекста',
        'sentiment': 'NEUTRAL',
    }


def extract_top_events():
    """Extract top whale events from cex_flow data."""
    cex_flow = load_json('cex_flow.json')
    seeds = load_seeds()
    
    events = []
    
    if not cex_flow:
        logger.warning("No cex_flow data")
        return events
    
    # Get top inflows to CEX (distribution candidates)
    top_inflows = cex_flow.get('top_inflows', [])
    for inf in top_inflows[:5]:
        try:
            amount = inf.get('amount', 0) / 1e6
            events.append({
                'amount_M_strk': round(amount, 2),
                'from_addr': inf.get('from', ''),
                'to_addr': '',  # CEX name is in 'to_cex' field, no raw addr
                'to_cex_name': inf.get('to_cex', ''),
                'direction': 'to_cex',
            })
        except Exception:
            continue
    
    # Get top outflows from CEX (accumulation candidates)
    top_outflows = cex_flow.get('top_outflows', [])
    for out in top_outflows[:5]:
        try:
            amount = out.get('amount', 0) / 1e6
            events.append({
                'amount_M_strk': round(amount, 2),
                'from_addr': '',  # CEX name is in 'from_cex' field
                'from_cex_name': out.get('from_cex', ''),
                'to_addr': out.get('to', ''),
                'direction': 'from_cex',
            })
        except Exception:
            continue
    
    # Sort by amount, take top 3
    events.sort(key=lambda x: -x['amount_M_strk'])
    top3 = events[:3]
    
    # Classify each — CEX side is known via cex_name, other side via seeds lookup
    classified = []
    for ev in top3:
        if ev['direction'] == 'to_cex':
            # from_addr is the non-CEX side, to_cex_name is CEX
            from_label, from_kind = label_address(ev['from_addr'], seeds)
            to_label = ev.get('to_cex_name', '?')
            to_kind = 'CEX'
        else:
            # from_cex_name is CEX, to_addr is non-CEX
            from_label = ev.get('from_cex_name', '?')
            from_kind = 'CEX'
            to_label, to_kind = label_address(ev['to_addr'], seeds)
        
        classification = classify_event(from_kind, to_kind, from_label, to_label)
        
        # Address to display
        from_addr_display = ev['from_addr'][:10] + '...' if ev.get('from_addr') else from_label
        to_addr_display = ev['to_addr'][:10] + '...' if ev.get('to_addr') else to_label
        
        classified.append({
            'amount_M_strk': ev['amount_M_strk'],
            'from': from_label,
            'from_kind': from_kind,
            'to': to_label,
            'to_kind': to_kind,
            'from_addr_short': from_addr_display,
            'to_addr_short': to_addr_display,
            'classification': classification['type'],
            'sentiment': classification['sentiment'],
            'meaning': classification['meaning'],
        })
    
    return classified


def build_cohort_read(events):
    """Aggregate interpretation of top 3 events."""
    if not events:
        return {
            'read': 'NO_DATA',
            'summary': 'Нет данных о крупных whale transactions за период',
        }
    
    types = [e['classification'] for e in events]
    sentiments = [e['sentiment'] for e in events]
    
    dist_count = sentiments.count('BEARISH')
    accum_count = sentiments.count('BULLISH')
    neutral_count = sentiments.count('NEUTRAL')
    
    if dist_count >= 2:
        read = 'WHALES_DISTRIBUTING'
        summary = f'{dist_count}/{len(events)} топ-транзакций = DISTRIBUTION (whales sending to exchanges). Bearish read.'
    elif accum_count >= 2:
        read = 'WHALES_ACCUMULATING'
        summary = f'{accum_count}/{len(events)} топ-транзакций = ACCUMULATION (from CEX to private). Bullish read.'
    elif dist_count > 0 and accum_count > 0:
        read = 'MIXED_ACTIVITY'
        summary = f'Смешанная активность: {dist_count} distribution + {accum_count} accumulation.'
    elif neutral_count >= 2:
        read = 'INFRA_ROUTING'
        summary = 'Топ-транзакции — infrastructure routing (bridges/rebalances), не market signal.'
    else:
        read = 'MIXED'
        summary = f'{dist_count}D/{accum_count}A/{neutral_count}N — mixed activity.'
    
    return {'read': read, 'summary': summary, 'dist_count': dist_count, 'accum_count': accum_count, 'neutral_count': neutral_count}


def main():
    logger.info("=" * 60)
    logger.info("WHALE AUTO ANALYSIS · top-3 events")
    logger.info("=" * 60)
    
    events = extract_top_events()
    cohort_read = build_cohort_read(events)
    
    result = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'events': events,
        'cohort_read': cohort_read,
    }
    
    logger.info(f"\n=== TOP {len(events)} WHALE EVENTS ===")
    for i, ev in enumerate(events, 1):
        sentiment_emoji = "🔴" if ev['sentiment'] == 'BEARISH' else "🟢" if ev['sentiment'] == 'BULLISH' else "⚪"
        logger.info(f"\n{i}. {sentiment_emoji} {ev['amount_M_strk']}M STRK · {ev['classification']}")
        logger.info(f"   {ev['from']} ({ev['from_kind']}) → {ev['to']} ({ev['to_kind']})")
        logger.info(f"   Meaning: {ev['meaning']}")
    
    logger.info(f"\n=== COHORT READ ===")
    logger.info(f"{cohort_read['read']}: {cohort_read['summary']}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
