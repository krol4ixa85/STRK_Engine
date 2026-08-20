"""
News & Events Classifier v3 · Surf primary + RSS fallback
==========================================================
Реализация hybrid pattern:
1. PRIMARY: Surf CLI (30+ sources, AI-classified sentiment)
2. FALLBACK: RSS feeds (CoinDesk, TheBlock, CoinTelegraph, Decrypt)
3. MERGE: Surf data + RSS data → dedupe → apply methodology layer
4. Apply methodology:
   - Type classification (security/unlock/upgrade/...)
   - Phase modifier (STRK phase влияет)
   - FOMO detection
   - Route system (home max 3 / macro / archive)
   - Decision Contract linkage

Requires:
    SURF_API_KEY (primary source)
    Falls back to RSS if Surf unavailable

Output: data/cache/surf_events.json
"""
import os
import sys
import json
import re
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import feedparser
except ImportError:
    feedparser = None

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# CONFIG (per methodology)
# ============================================================
WATCHLIST_ASSETS = {
    'STRK', 'STARKNET', 'ETH', 'BTC',
    'LINK', 'ETHFI', 'MORPHO', 'ONDO', 'CFG',
    'ARB', 'OP', 'MNT', 'LDO', 'EIGEN', 'AAVE', 'PENDLE',
    'TAO', 'RNDR', 'AIXBT', 'FET',
    'DOGE', 'PEPE', 'BONK', 'WIF',
}

WATCHLIST_THEMES = {
    'zk', 'zero-knowledge', 'zero knowledge',
    'l2', 'layer 2', 'layer-2', 'rollup',
    'rwa', 'real world asset', 'tokenized',
    'lst', 'liquid staking',
    'restaking', 'restake',
    'l1 security', 'consensus attack', 'bridge hack',
}

ASSET_KEYWORDS = {
    'STRK': ['starknet', 'strk', 'shinobi', 'strkbtc', 'cairo', 'starkware'],
    'LINK': ['chainlink', 'link token', 'ccip'],
    'ETHFI': ['ether.fi', 'ethfi', 'etherfi'],
    'MORPHO': ['morpho'],
    'ARB': ['arbitrum', 'arb token'],
    'OP': ['optimism', 'op token'],
    'MNT': ['mantle network', 'mnt token'],
    'ONDO': ['ondo finance', 'ondo'],
    'CFG': ['centrifuge', 'cfg token'],
    'LDO': ['lido dao', 'ldo'],
    'EIGEN': ['eigenlayer', 'eigen token'],
    'AAVE': ['aave protocol', 'aave '],
    'PENDLE': ['pendle finance', 'pendle'],
    'CRV': ['curve finance', 'curve dao'],
    'UNI': ['uniswap', 'uni token'],
    'TAO': ['bittensor', 'tao token'],
    'RNDR': ['render network', 'rndr'],
    'FET': ['fetch.ai', 'fet token'],
    'AIXBT': ['aixbt'],
    'DOGE': ['dogecoin', 'doge token'],
    'PEPE': ['pepe coin', 'pepecoin'],
    'BONK': ['bonk token', 'bonk inu'],
    'WIF': ['dogwifhat', 'wif token'],
    'BTC': ['bitcoin', 'btc price'],
    'ETH': ['ethereum', 'eth price'],
}

TYPE_KEYWORDS = {
    'security': {'weight': 1.0, 'keywords': ['hack', 'hacked', 'exploit', 'exploited', 'drain', 'stolen', 'vulnerability', 'security incident', 'bridge attack', 'compromised']},
    'unlock': {'weight': 0.9, 'keywords': ['unlock', 'unlocks', 'unlocked', 'cliff', 'vesting', 'token release', 'circulation', 'supply increase']},
    'regulation': {'weight': 0.8, 'keywords': ['sec ', 'lawsuit', 'ban ', 'banned', 'enforcement', 'etf approv', 'etf reject', 'etf decision', 'regulatory', 'settlement', 'court ruling', 'compliance']},
    'upgrade': {'weight': 0.7, 'keywords': ['mainnet', 'protocol upgrade', 'hard fork', 'v0.', 'v1.', 'new version', 'network upgrade', 'shinobi', 'improvement proposal']},
    'partnership': {'weight': 0.4, 'keywords': ['partnership', 'integration', 'integrated', 'grant', 'grants', 'listing', 'listed on', 'collaboration', 'joins']},
    'funding': {'weight': 0.3, 'keywords': ['raise', 'raised', 'funding round', 'series a', 'series b', 'treasury', 'foundation grant', 'investment']},
    'narrative': {'weight': 0.2, 'keywords': ['sector hype', 'trending', 'season', 'narrative shift', 'ai agents', 'meme season', 'rwa summer']},
    'noise': {'weight': 0.0, 'keywords': ['to the moon', 'price prediction', 'moon', 'lambo', 'to $100k', 'guru says', 'trader predicts']},
}

