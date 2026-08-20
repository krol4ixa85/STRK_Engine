"""
News → Asset Impact Classifier v1
==================================
Собирает news из multiple sources → классифицирует по:
1. Mentioned assets (STRK, LINK, ETHFI, etc)
2. Sentiment (bullish/bearish/neutral)
3. Impact severity (high/medium/low)
4. Suggested action_hint (BUY, HOLD, TRIM, EXIT, WATCH)

Sources:
- RSS feeds: CoinDesk, TheBlock, CoinTelegraph, Decrypt
- Whale alerts (Amber, Wintermute, Alameda-style movements)
- CEX transfer alerts (deposits to Binance/OKX = potential sell)

Output: data/cache/news_impact.json
"""
import os
import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import feedparser
except ImportError:
    feedparser = None

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

# RSS feeds
RSS_FEEDS = [
    ('CoinDesk', 'https://www.coindesk.com/arc/outboundfeeds/rss/'),
    ('TheBlock', 'https://www.theblock.co/rss.xml'),
    ('CoinTelegraph', 'https://cointelegraph.com/rss'),
    ('Decrypt', 'https://decrypt.co/feed'),
    ('Starknet Blog', 'https://www.starknet.io/blog/rss.xml'),
]

# Assets to track (all LAB monitoring universe)
TRACKED_ASSETS = {
    'STRK': ['starknet', 'strk', 'shinobi', 'strkbtc', 'cairo'],
    'LINK': ['chainlink', 'link', 'ccip'],
    'ETHFI': ['ether.fi', 'ethfi', 'etherfi'],
    'MORPHO': ['morpho'],
    'ARB': ['arbitrum', 'arb '],
    'OP': ['optimism', 'op token'],
    'MNT': ['mantle', 'mnt'],
    'ONDO': ['ondo'],
    'CFG': ['centrifuge', 'cfg'],
    'LDO': ['lido', 'ldo'],
    'EIGEN': ['eigenlayer', 'eigen'],
    'AAVE': ['aave'],
    'PENDLE': ['pendle'],
    'CRV': ['curve', 'crv'],
    'UNI': ['uniswap', 'uni token'],
    'TAO': ['bittensor', 'tao'],
    'RNDR': ['render', 'rndr'],
    'FET': ['fetch.ai', 'fet'],
    'AIXBT': ['aixbt'],
    'DOGE': ['dogecoin', 'doge'],
    'PEPE': ['pepe coin'],
    'BONK': ['bonk'],
    'WIF': ['dogwifhat', 'wif '],
    'BTC': ['bitcoin', 'btc'],
    'ETH': ['ethereum', 'eth'],
}

# ============================================================
# CLASSIFICATION KEYWORDS
# ============================================================

BULLISH_KEYWORDS = {
    # Product / Ecosystem
    'launch': 2, 'launches': 2, 'launched': 2,
    'mainnet': 3, 'upgrade': 3, 'protocol upgrade': 4,
    'partnership': 2, 'integration': 2, 'integrated': 2,
    'adoption': 2, 'listed': 2, 'listing': 2,
    'staking': 1, 'yield': 1, 'apy': 1,
    # Financial
    'inflow': 2, 'accumulate': 3, 'accumulation': 3,
    'buy': 1, 'bought': 1, 'purchase': 1, 
    'bullish': 2, 'rally': 2, 'surge': 2, 'moon': 1,
    'breakout': 3, 'ath': 2, 'all-time high': 3,
    'institution': 2, 'etf': 3, 'approved': 2,
    'record': 2, 'milestone': 2,
    # Innovation
    'privacy': 1, 'security': 1, 'faster': 1,
    'lower fees': 2, 'cheaper': 1,
}

