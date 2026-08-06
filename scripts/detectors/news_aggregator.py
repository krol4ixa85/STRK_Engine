#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_aggregator.py — RSS news aggregator focused on STRK/Starknet

Sources:
  · CryptoPanic (API - has free tier)
  · CoinDesk RSS
  · The Block RSS  
  · Cointelegraph RSS
  · Decrypt RSS

Filters for STRK/Starknet related news.
Simple sentiment classification via keywords.

Signals:
  · POSITIVE_NEWS: partnership, upgrade, adoption, listing
  · NEGATIVE_NEWS: hack, exploit, delay, downgrade
  · NEUTRAL_NEWS: general updates
  · CATALYST_EVENT: major upcoming event (unlock, testnet, mainnet)
"""

import os
import sys
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'news_aggregator.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('news')

# RSS sources (free, no auth needed)
SOURCES = [
    {'name': 'CoinDesk', 'url': 'https://www.coindesk.com/arc/outboundfeeds/rss/', 'weight': 3},
    {'name': 'CoinTelegraph', 'url': 'https://cointelegraph.com/rss', 'weight': 2},
    {'name': 'Decrypt', 'url': 'https://decrypt.co/feed', 'weight': 2},
    {'name': 'CryptoNews', 'url': 'https://cryptonews.com/news/feed', 'weight': 1},
    {'name': 'Bitcoin.com', 'url': 'https://news.bitcoin.com/feed/', 'weight': 1},
]

# STRK-related keywords (case insensitive, broader net)
STRK_KEYWORDS = [
    'starknet', 'stark net', 'strk',
    'starkware', 'stark ware',
    'cairo language', 'cairo lang',
    'layer 2', 'layer-2', 'l2',
    'zk-rollup', 'zk rollup', 'zk-rollups',
    'ethereum scaling', 'eth scaling',
    'arbitrum', 'optimism', 'zksync',  # L2 competitors matter
    'base network', 'linea',
    'validity proof', 'validity rollup',
]

# Sentiment keywords
POSITIVE_KW = [
    'partnership', 'partner', 'integration', 'adoption', 'launch', 'mainnet',
    'upgrade', 'grant', 'fund', 'raise', 'invest', 'growth', 'expand',
    'listing', 'list', 'record', 'high', 'ath', 'all-time high',
    'milestone', 'success', 'breakthrough', 'innovation',
]

NEGATIVE_KW = [
    'hack', 'exploit', 'vulnerability', 'bug', 'delay', 'postpone',
    'crash', 'plunge', 'dump', 'sell-off', 'decline', 'lawsuit',
    'sec', 'regulatory', 'concern', 'warning', 'risk', 'suspend',
    'halt', 'freeze', 'ban',
]

CATALYST_KW = [
    'unlock', 'vesting', 'testnet', 'mainnet', 'ipo', 'airdrop',
    'season 2', 'incentive', 'liquidity mining', 'staking rewards',
    'token generation', 'tge',
]


def fetch_rss(url, timeout=15):
    """Fetch and parse RSS feed."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; STRK-Engine/1.0)',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        
        # Parse XML
        root = ET.fromstring(data)
        
        # Handle both RSS 2.0 and Atom
        items = []
        for item in root.iter('item'):
            entry = {}
            for elem in item:
                tag = elem.tag.lower().split('}')[-1]  # remove namespace
                if tag == 'title':
                    entry['title'] = (elem.text or '').strip()
                elif tag == 'description':
                    entry['description'] = re.sub(r'<[^>]+>', '', elem.text or '')[:500]
                elif tag == 'link':
                    entry['link'] = (elem.text or '').strip()
                elif tag == 'pubdate' or tag == 'published':
                    entry['pub_date'] = (elem.text or '').strip()
            if entry.get('title'):
                items.append(entry)
        
        # Atom format
        if not items:
            for entry in root.iter('{http://www.w3.org/2005/Atom}entry'):
                e = {}
                for child in entry:
                    tag = child.tag.split('}')[-1]
                    if tag == 'title':
                        e['title'] = (child.text or '').strip()
                    elif tag == 'summary':
                        e['description'] = re.sub(r'<[^>]+>', '', child.text or '')[:500]
                    elif tag == 'link':
                        e['link'] = child.get('href', '')
                    elif tag == 'published':
                        e['pub_date'] = (child.text or '').strip()
                if e.get('title'):
                    items.append(e)
        
        return items
    except Exception as e:
        logger.error(f"RSS error {url}: {e}")
        return []