SOURCE_WEIGHTS = {
    'official': 1.0, 'tier1_news': 0.85, 'surf_cited': 0.8,
    'aggregator': 0.5, 'social': 0.3, 'unknown': 0.2,
}

SOURCE_CLASS_MAP = {
    'starknet.io': 'official', 'ethereum.org': 'official', 'bitcoin.org': 'official',
    'theblock.co': 'tier1_news', 'coindesk.com': 'tier1_news',
    'bloomberg.com': 'tier1_news', 'reuters.com': 'tier1_news',
    'cointelegraph.com': 'tier1_news', 'decrypt.co': 'tier1_news',
    'blockworks.co': 'tier1_news', 'dlnews.com': 'tier1_news',
    'thedefiant.io': 'tier1_news', 'techflow.com': 'tier1_news',
    'foresightnews.pro': 'aggregator', 'chaincatcher.com': 'aggregator',
    'cryptopanic.com': 'aggregator',
    'twitter.com': 'social', 'x.com': 'social',
}

SEVERITY_MULT = {'high': 1.0, 'medium': 0.7, 'low': 0.4}

PHASE_MODIFIERS = {
    'ACCUMULATION': {'security': 1.2, 'unlock': 1.2, 'upgrade': 0.7, 'partnership': 0.6, 'regulation': 1.0, 'funding': 0.5, 'narrative': 0.3, 'noise': 0.0},
    'WAIT': {'security': 1.2, 'unlock': 1.2, 'upgrade': 0.7, 'partnership': 0.6, 'regulation': 1.0, 'funding': 0.5, 'narrative': 0.3, 'noise': 0.0},
    'MARKUP': {'security': 1.1, 'unlock': 1.1, 'upgrade': 0.9, 'partnership': 0.7, 'regulation': 0.9, 'funding': 0.5, 'narrative': 0.5, 'noise': 0.0},
    'SPRING': {'security': 1.1, 'unlock': 1.1, 'upgrade': 1.0, 'partnership': 0.8, 'regulation': 0.9, 'funding': 0.5, 'narrative': 0.4, 'noise': 0.0},
    'DISTRIBUTION': {'security': 1.2, 'unlock': 1.3, 'upgrade': 0.5, 'partnership': 0.4, 'regulation': 1.1, 'funding': 0.3, 'narrative': 0.2, 'noise': 0.0},
    'MARKDOWN': {'security': 1.3, 'unlock': 1.3, 'upgrade': 0.4, 'partnership': 0.3, 'regulation': 1.2, 'funding': 0.3, 'narrative': 0.2, 'noise': 0.0},
}

ROUTE_THRESHOLDS = {'home': 0.75, 'macro': 0.45}

RSS_FEEDS = [
    ('TheBlock', 'https://www.theblock.co/rss.xml'),
    ('CoinDesk', 'https://www.coindesk.com/arc/outboundfeeds/rss/'),
    ('CoinTelegraph', 'https://cointelegraph.com/rss'),
    ('Decrypt', 'https://decrypt.co/feed'),
]

# ============================================================
# LAB CONTEXT
# ============================================================
def get_current_phase():
    lab_path = CACHE_DIR / 'strk_lab_report.json'
    if not lab_path.exists():
        return 'ACCUMULATION'
    try:
        with open(lab_path) as f:
            lab = json.load(f)
        phase = (lab.get('strk_status', {}).get('wyckoff_phase') or 'ACCUMULATION').upper().strip()
        if phase in PHASE_MODIFIERS:
            return phase
        if 'WAIT' in phase or 'STILL' in phase:
            return 'ACCUMULATION'
        return 'ACCUMULATION'
    except:
        return 'ACCUMULATION'