BEARISH_KEYWORDS = {
    # Financial / Distribution
    'unlock': 3, 'unlocks': 3, 'unlocked': 2,
    'dump': 3, 'sell': 2, 'sold': 1, 'selling pressure': 3,
    'outflow': 3, 'distribution': 3,
    'liquidation': 3, 'liquidated': 3,
    'crash': 4, 'plunge': 3, 'plummet': 3, 'tank': 3,
    'bearish': 2, 'downturn': 2, 'downside': 1,
    'drop': 1, 'declined': 1, 'fall': 1, 'fell': 1,
    # Whale movements
    'binance deposit': 4, 'okx deposit': 3, 'exchange deposit': 3,
    'moved to': 2, 'transfers to binance': 4, 'transfers to okx': 3,
    'whale moves': 3, 'whale deposits': 3,
    # Fundamental risk
    'hack': 5, 'hacked': 5, 'exploit': 5, 'exploited': 5,
    'rug': 5, 'scam': 4, 'fraud': 4,
    'sec': 3, 'lawsuit': 3, 'investigation': 3,
    'delist': 4, 'delisted': 4, 'delisting': 4,
    'restriction': 2, 'regulation': 1,
    'downgrade': 2, 'warning': 2,
}

# Whale wallets keywords (institutional)
WHALE_ENTITIES = {
    'amber': 'Amber Group',
    'wintermute': 'Wintermute',
    'jump': 'Jump Trading',
    'alameda': 'Alameda',
    'binance': 'Binance',
    'okx': 'OKX',
    'coinbase': 'Coinbase',
    'ftx': 'FTX (defunct)',
}

# ============================================================
# CLASSIFIER
# ============================================================

def detect_assets(text):
    """Return list of tracked assets mentioned in text."""
    text_lower = text.lower()
    found = set()
    for asset, keywords in TRACKED_ASSETS.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
                found.add(asset)
                break
    return sorted(found)

def score_sentiment(text):
    """Return (bullish_score, bearish_score, matched_bullish, matched_bearish)."""
    text_lower = text.lower()
    
    bullish_score = 0
    bearish_score = 0
    matched_bullish = []
    matched_bearish = []
    
    for kw, weight in BULLISH_KEYWORDS.items():
        if kw in text_lower:
            bullish_score += weight
            matched_bullish.append(kw)
    
    for kw, weight in BEARISH_KEYWORDS.items():
        if kw in text_lower:
            bearish_score += weight
            matched_bearish.append(kw)
    
    return bullish_score, bearish_score, matched_bullish, matched_bearish

def detect_whales(text):
    """Return list of whale entities mentioned."""
    text_lower = text.lower()
    return [name for kw, name in WHALE_ENTITIES.items() if kw in text_lower]

def classify_impact(bullish, bearish, has_whale, has_transfer):
    """
    Return (sentiment, severity, action_hint, layman_ru).
    """
    net_score = bullish - bearish
    
    # High severity signals
    high_impact = (abs(net_score) >= 5) or has_transfer or has_whale
    
    if net_score >= 5:
        return ('BULLISH', 'high' if high_impact else 'medium', 
                'BUY_ACCUMULATE', 
                'Сильный bullish катализатор — можно докупать частично, watch confirmation')
    elif net_score >= 2:
        return ('BULLISH', 'medium',
                'HOLD_POSITIVE',
                'Умеренно bullish — держать позицию, не делать резких действий')
    elif net_score <= -5:
        return ('BEARISH', 'high' if high_impact else 'medium',
                'EXIT_TRIM',
                'Сильный bearish катализатор — trim позицию 50-100%')
    elif net_score <= -2:
        return ('BEARISH', 'medium',
                'WATCH_RISK',
                'Bearish signal — не докупать, follow closely, готовить exit plan')
    else:
        return ('NEUTRAL', 'low',
                'NO_ACTION',
                'Нейтральная новость — влияние минимальное')