def is_strk_related(text):
    """Check if text mentions STRK/Starknet."""
    text_lower = text.lower()
    for kw in STRK_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False


def classify_sentiment(text):
    """Simple keyword-based sentiment."""
    text_lower = text.lower()
    
    pos_score = sum(1 for kw in POSITIVE_KW if kw in text_lower)
    neg_score = sum(1 for kw in NEGATIVE_KW if kw in text_lower)
    cat_score = sum(1 for kw in CATALYST_KW if kw in text_lower)
    
    if cat_score >= 1:
        return 'CATALYST'
    elif pos_score > neg_score:
        return 'POSITIVE'
    elif neg_score > pos_score:
        return 'NEGATIVE'
    else:
        return 'NEUTRAL'


def parse_date(date_str):
    """Parse various RSS date formats."""
    if not date_str:
        return None
    
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%SZ',
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def main():
    logger.info("=" * 60)
    logger.info("NEWS AGGREGATOR · STRK/Starknet coverage")
    logger.info("=" * 60)
    
    all_strk_news = []
    all_general_news = []
    
    for source in SOURCES:
        logger.info(f"\n  Fetching {source['name']}...")
        items = fetch_rss(source['url'])
        logger.info(f"    Got {len(items)} items")
        
        for item in items:
            text = f"{item.get('title', '')} {item.get('description', '')}"
            
            entry = {
                'source': source['name'],
                'title': item.get('title', ''),
                'link': item.get('link', ''),
                'pub_date': item.get('pub_date', ''),
                'weight': source['weight'],
            }
            
            # Parse date
            dt = parse_date(item.get('pub_date', ''))
            if dt:
                entry['ts'] = dt.isoformat()
                # Skip if older than 7 days
                if dt < datetime.now(timezone.utc) - timedelta(days=7):
                    continue
            
            if is_strk_related(text):
                entry['sentiment'] = classify_sentiment(text)
                entry['description'] = item.get('description', '')[:300]
                all_strk_news.append(entry)
            else:
                # Keep some general news for macro context
                all_general_news.append(entry)
    
    # Sort by date (recent first)
    all_strk_news.sort(key=lambda x: x.get('ts', ''), reverse=True)
    
    # Classify overall signal
    sentiments = [n['sentiment'] for n in all_strk_news]
    pos_count = sentiments.count('POSITIVE')
    neg_count = sentiments.count('NEGATIVE')
    cat_count = sentiments.count('CATALYST')
    neu_count = sentiments.count('NEUTRAL')
    
    if cat_count >= 2:
        overall = 'CATALYST_EVENTS'
        interpretation = f'{cat_count} catalyst events detected'
    elif pos_count > neg_count * 2:
        overall = 'BULLISH_NEWS'
        interpretation = f'{pos_count} positive vs {neg_count} negative'
    elif neg_count > pos_count * 2:
        overall = 'BEARISH_NEWS'
        interpretation = f'{neg_count} negative vs {pos_count} positive'
    elif pos_count == 0 and neg_count == 0 and neu_count < 3:
        overall = 'NO_COVERAGE'
        interpretation = 'Very little STRK news coverage'
    else:
        overall = 'NEUTRAL'
        interpretation = f'{pos_count}P/{neg_count}N/{cat_count}C/{neu_count}Neu'
    
    logger.info(f"\n=== SUMMARY ===")
    logger.info(f"STRK-related news (7d): {len(all_strk_news)}")
    logger.info(f"Overall signal: {overall}")
    logger.info(f"Breakdown: {pos_count} POS · {neg_count} NEG · {cat_count} CAT · {neu_count} NEU")
    
    if all_strk_news[:5]:
        logger.info(f"\nRecent STRK news:")
        for n in all_strk_news[:5]:
            logger.info(f"  · [{n['sentiment']}] {n['title'][:80]}...")
            logger.info(f"    {n['source']} · {n.get('pub_date', '')[:20]}")
    
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'overall_signal': overall,
        'interpretation': interpretation,
        'strk_news_count': len(all_strk_news),
        'sentiment_breakdown': {
            'positive': pos_count,
            'negative': neg_count,
            'catalyst': cat_count,
            'neutral': neu_count,
        },
        'top_news': all_strk_news[:10],
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