def get_hold_tokens():
    lab_path = CACHE_DIR / 'strk_lab_report.json'
    if not lab_path.exists():
        return []
    try:
        with open(lab_path) as f:
            lab = json.load(f)
        return [x['token'] for x in lab.get('strong_buy', []) if x.get('token') != 'STRK']
    except:
        return []

# ============================================================
# SURF CLI FETCH (PRIMARY)
# ============================================================
def check_surf_available():
    """Check if surf CLI is installed and configured."""
    try:
        result = subprocess.run(['surf', '--version'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print(f'  ✓ surf CLI: {result.stdout.strip()}')
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False

def fetch_surf_news(assets_list, days=2):
    """
    Fetch news from Surf CLI.
    Returns list of raw articles in unified format.
    """
    if not check_surf_available():
        print('  ✗ Surf CLI not available — will fallback to RSS')
        return None
    
    articles = []
    
    # Try multiple approaches — surf syntax may vary
    approaches = [
        # Approach 1: Direct news query
        ['surf', 'news', '--assets', ','.join(assets_list), '--days', str(days), '--format', 'json'],
        # Approach 2: news-search
        ['surf', 'news-search', '--symbols', ','.join(assets_list), '--limit', '30'],
        # Approach 3: search-news
        ['surf', 'search-news', '--query', 'crypto', '--symbols', ','.join(assets_list)],
    ]
    
    for cmd in approaches:
        try:
            print(f'  ⏳ Trying: {" ".join(cmd[:4])}...')
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                print(f'    ⚠ Exit {result.returncode}: {result.stderr[:200]}')
                continue
            
            # Parse output
            try:
                data = json.loads(result.stdout)
                # Different Surf endpoints may return different structures
                if isinstance(data, dict):
                    raw_articles = (data.get('news') or data.get('articles') or 
                                   data.get('results') or data.get('data') or [])
                elif isinstance(data, list):
                    raw_articles = data
                else:
                    raw_articles = []
                
                if not raw_articles:
                    print(f'    ⚠ Empty results')
                    continue
                
                print(f'    ✓ Got {len(raw_articles)} articles from Surf')
                
                # Normalize
                for item in raw_articles:
                    if not isinstance(item, dict):
                        continue
                    title = item.get('title') or item.get('headline') or ''
                    if not title:
                        continue
                    
                    articles.append({
                        'source': item.get('source') or item.get('publisher') or 'Surf',
                        'title': title,
                        'description': item.get('description') or item.get('summary') or item.get('excerpt', ''),
                        'link': item.get('url') or item.get('link', ''),
                        'published': item.get('published_at') or item.get('date') or item.get('created_at'),
                        # Surf may pre-classify these:
                        'surf_sentiment': item.get('sentiment'),  # bullish/bearish/neutral
                        'surf_category': item.get('category') or item.get('topic'),
                        'surf_relevance': item.get('relevance_score'),
                        'source_type': 'surf',
                    })
                
                if articles:
                    return articles  # Success
            
            except json.JSONDecodeError:
                print(f'    ⚠ Not JSON output')
                continue
        
        except subprocess.TimeoutExpired:
            print(f'    ⚠ Timeout')
            continue
        except Exception as e:
            print(f'    ⚠ {e}')
            continue
    
    print('  ✗ All Surf approaches failed — fallback to RSS')
    return None

# ============================================================
# RSS FETCH (FALLBACK)
# ============================================================
def fetch_rss(source_name, url, max_articles=15, hours_back=48):
    if not feedparser:
        return []
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        articles = []
        for entry in feed.entries[:max_articles]:
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
            desc = re.sub(r'<[^>]+>', '', entry.get('summary', '') or entry.get('description', '')).strip()[:500]
            articles.append({
                'source': source_name,
                'title': title,
                'description': desc,
                'link': entry.get('link', ''),
                'published': pub_ts.isoformat() if pub_ts else None,
                'source_type': 'rss',
            })
        return articles
    except Exception as e:
        print(f'  ⚠ {source_name}: {e}')
        return []

def fetch_all_rss():
    """Fetch from all RSS feeds."""
    print('\n=== RSS Fallback ===')
    all_articles = []
    for source_name, url in RSS_FEEDS:
        articles = fetch_rss(source_name, url)
        print(f'  {source_name}: {len(articles)} articles')
        all_articles.extend(articles)
    return all_articles

# ============================================================
# CLASSIFICATION (methodology layer)
# ============================================================
def classify_type(text):
    text_lower = text.lower()
    scores = {}
    matched = {}
    for type_name, cfg in TYPE_KEYWORDS.items():
        kws = [kw for kw in cfg['keywords'] if kw in text_lower]
        if kws:
            scores[type_name] = len(kws) * cfg['weight']
            matched[type_name] = kws
    if not scores:
        return ('narrative', 0.2, [])
    best = max(scores.items(), key=lambda x: x[1])
    return (best[0], TYPE_KEYWORDS[best[0]]['weight'], matched.get(best[0], []))

def detect_severity(text, event_type):
    text_lower = text.lower()
    high_markers = [
        r'\$\d+m', r'\$\d+ million', r'\$\d+b', r'\$\d+ billion',
        'unprecedented', 'largest ever', 'record breaking',
        'critical', 'emergency', 'urgent',
        'halt', 'paused', 'shutdown', 'exploit',
    ]
    if event_type == 'security':
        if any(re.search(pat, text_lower) for pat in high_markers):
            return 'high'
        return 'medium'
    if event_type == 'unlock':
        matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:m|million|b|billion)', text_lower)
        if matches:
            values = [float(m) for m in matches]
            if any(v > 100 for v in values):
                return 'high'
            return 'medium'
        return 'medium'
    if event_type == 'regulation':
        if 'approv' in text_lower or 'reject' in text_lower or 'lawsuit' in text_lower:
            return 'high'
        return 'medium'
    if any(re.search(pat, text_lower) for pat in high_markers):
        return 'high'
    if event_type in ('upgrade', 'partnership'):
        return 'medium'
    return 'low'

def classify_source(url, source_name=''):
    if not url and not source_name:
        return 'unknown'
    combined = (url + ' ' + source_name).lower()
    for domain, cls in SOURCE_CLASS_MAP.items():
        if domain in combined:
            return cls
    lname = source_name.lower()
    if 'blog' in lname or 'official' in lname:
        return 'official'
    if lname in ('theblock', 'coindesk', 'cointelegraph', 'decrypt', 'bloomberg', 'reuters'):
        return 'tier1_news'
    return 'unknown'

def detect_assets(text):
    text_lower = text.lower()
    found = set()
    for asset, keywords in ASSET_KEYWORDS.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
                found.add(asset)
                break
    return sorted(found)

def is_relevant(text, assets):
    if any(a in WATCHLIST_ASSETS for a in assets):
        return True
    text_lower = text.lower()
    for theme in WATCHLIST_THEMES:
        if theme in text_lower:
            return True
    return False

def detect_fomo_risk(article, all_articles_last_24h):
    text = (article.get('title', '') + ' ' + article.get('description', '')).lower()
    source_class = article.get('source_class', 'unknown')
    
    # Rule 1: "listing soon" без даты
    listing_markers = ['listing soon', 'coming soon', 'about to list', 'binance announcement', 'coinbase announcement']
    if any(m in text for m in listing_markers):
        has_date = bool(re.search(r'(20\d\d|\d{1,2}[/\-]\d{1,2}|monday|tuesday|wednesday|thursday|friday|next week)', text))
        if not has_date:
            return True
    
    # Rule 2: Single source without official/tier1 backup
    if source_class in ('social', 'unknown', 'aggregator'):
        title_lower = article.get('title', '').lower()
        title_words = set(re.findall(r'\w{5,}', title_lower))
        if len(title_words) >= 3:
            tier1_matches = 0
            for other in all_articles_last_24h:
                if other == article:
                    continue
                if other.get('source_class') in ('official', 'tier1_news'):
                    other_words = set(re.findall(r'\w{5,}', other.get('title', '').lower()))
                    overlap = len(title_words & other_words) / max(len(title_words), 1)
                    if overlap > 0.4:
                        tier1_matches += 1
            if tier1_matches == 0:
                return True
    
    # Rule 3: Repetitive claim
    title_lower = article.get('title', '').lower()
    title_words = set(re.findall(r'\w{5,}', title_lower))
    similar_count = 0
    for other in all_articles_last_24h:
        if other == article:
            continue
        other_words = set(re.findall(r'\w{5,}', other.get('title', '').lower()))
        overlap = len(title_words & other_words) / max(len(title_words), 1)
        if overlap > 0.6:
            similar_count += 1
    if similar_count > 3:
        return True
    return False

# ============================================================
# SCORING
# ============================================================
def compute_score(base_weight, source_class, severity, event_type, phase, fomo_risk, surf_boost=1.0):
    """
    score = base × source × severity × phase × fomo × surf_boost
    surf_boost: если Surf уже дал sentiment/relevance — небольшой бонус
    """
    source_weight = SOURCE_WEIGHTS.get(source_class, 0.2)
    severity_mult = SEVERITY_MULT.get(severity, 0.4)
    phase_modifier = PHASE_MODIFIERS.get(phase, PHASE_MODIFIERS['ACCUMULATION']).get(event_type, 0.5)
    fomo_penalty = 0.5 if fomo_risk else 1.0
    return base_weight * source_weight * severity_mult * phase_modifier * fomo_penalty * surf_boost

def route_by_score(score):
    if score >= ROUTE_THRESHOLDS['home']:
        return 'home'
    if score >= ROUTE_THRESHOLDS['macro']:
        return 'macro'
    return 'archive'

def generate_action_hint(event_type, severity, phase, assets):
    if event_type in ('security', 'unlock') and phase in ('ACCUMULATION', 'WAIT'):
        if 'STRK' in assets:
            return {'field': 'MONITOR', 'action': 'add_watch_item',
                    'layman_ru': f'⚠ {event_type.upper()} для STRK в accumulation — добавить в MONITOR, не входить'}
        return {'field': 'SIZE', 'action': 'down',
                'layman_ru': f'⚠ {event_type.upper()} для HOLD → уменьшить SIZE на 25-50%'}
    
    if event_type == 'security' and severity == 'high':
        return {'field': 'INVALIDATION', 'action': 'tighten',
                'layman_ru': '🔴 Serious security event — ужесточить INVALIDATION, готовить exit'}
    
    if event_type == 'upgrade' and phase == 'MARKUP':
        return {'field': 'thesis_alive', 'action': 'confirm',
                'layman_ru': '✅ Upgrade поддерживает thesis — держать, не увеличивать size'}
    
    if event_type == 'unlock':
        return {'field': 'SIZE', 'action': 'down_or_watch',
                'layman_ru': '📉 Unlock event → уменьшить SIZE или в MONITOR'}
    
    if event_type in ('partnership', 'upgrade') and phase in ('ACCUMULATION', 'WAIT'):
        return {'field': 'thesis_alive', 'action': 'noted',
                'layman_ru': f'{event_type.title()} — поддерживает thesis, но НЕ NEW_ENTRY'}
    
    return {'field': 'MONITOR', 'action': 'noted',
            'layman_ru': f'{event_type.title()} · {severity} · {phase} — noted, watch mode'}

# ============================================================
# NORMALIZATION
# ============================================================
def normalize_to_event(article, phase, all_articles):
    text = article.get('title', '') + ' ' + article.get('description', '')
    
    assets = detect_assets(text)
    if not is_relevant(text, assets):
        return None
    
    event_type, base_weight, matched_kws = classify_type(text)
    if event_type == 'noise':
        return None
    
    severity = detect_severity(text, event_type)
    source_class = classify_source(article.get('link', ''), article.get('source', ''))
    article['source_class'] = source_class
    
    fomo_risk = detect_fomo_risk(article, all_articles)
    
    # Surf boost: если это данные из Surf с pre-classification
    surf_boost = 1.0
    if article.get('source_type') == 'surf':
        source_class = 'surf_cited'
        surf_boost = 1.1  # 10% boost за Surf pre-classification
        # Если Surf дал sentiment — используем его как sanity check
        # (не переопределяем classification, но повышаем confidence)
    
    score = compute_score(base_weight, source_class, severity, event_type, phase, fomo_risk, surf_boost)
    route = route_by_score(score)
    action = generate_action_hint(event_type, severity, phase, assets)
    
    id_hash = hashlib.md5((article.get('title', '') + article.get('link', '')).encode()).hexdigest()[:8]
    source_type = article.get('source_type', 'rss')
    
    return {
        'id': f"{source_type}-{id_hash}",
        'assets': assets,
        'type': event_type,
        'severity': severity,
        'summary': article.get('title', '')[:200],
        'source_class': source_class,
        'source_name': article.get('source', ''),
        'source_type': source_type,
        'url': article.get('link', ''),
        'published': article.get('published'),
        'fomo_risk': fomo_risk,
        'score': round(score, 3),
        'route': route,
        'action_hint': action,
        'matched_keywords': matched_kws[:3],
        'surf_sentiment': article.get('surf_sentiment'),
    }

def dedupe_events(events):
    seen = []
    result = []
    for event in events:
        title_words = set(re.findall(r'\w{5,}', event['summary'].lower()))
        is_dupe = False
        for seen_words in seen:
            if len(title_words) >= 3:
                overlap = len(title_words & seen_words) / max(len(title_words), 1)
                if overlap > 0.6:
                    is_dupe = True
                    break
        if not is_dupe:
            seen.append(title_words)
            result.append(event)
    return result

# ============================================================
# MAIN
# ============================================================
def main():
    print('=== News & Events Classifier v3 · Surf + RSS Hybrid ===\n')
    
    phase = get_current_phase()
    hold_tokens = get_hold_tokens()
    print(f'Current STRK phase: {phase}')
    print(f'HOLD tokens: {hold_tokens}\n')
    
    for tok in hold_tokens:
        WATCHLIST_ASSETS.add(tok)
    
    # ============ SURF PRIMARY ============
    print('=== Surf (Primary) ===')
    surf_assets = sorted(WATCHLIST_ASSETS)[:20]  # Limit query size
    surf_articles = fetch_surf_news(surf_assets, days=2)
    
    surf_success = surf_articles is not None and len(surf_articles) > 0
    if surf_success:
        print(f'✓ Surf returned {len(surf_articles)} articles')
    else:
        surf_articles = []
        print('✗ Surf unavailable or empty — using RSS fallback')
    
    # ============ RSS FALLBACK (always fetch as backup) ============
    rss_articles = fetch_all_rss() if not feedparser is None else []
    print(f'✓ RSS returned {len(rss_articles)} articles')
    
    # ============ MERGE ============
    all_articles = surf_articles + rss_articles
    print(f'\nCombined: {len(all_articles)} raw articles')
    
    # ============ CLASSIFY ============
    print('\n=== Classification (methodology layer) ===')
    events = []
    for article in all_articles:
        event = normalize_to_event(article, phase, all_articles)
        if event:
            events.append(event)
    
    print(f'Relevant events: {len(events)}')
    
    # ============ DEDUPE ============
    events = dedupe_events(events)
    events.sort(key=lambda e: -e['score'])
    print(f'After dedupe: {len(events)}')
    
    # ============ ROUTE ============
    home_events = [e for e in events if e['route'] == 'home'][:3]
    macro_events = [e for e in events if e['route'] == 'macro'][:10]
    archive_events = [e for e in events if e['route'] == 'archive']
    
    print(f'\n=== Routing ===')
    print(f'  HOME: {len(home_events)}/3')
    print(f'  MACRO: {len(macro_events)}')
    print(f'  Archive: {len(archive_events)}')
    
    # ============ OUTPUT ============
    output = {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'phase_used': phase,
        'hold_tokens': hold_tokens,
        'sources': {
            'surf_used': surf_success,
            'surf_articles': len(surf_articles),
            'rss_articles': len(rss_articles),
        },
        'events': home_events + macro_events + archive_events[:20],
        'route_counts': {
            'home': len(home_events),
            'macro': len(macro_events),
            'archive': len(archive_events),
        },
        'home_events': home_events,
        'macro_events': macro_events,
        'methodology_version': '3.0',
    }
    
    output_path = CACHE_DIR / 'surf_events.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f'\n✓ Written: {output_path}')
    
    if home_events:
        print(f'\n=== HOME EVENTS ({len(home_events)}) ===')
        for e in home_events:
            surf_tag = ' [SURF]' if e.get('source_type') == 'surf' else ' [RSS]'
            print(f'\n  🔴{surf_tag} {e["assets"]} · {e["type"]}/{e["severity"]} · score {e["score"]}')
            print(f'    {e["summary"][:120]}')
            print(f'    → {e["action_hint"]["layman_ru"]}')

if __name__ == '__main__':
    main()
