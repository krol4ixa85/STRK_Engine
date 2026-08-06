#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
twitter_nitter.py — Twitter tracking via Nitter RSS (no X API needed)

X API стоит $100/месяц — обходим через Nitter instances.
Nitter — open-source альтернатива Twitter web которая даёт RSS.

Public Nitter instances (rotating for reliability):
  · https://nitter.net
  · https://nitter.privacydev.net
  · https://nitter.poast.org
  · https://nitter.cz
  · https://nitter.d420.de

Отслеживаем:
  · @Starknet (official)
  · @StarkWareLtd
  · @starknetfndn (foundation)
  · @lambdaclass
  · @NethermindEth
  · @0xSpaceShard
  · @argentHQ (главный кошелек)
  · @BraavosWallet
  · @0xDMCcam (СЕО Starkware)

Signals:
  · HIGH_ENGAGEMENT: много tweets + retweets
  · ANNOUNCEMENT: keyword-based detection (launch, partnership, upgrade)
  · SILENCE: 0 tweets 7d от главных аккаунтов
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
OUTPUT_FILE = CACHE_DIR / 'twitter_nitter.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('twitter')

# Rotating Nitter instances
NITTER_INSTANCES = [
    'https://nitter.privacydev.net',
    'https://nitter.poast.org',
    'https://nitter.net',
    'https://nitter.cz',
]

# Accounts to track
ACCOUNTS = [
    {'handle': 'Starknet', 'weight': 5, 'role': 'official'},
    {'handle': 'StarkWareLtd', 'weight': 4, 'role': 'company'},
    {'handle': 'starknetfndn', 'weight': 3, 'role': 'foundation'},
    {'handle': 'lambdaclass', 'weight': 2, 'role': 'infra'},
    {'handle': 'NethermindEth', 'weight': 2, 'role': 'infra'},
    {'handle': 'argentHQ', 'weight': 2, 'role': 'wallet'},
    {'handle': 'BraavosWallet', 'weight': 1, 'role': 'wallet'},
]

# Keywords for detection
POSITIVE_KW = [
    'launch', 'launched', 'launching', 'partnership', 'partnered',
    'upgrade', 'update', 'release', 'milestone', 'record',
    'grant', 'fund', 'raise', 'investment', 'listing',
    'integration', 'live', 'mainnet',
]

CATALYST_KW = [
    'announcement', 'announcing', 'unveil', 'reveal', 'introducing',
    'season', 'quest', 'airdrop', 'reward', 'incentive',
    'testnet', 'v0.', 'v1.', 'v2.',
]

NEGATIVE_KW = [
    'delay', 'postpone', 'issue', 'bug', 'incident', 'downtime',
    'hack', 'exploit', 'concern', 'apology',
]


def fetch_nitter_rss(handle, tries=3):
    """Fetch RSS from Nitter, rotating instances."""
    for instance in NITTER_INSTANCES[:tries]:
        url = f"{instance}/{handle}/rss"
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; STRK-Engine/1.0)',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            
            # Parse XML
            root = ET.fromstring(data)
            items = []
            for item in root.iter('item'):
                entry = {}
                for elem in item:
                    tag = elem.tag.lower().split('}')[-1]
                    if tag == 'title':
                        entry['title'] = (elem.text or '').strip()
                    elif tag == 'description':
                        entry['description'] = re.sub(r'<[^>]+>', '', elem.text or '')[:500]
                    elif tag == 'link':
                        entry['link'] = (elem.text or '').strip()
                    elif tag == 'pubdate':
                        entry['pub_date'] = (elem.text or '').strip()
                if entry.get('title'):
                    items.append(entry)
            
            if items:
                logger.info(f"    Fetched {len(items)} tweets from {instance}")
                return items
        except Exception as e:
            logger.debug(f"    Failed {instance}: {e}")
            continue
    
    logger.warning(f"    All Nitter instances failed for @{handle}")
    return []


def classify_tweet(text):
    """Simple keyword classification."""
    text_lower = text.lower()
    
    catalyst = sum(1 for kw in CATALYST_KW if kw in text_lower)
    positive = sum(1 for kw in POSITIVE_KW if kw in text_lower)
    negative = sum(1 for kw in NEGATIVE_KW if kw in text_lower)
    
    if catalyst >= 1:
        return 'CATALYST'
    elif positive > negative:
        return 'POSITIVE'
    elif negative > positive:
        return 'NEGATIVE'
    else:
        return 'NEUTRAL'


