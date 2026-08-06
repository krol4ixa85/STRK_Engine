#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
starknet_discord.py — Starknet Discord announcements monitor

Читает official Starknet Discord server #announcements channel.
Классифицирует post'ы:
  · MAJOR_UPDATE: mainnet upgrade, protocol change
  · PARTNERSHIP: integration, collaboration
  · INCENTIVES: quests, airdrops, rewards
  · INFRA: infrastructure, tools, SDK
  · COMMUNITY: community events, AMA
  · NEUTRAL: general updates

ТРЕБУЕТ:
  · DISCORD_BOT_TOKEN — уже есть у тебя
  · STARKNET_DISCORD_CHANNEL_ID — ID канала #announcements
  
Как найти channel ID:
  1. Discord Settings → Advanced → Developer Mode ON
  2. ПКМ на канале → Copy Channel ID
  3. Добавь в secrets как STARKNET_ANNOUNCEMENTS_CHANNEL_ID

Пороги для сигналов:
  · 2+ MAJOR/PARTNERSHIP announcements за 7 дней → BULLISH_NEWS_FLOW
  · 0 announcements за 14 дней → CONCERNING_SILENCE
  · 1+ MAJOR_UPDATE → CATALYST_DETECTED
"""

import os
import sys
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'starknet_discord.json'
STATE_FILE = CACHE_DIR / 'starknet_discord_state.json'

DISCORD_API = 'https://discord.com/api/v10'
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '')
# Starknet #announcements channel ID (needs to be set by user)
CHANNEL_ID = os.environ.get('STARKNET_ANNOUNCEMENTS_CHANNEL_ID', '')

# Category keywords
CATEGORY_KEYWORDS = {
    'MAJOR_UPDATE': [
        'mainnet', 'upgrade', 'protocol change', 'new version', 'release',
        'v0.', 'v1.', 'v2.', 'v3.', 'update', 'launched',
        'hard fork', 'rollup', 'sequencer', 'prover',
    ],
    'PARTNERSHIP': [
        'partnership', 'partnered', 'collaborated', 'joining', 'integration',
        'welcome', 'onboarded', 'welcomes', 'together with',
        'signed', 'agreement',
    ],
    'INCENTIVES': [
        'quest', 'reward', 'incentive', 'season', 'airdrop', 'staking',
        'provisions', 'grant', 'earn', 'liquidity mining', 'points',
        'campaign', 'competition',
    ],
    'INFRA': [
        'sdk', 'documentation', 'developer', 'tooling', 'framework',
        'cairo', 'compiler', 'library', 'api', 'infrastructure',
        'testnet', 'devnet',
    ],
    'COMMUNITY': [
        'ama', 'community', 'meetup', 'summit', 'event', 'discord',
        'twitter space', 'workshop', 'hackathon', 'twitter',
    ],
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('sn_discord')


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'last_message_id': None, 'processed_ids': []}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def fetch_messages(channel_id, limit=50):
    """Fetch recent messages from channel."""
    url = f"{DISCORD_API}/channels/{channel_id}/messages?limit={limit}"
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
        'User-Agent': 'STRK-Engine-Bot/1.0',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.error(f"Discord API error {e.code}: {e.reason}")
        if e.code == 403:
            logger.error("  → Bot needs access to the channel!")
            logger.error("  → Invite bot to Starknet server with 'Read Messages' + 'View Channel' perms")
        elif e.code == 401:
            logger.error("  → Bot token invalid")
        return []
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return []


def classify_message(text):
    """Classify message by content."""
    text_lower = text.lower()
    scores = {cat: 0 for cat in CATEGORY_KEYWORDS.keys()}
    
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[cat] += 1
    
    # Find best category
    max_score = max(scores.values())
    if max_score == 0:
        return 'NEUTRAL', scores
    
    best_cat = max(scores.items(), key=lambda x: x[1])[0]
    return best_cat, scores


def analyze_announcements():
    """Fetch and analyze recent announcements."""
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set")
        return {'signal': 'NO_TOKEN', 'error': 'DISCORD_BOT_TOKEN not configured'}
    
    if not CHANNEL_ID:
        logger.warning("STARKNET_ANNOUNCEMENTS_CHANNEL_ID not set")
        return {
            'signal': 'NO_CHANNEL',
            'error': 'STARKNET_ANNOUNCEMENTS_CHANNEL_ID not configured',
            'setup_instructions': [
                '1. Enable Developer Mode in Discord (User Settings > Advanced)',
                '2. Right-click on Starknet #announcements channel',
                '3. Click "Copy Channel ID"',
                '4. Add as GitHub secret: STARKNET_ANNOUNCEMENTS_CHANNEL_ID',
                '5. Invite your bot to Starknet Discord with "Read Messages" permission',
            ]
        }
    
    logger.info(f"Fetching messages from channel {CHANNEL_ID}...")
    messages = fetch_messages(CHANNEL_ID, limit=50)
    logger.info(f"  Got {len(messages)} messages")
    
    if not messages:
        return {'signal': 'NO_DATA', 'error': 'No messages retrieved'}
    
    # Analyze recent (last 7 days)
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)
    cutoff_14d = now - timedelta(days=14)
    
    recent_7d = []
    recent_14d = []
    
    for msg in messages:
        try:
            msg_time = datetime.fromisoformat(msg['timestamp'].replace('Z', '+00:00'))
            content = msg.get('content', '')
            
            # Also check embeds (some announcements are embeds)
            for embed in msg.get('embeds', []):
                content += ' ' + (embed.get('title') or '')
                content += ' ' + (embed.get('description') or '')
            
            if not content.strip():
                continue
            
            category, scores = classify_message(content)
            
            entry = {
                'id': msg['id'],
                'timestamp': msg['timestamp'],
                'content_preview': content[:200].replace('\n', ' '),
                'category': category,
                'scores': scores,
                'author': msg.get('author', {}).get('username', 'unknown'),
            }
            
            if msg_time > cutoff_7d:
                recent_7d.append(entry)
            if msg_time > cutoff_14d:
                recent_14d.append(entry)
        except Exception as e:
            logger.debug(f"Error parsing message: {e}")
            continue
    
    # Category counts (7d)
    cat_counts = {}
    for entry in recent_7d:
        cat = entry['category']
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    
    # === Classification ===
    major = cat_counts.get('MAJOR_UPDATE', 0)
    partnership = cat_counts.get('PARTNERSHIP', 0)
    incentives = cat_counts.get('INCENTIVES', 0)
    total_7d = len(recent_7d)
    total_14d = len(recent_14d)
    
    signal = 'NORMAL'
    interpretation = ''
    
    if major >= 1 and (partnership + incentives) >= 1:
        signal = 'CATALYST_DETECTED'
        interpretation = f'{major} major update + {partnership + incentives} catalysts (7d) — bullish news flow'
    elif major >= 1:
        signal = 'MAJOR_UPDATE'
        interpretation = f'{major} major update in 7d'
    elif partnership >= 2:
        signal = 'PARTNERSHIP_FLOW'
        interpretation = f'{partnership} partnerships in 7d'
    elif incentives >= 2:
        signal = 'INCENTIVE_PROGRAMS'
        interpretation = f'{incentives} incentive programs in 7d'
    elif total_14d == 0:
        signal = 'CONCERNING_SILENCE'
        interpretation = 'No announcements in 14 days — concerning silence'
    elif total_7d == 0:
        signal = 'LOW_ACTIVITY'
        interpretation = f'No announcements in 7d ({total_14d} in 14d)'
    else:
        signal = 'NORMAL'
        interpretation = f'{total_7d} announcements in 7d'
    
    return {
        'as_of': now.isoformat(),
        'signal': signal,
        'interpretation': interpretation,
        'total_7d': total_7d,
        'total_14d': total_14d,
        'category_counts_7d': cat_counts,
        'recent_announcements': recent_7d[:5],
    }


def main():
    logger.info("=" * 60)
    logger.info("STARKNET DISCORD ANNOUNCEMENTS MONITOR")
    logger.info("=" * 60)
    
    result = analyze_announcements()
    
    logger.info(f"\nSignal: {result.get('signal', 'ERROR')}")
    if result.get('interpretation'):
        logger.info(f"Interpretation: {result['interpretation']}")
    
    if result.get('setup_instructions'):
        logger.info(f"\n=== SETUP NEEDED ===")
        for step in result['setup_instructions']:
            logger.info(f"  {step}")
    
    if result.get('recent_announcements'):
        logger.info(f"\n=== RECENT ANNOUNCEMENTS ===")
        for a in result['recent_announcements'][:3]:
            logger.info(f"  · [{a['category']}] {a['timestamp'][:10]}")
            logger.info(f"    {a['content_preview'][:120]}...")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