def classify_article(title, description=''):
    """Full classification of one news article."""
    combined = f"{title} {description}"
    
    assets = detect_assets(combined)
    if not assets:
        return None  # No relevant asset
    
    bullish, bearish, matched_b, matched_bear = score_sentiment(combined)
    whales = detect_whales(combined)
    has_whale = len(whales) > 0
    has_transfer = any(kw in combined.lower() for kw in ['deposit', 'transfer', 'moved', 'sent to'])
    
    sentiment, severity, action, layman = classify_impact(bullish, bearish, has_whale, has_transfer)
    
    return {
        'assets': assets,
        'sentiment': sentiment,
        'severity': severity,
        'action_hint': action,
        'layman_ru': layman,
        'bullish_score': bullish,
        'bearish_score': bearish,
        'net_score': bullish - bearish,
        'whales_mentioned': whales,
        'has_cex_transfer': has_transfer,
        'matched_keywords': {
            'bullish': matched_b[:5],
            'bearish': matched_bear[:5],
        }
    }

# ============================================================
# FETCH FROM RSS
# ============================================================

def fetch_rss_articles(feed_name, feed_url, max_articles=15, hours_back=48):
    """Fetch recent articles from one RSS feed."""
    if not feedparser:
        return []
    
    try:
        feed = feedparser.parse(feed_url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        articles = []
        
        for entry in feed.entries[:max_articles]:
            # Parse published date
            pub_ts = None
            for attr in ('published_parsed', 'updated_parsed'):
                if hasattr(entry, attr) and getattr(entry, attr):
                    try:
                        pub_ts = datetime(*getattr(entry, attr)[:6], tzinfo=timezone.utc)
                        break
                    except:
                        pass
            
            if pub_ts and pub_ts < cutoff:
                continue
            
            title = entry.get('title', '').strip()
            desc = entry.get('summary', '') or entry.get('description', '')
            # Strip HTML from description
            desc = re.sub(r'<[^>]+>', '', desc).strip()[:500]
            
            articles.append({
                'source': feed_name,
                'title': title,
                'description': desc,
                'link': entry.get('link', ''),
                'published': pub_ts.isoformat() if pub_ts else None,
            })
        return articles
    except Exception as e:
        print(f'  ⚠ {feed_name}: {e}')
        return []

# ============================================================
# AGGREGATION
# ============================================================

def aggregate_by_asset(classified_articles):
    """Aggregate news per asset."""
    per_asset = {}
    for article in classified_articles:
        for asset in article['classification']['assets']:
            if asset not in per_asset:
                per_asset[asset] = {
                    'asset': asset,
                    'bullish_articles': [],
                    'bearish_articles': [],
                    'neutral_articles': [],
                    'net_score': 0,
                    'severity_high_count': 0,
                    'has_cex_transfer_alert': False,
                    'whales_mentioned': set(),
                }
            
            data = per_asset[asset]
            classification = article['classification']
            data['net_score'] += classification['net_score']
            
            if classification['severity'] == 'high':
                data['severity_high_count'] += 1
            
            if classification['has_cex_transfer']:
                data['has_cex_transfer_alert'] = True
            
            for whale in classification.get('whales_mentioned', []):
                data['whales_mentioned'].add(whale)
            
            article_summary = {
                'title': article['title'],
                'source': article['source'],
                'link': article['link'],
                'published': article['published'],
                'action_hint': classification['action_hint'],
                'layman_ru': classification['layman_ru'],
                'severity': classification['severity'],
                'net_score': classification['net_score'],
            }
            
            if classification['sentiment'] == 'BULLISH':
                data['bullish_articles'].append(article_summary)
            elif classification['sentiment'] == 'BEARISH':
                data['bearish_articles'].append(article_summary)
            else:
                data['neutral_articles'].append(article_summary)
    
    # Compute overall action per asset
    for asset, data in per_asset.items():
        data['whales_mentioned'] = sorted(data['whales_mentioned'])
        n_bull = len(data['bullish_articles'])
        n_bear = len(data['bearish_articles'])
        
        # Overall verdict
        if data['net_score'] >= 5 and data['severity_high_count'] > 0:
            data['overall_action'] = 'STRONG_BUY_CATALYST'
            data['overall_layman'] = f'Сильный bullish momentum — {n_bull} позитивных news, катализатор для входа'
        elif data['net_score'] >= 3:
            data['overall_action'] = 'POSITIVE_MOMENTUM'
            data['overall_layman'] = f'Умеренно bullish — {n_bull} позитивных, {n_bear} негативных. Держать позицию.'
        elif data['net_score'] <= -5 and data['severity_high_count'] > 0:
            data['overall_action'] = 'STRONG_EXIT_SIGNAL'
            data['overall_layman'] = f'Сильный bearish signal — {n_bear} негативных news + whale movements. Trim позицию 75-100%.'
        elif data['net_score'] <= -3:
            data['overall_action'] = 'NEGATIVE_MOMENTUM'
            data['overall_layman'] = f'Умеренно bearish — {n_bear} негативных. Не докупать, готовить exit план.'
        else:
            data['overall_action'] = 'NEUTRAL'
            data['overall_layman'] = f'Нейтральные новости — {n_bull} bull, {n_bear} bear. Watch mode.'
        
        # Add CEX transfer alert flag
        if data['has_cex_transfer_alert']:
            data['overall_layman'] = '⚠ WHALE CEX TRANSFER DETECTED. ' + data['overall_layman']
    
    return per_asset

# ============================================================
# MAIN
# ============================================================

def main():
    print('=== News Impact Classifier v1 ===\n')
    
    if not feedparser:
        print('❌ feedparser not installed. Run: pip install feedparser')
        sys.exit(1)
    
    # Fetch RSS feeds
    all_articles = []
    for feed_name, feed_url in RSS_FEEDS:
        print(f'⏳ Fetching {feed_name}...')
        articles = fetch_rss_articles(feed_name, feed_url)
        print(f'  ↳ {len(articles)} articles fetched')
        all_articles.extend(articles)
    
    print(f'\nTotal articles: {len(all_articles)}')
    
    # Classify
    classified = []
    for article in all_articles:
        classification = classify_article(article['title'], article.get('description', ''))
        if classification:
            classified.append({
                **article,
                'classification': classification
            })
    
    print(f'Relevant to tracked assets: {len(classified)}')
    
    # Aggregate per asset
    per_asset = aggregate_by_asset(classified)
    
    # Prepare output
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'total_articles_scanned': len(all_articles),
        'relevant_articles': len(classified),
        'assets_with_news': len(per_asset),
        'per_asset': per_asset,
        'top_bullish_articles': sorted(
            [a for a in classified if a['classification']['sentiment'] == 'BULLISH'],
            key=lambda x: -x['classification']['net_score']
        )[:10],
        'top_bearish_articles': sorted(
            [a for a in classified if a['classification']['sentiment'] == 'BEARISH'],
            key=lambda x: x['classification']['net_score']
        )[:10],
        # Critical alerts (high severity + whale transfers)
        'critical_alerts': [
            {
                'asset': a,
                'action': data['overall_action'],
                'layman': data['overall_layman'],
                'severity_high_count': data['severity_high_count'],
                'whales': data['whales_mentioned']
            }
            for a, data in per_asset.items()
            if data['severity_high_count'] > 0 or data['has_cex_transfer_alert']
        ]
    }
    
    output_path = CACHE_DIR / 'news_impact.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f'\n✓ News impact classified: {output_path}')
    print(f'\n=== TOP CRITICAL ALERTS ===')
    for alert in output['critical_alerts'][:5]:
        print(f'\n  {alert["asset"]} · {alert["action"]}')
        print(f'    {alert["layman"]}')
        if alert['whales']:
            print(f'    Whales: {", ".join(alert["whales"])}')
    
    print(f'\n=== PER-ASSET SUMMARY ===')
    for asset, data in sorted(per_asset.items(), key=lambda x: abs(x[1]['net_score']), reverse=True)[:8]:
        n_bull = len(data['bullish_articles'])
        n_bear = len(data['bearish_articles'])
        print(f'  {asset}: net={data["net_score"]:+d}, {n_bull} bull, {n_bear} bear · {data["overall_action"]}')

if __name__ == '__main__':
    main()
