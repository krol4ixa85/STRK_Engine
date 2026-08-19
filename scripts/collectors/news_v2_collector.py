#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_v2_collector.py — использует surf.io API для structured news.

Что даёт surf.io VS старый RSS aggregator:
  - 770 STRK-specific news (VS 2 items ранее)
  - Structured JSON (VS raw HTML parsing)
  - Project-tagged (VS keyword-only)
  - Multi-source: CHAINCATCHER, CRYPTOPOTATO, TECHFLOW, PANEWS, THEDEFIANT, TRADINGVIEW etc.

Что делаем:
  1. Fetch top 20 trending crypto news (1 credit)
  2. Fetch top 10 Starknet-specific news (1 credit)
  3. Categorize by topic: regulations, RWA, zk-privacy, L2, tokenization
  4. Detect basic sentiment (bull/bear keywords)
  5. Save to data/cache/news_v2.json

Cost: ~2-3 credits per run × 4 runs/day = 8-12 credits/day (~300/mo safe in Free tier)
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

SURF_API_BASE = 'https://api.asksurf.ai/v1'
SURF_API_KEY = os.getenv('SURF_API_KEY', '')  # optional

# Topic categorization keywords (lowercase)
TOPIC_KEYWORDS = {
    'regulations': [
        'sec', 'cftc', 'regulator', 'mica', 'compliance', 'lawsuit', 'court',
        'fine', 'settlement', 'enforcement', 'legislation', 'law', 'ban',
        'approve', 'reject', 'ruling', 'senate', 'congress',
    ],
    'tokenization': [
        'tokeniz', 'rwa', 'real-world', 'real world', 'buidl', 'ondo',
        'franklin', 'blackrock', 'treasury', 'bond', 'stock token',
    ],
    'zk_privacy': [
        'zero knowledge', 'zero-knowledge', 'zk-', 'zk rollup', 'zk-rollup',
        'zksync', 'starknet', 'stark', 'cairo', 'privacy', 'private',
        'quantum', 'stark', 'zcash', 'monero',
    ],
    'rwa': [
        'rwa', 'real-world', 'real world', 'treasury', 'blackrock', 'buidl',
        'ondo', 'franklin', 'centrifuge', 'maker rwa', 'tokenized bond',
    ],
    'l2': [
        'layer 2', 'layer-2', 'l2', 'arbitrum', 'optimism', 'base', 'zksync',
        'starknet', 'scroll', 'linea', 'rollup', 'polygon zkevm',
    ],
    'defi': [
        'defi', 'lending', 'uniswap', 'aave', 'compound', 'curve', 'maker',
        'morpho', 'yield', 'vault', 'liquidity',
    ],
    'staking_lst': [
        'staking', 'liquid staking', 'lst', 'lido', 'rocket pool', 'ethfi',
        'ether.fi', 'stake', 'validator', 'restaking', 'eigenlayer',
    ],
}

# Sentiment keywords
BULLISH_KEYWORDS = [
    'launch', 'partner', 'growth', 'up', 'rise', 'gain', 'record', 'high',
    'surge', 'rally', 'adoption', 'integration', 'expand', 'boost', 'positive',
    'buy', 'inflow', 'approve', 'success', 'milestone', 'breakthrough',
]
BEARISH_KEYWORDS = [
    'hack', 'exploit', 'lawsuit', 'crash', 'dump', 'sell-off', 'liquidat',
    'decline', 'fall', 'drop', 'bear', 'fear', 'warning', 'risk', 'concern',
    'delay', 'shutdown', 'ban', 'reject', 'loss', 'exit',
]
CATALYST_KEYWORDS = [
    'launch', 'mainnet', 'upgrade', 'partnership', 'listing', 'etf',
    'approve', 'live', 'release', 'announce', 'integration',
]


def surf_get(endpoint, params=None):
    """GET к surf.io API."""
    url = f'{SURF_API_BASE}/{endpoint}'
    headers = {}
    if SURF_API_KEY:
        headers['X-API-KEY'] = SURF_API_KEY
    
    try:
        r = requests.get(url, params=params or {}, headers=headers, timeout=30)
        if r.status_code == 402:
            print(f'[SURF] 402 — API key required for {endpoint}')
            return None
        if r.status_code == 401:
            print(f'[SURF] 401 Unauthorized')
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'[SURF {endpoint}] Error: {e}')
        return None


def classify_topics(text):
    """Return list of topics matched in text."""
    text_lower = text.lower()
    topics = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            topics.append(topic)
    return topics


def classify_sentiment(text):
    """Return sentiment: POSITIVE / NEGATIVE / CATALYST / NEUTRAL."""
    text_lower = text.lower()
    
    catalyst_count = sum(1 for kw in CATALYST_KEYWORDS if kw in text_lower)
    bull_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bear_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    
    # Strong catalyst = catalyst mention with bullish context
    if catalyst_count >= 2 and bull_count > bear_count:
        return 'CATALYST'
    elif bull_count > bear_count + 1:
        return 'POSITIVE'
    elif bear_count > bull_count + 1:
        return 'NEGATIVE'
    else:
        return 'NEUTRAL'