def parse_pub_date(date_str):
    if not date_str:
        return None
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
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


def analyze_twitter():
    """Fetch and analyze tweets from tracked accounts."""
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)
    
    all_tweets = []
    per_account = {}
    
    for acc in ACCOUNTS:
        logger.info(f"\n  Fetching @{acc['handle']} ({acc['role']})...")
        tweets = fetch_nitter_rss(acc['handle'])
        
        recent = []
        for t in tweets:
            dt = parse_pub_date(t.get('pub_date', ''))
            if dt and dt > cutoff_7d:
                text = f"{t.get('title', '')} {t.get('description', '')}"
                sentiment = classify_tweet(text)
                entry = {
                    'handle': acc['handle'],
                    'weight': acc['weight'],
                    'role': acc['role'],
                    'title': t.get('title', ''),
                    'link': t.get('link', ''),
                    'timestamp': dt.isoformat(),
                    'sentiment': sentiment,
                }
                recent.append(entry)
                all_tweets.append(entry)
        
        per_account[acc['handle']] = {
            'weight': acc['weight'],
            'role': acc['role'],
            'tweets_7d': len(recent),
            'tweets': recent[:5],
        }
    
    # === Aggregate analysis ===
    total = len(all_tweets)
    sentiment_counts = {'POSITIVE': 0, 'CATALYST': 0, 'NEGATIVE': 0, 'NEUTRAL': 0}
    for t in all_tweets:
        sentiment_counts[t['sentiment']] = sentiment_counts.get(t['sentiment'], 0) + 1
    
    # Weighted sentiment
    weighted_pos = sum(t['weight'] for t in all_tweets if t['sentiment'] in ('POSITIVE', 'CATALYST'))
    weighted_neg = sum(t['weight'] for t in all_tweets if t['sentiment'] == 'NEGATIVE')
    
    # === Classification ===
    signal = 'NORMAL'
    interpretation = ''
    
    if sentiment_counts.get('CATALYST', 0) >= 2:
        signal = 'CATALYST_TWEETS'
        interpretation = f'{sentiment_counts["CATALYST"]} catalyst tweets — potential announcement window'
    elif weighted_pos > weighted_neg * 2 and total >= 5:
        signal = 'POSITIVE_MOMENTUM'
        interpretation = f'{sentiment_counts["POSITIVE"]}P + {sentiment_counts["CATALYST"]}C vs {sentiment_counts["NEGATIVE"]}N — bullish tone'
    elif weighted_neg > weighted_pos * 2:
        signal = 'NEGATIVE_TONE'
        interpretation = f'{sentiment_counts["NEGATIVE"]}N tweets dominant — concerning tone'
    elif total == 0:
        signal = 'SILENCE'
        interpretation = 'No tweets from tracked accounts in 7d — Nitter might be down or team is silent'
    elif total < 5:
        signal = 'LOW_ACTIVITY'
        interpretation = f'Only {total} tweets from all tracked accounts (7d)'
    else:
        signal = 'NORMAL'
        interpretation = f'{total} tweets · {sentiment_counts["POSITIVE"]}P/{sentiment_counts["CATALYST"]}C/{sentiment_counts["NEGATIVE"]}N'
    
    return {
        'as_of': now.isoformat(),
        'signal': signal,
        'interpretation': interpretation,
        'total_tweets_7d': total,
        'sentiment_breakdown': sentiment_counts,
        'weighted_positive': weighted_pos,
        'weighted_negative': weighted_neg,
        'per_account': per_account,
        'recent_catalysts': [t for t in all_tweets if t['sentiment'] == 'CATALYST'][:5],
    }


def main():
    logger.info("=" * 60)
    logger.info("TWITTER MONITORING · via Nitter RSS")
    logger.info("=" * 60)
    
    result = analyze_twitter()
    
    logger.info(f"\n=== SUMMARY ===")
    logger.info(f"Signal: {result['signal']}")
    logger.info(f"Interpretation: {result['interpretation']}")
    logger.info(f"Total tweets 7d: {result['total_tweets_7d']}")
    logger.info(f"Sentiment: {result['sentiment_breakdown']}")
    
    if result.get('recent_catalysts'):
        logger.info(f"\n=== RECENT CATALYST TWEETS ===")
        for t in result['recent_catalysts']:
            logger.info(f"  @{t['handle']}: {t['title'][:100]}...")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