def process_news_item(item):
    """Enrich a news item with topics + sentiment."""
    title = item.get('title', '') or ''
    summary = item.get('summary', '') or ''
    full_text = f'{title} {summary}'
    
    return {
        'id': item.get('id'),
        'title': title,
        'summary': summary,
        'source': item.get('source', 'unknown'),
        'url': item.get('url', ''),
        'published_at': item.get('published_at'),
        'published_iso': datetime.fromtimestamp(item.get('published_at', 0), tz=timezone.utc).isoformat() if item.get('published_at') else '',
        'project': item.get('project_name', ''),
        'topics': classify_topics(full_text),
        'sentiment': classify_sentiment(full_text),
    }


def fetch_trending_crypto_news(limit=20):
    """Trending crypto news."""
    data = surf_get('news/feed', {
        'limit': limit,
        'sort_by': 'trending',
    })
    if not data:
        return []
    return data.get('data', [])


def fetch_starknet_news(limit=10):
    """Starknet-specific news."""
    data = surf_get('news/feed', {
        'limit': limit,
        'project': 'Starknet',
        'sort_by': 'recency',
    })
    if not data:
        return []
    return data.get('data', [])


def main():
    print('=' * 70)
    print('NEWS COLLECTOR v2 · surf.io API')
    print('=' * 70)
    print(f'Run at: {datetime.now(timezone.utc).isoformat()}\n')
    
    # 1. General trending
    print('[1/2] Fetching trending crypto news...')
    trending = fetch_trending_crypto_news(20)
    print(f'  ✓ {len(trending)} items')
    
    # 2. Starknet-specific
    print('\n[2/2] Fetching Starknet news...')
    strk_news = fetch_starknet_news(10)
    print(f'  ✓ {len(strk_news)} items')
    
    # Process
    trending_processed = [process_news_item(item) for item in trending]
    strk_processed = [process_news_item(item) for item in strk_news]
    
    # Aggregate stats
    all_items = trending_processed + strk_processed
    
    # Dedupe by id
    seen_ids = set()
    unique_items = []
    for item in all_items:
        item_id = item.get('id')
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            unique_items.append(item)
    
    # Sentiment breakdown
    sentiment_counts = {'POSITIVE': 0, 'NEGATIVE': 0, 'CATALYST': 0, 'NEUTRAL': 0}
    for item in unique_items:
        sentiment_counts[item['sentiment']] = sentiment_counts.get(item['sentiment'], 0) + 1
    
    # Topic breakdown
    topic_counts = {topic: 0 for topic in TOPIC_KEYWORDS.keys()}
    for item in unique_items:
        for topic in item['topics']:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    
    # Overall signal
    pos_score = sentiment_counts['POSITIVE'] + sentiment_counts['CATALYST'] * 2
    neg_score = sentiment_counts['NEGATIVE'] * 1.5
    if pos_score > neg_score * 1.5:
        overall_signal = 'POSITIVE'
    elif neg_score > pos_score * 1.5:
        overall_signal = 'NEGATIVE'
    else:
        overall_signal = 'NEUTRAL'
    
    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'surf.io',
        'total_items': len(unique_items),
        'strk_specific_count': len(strk_processed),
        'trending_count': len(trending_processed),
        'overall_signal': overall_signal,
        'sentiment_breakdown': sentiment_counts,
        'topic_breakdown': topic_counts,
        'strk_news': strk_processed,
        'trending_news': trending_processed[:10],  # top 10 only in feed
        # legacy compatibility for dashboard
        'strk_news_count': len(strk_processed),
        'top_news': strk_processed[:5] if strk_processed else trending_processed[:5],
        'interpretation': f'Overall: {overall_signal} · Topics: ' + ', '.join([t for t, c in topic_counts.items() if c > 0][:3]),
    }
    
    print(f'\n{"=" * 70}')
    print(f'RESULTS:')
    print(f'  Total items: {len(unique_items)}')
    print(f'  STRK-specific: {len(strk_processed)}')
    print(f'  Overall signal: {overall_signal}')
    print(f'  Sentiment: {sentiment_counts}')
    print(f'  Topics: {dict((k, v) for k, v in topic_counts.items() if v > 0)}')
    print(f'{"=" * 70}')
    
    # Save
    output_path = CACHE_DIR / 'news_v2.json'
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f'\n✓ Saved: {output_path}')
    
    # ALSO overwrite old news_aggregator.json for dashboard compatibility
    legacy_path = CACHE_DIR / 'news_aggregator.json'
    with open(legacy_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f'✓ Also updated legacy: {legacy_path}')
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
