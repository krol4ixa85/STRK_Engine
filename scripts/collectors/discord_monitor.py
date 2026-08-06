#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discord_monitor.py — Discord Alert Channel Reader

Читает сообщения из Discord канала с Nansen alerts (или любых whale bots).
Парсит их, классифицирует и сохраняет для composite detector.

ТРЕБУЕТ:
  · DISCORD_BOT_TOKEN — в config.env (см. README как создать)
  · DISCORD_CHANNEL_ID — канал с алертами (по умолчанию 1502225814714978374)

Discord Bot API:
  GET https://discord.com/api/v10/channels/{channel_id}/messages?limit=100&after={id}
  Header: Authorization: Bot {token}

Nansen alert format examples:
  "🐋 Whale Alert: 15.0M STRK transferred..."
  "Binance 14 → BingX Deposit"
  "Custody Outflow · possible unlock"

Anti-spam:
  - State хранит последний прочитанный message ID
  - Не отправляет одно и то же сообщение в Telegram дважды
"""

import os
import sys
import json
import time
import logging
import argparse
import urllib.request
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = CACHE_DIR / 'discord_monitor_state.json'

DISCORD_API = 'https://discord.com/api/v10'
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '')
DISCORD_CHANNEL_ID = os.environ.get('DISCORD_CHANNEL_ID', '1502225814714978374')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('discord')


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'last_message_id': None, 'seen_message_ids': [], 'parsed_events': []}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, default=str)


def fetch_discord_messages(channel_id, after_id=None, limit=50):
    """Fetch recent messages from Discord channel."""
    url = f"{DISCORD_API}/channels/{channel_id}/messages?limit={limit}"
    if after_id:
        url += f"&after={after_id}"
    
    req = urllib.request.Request(url, headers={
        'Authorization': f'Bot {DISCORD_BOT_TOKEN}',
        'User-Agent': 'STRK-Engine-Bot/1.0',
    })
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.error(f"Discord API error {e.code}: {e.reason}")
        if e.code == 401:
            logger.error("  BOT TOKEN invalid or bot not in server")
        elif e.code == 403:
            logger.error("  Bot lacks READ_MESSAGES permission")
        elif e.code == 404:
            logger.error(f"  Channel {channel_id} not found or bot not member")
        return None
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return None


def parse_nansen_alert(msg_content):
    """
    Parse Nansen-style whale alert message.
    Returns dict with amount, class, addresses if parseable, else None.
    """
    if not msg_content:
        return None
    
    parsed = {'raw': msg_content[:500]}
    
    # Amount pattern: "15.0M STRK", "1,500,000 STRK", "1.5B STRK"
    amount_patterns = [
        (r'(\d+(?:\.\d+)?)\s*[MК]\s*STRK', 1_000_000),
        (r'(\d+(?:\.\d+)?)\s*[BБ]\s*STRK', 1_000_000_000),
        (r'([\d,]+(?:\.\d+)?)\s*STRK', 1),
    ]
    for pattern, mult in amount_patterns:
        m = re.search(pattern, msg_content)
        if m:
            try:
                amt_str = m.group(1).replace(',', '')
                parsed['amount_strk'] = float(amt_str) * mult
                break
            except ValueError:
                continue
    
    # Ethereum addresses
    addrs = re.findall(r'0x[a-fA-F0-9]{40}', msg_content)
    if addrs:
        parsed['addresses'] = list(set(a.lower() for a in addrs))
    
    # Route pattern: "X → Y"
    route_match = re.search(r'([^→\n]+)→([^→\n]+)', msg_content)
    if route_match:
        parsed['route'] = f"{route_match.group(1).strip()} → {route_match.group(2).strip()}"
    
    # Alert type keywords
    content_lower = msg_content.lower()
    if any(w in content_lower for w in ['distribution', 'sell', 'dump']):
        parsed['direction_hint'] = 'bearish'
    elif any(w in content_lower for w in ['accumulation', 'buy', 'inflow']):
        parsed['direction_hint'] = 'bullish'
    elif any(w in content_lower for w in ['bridge', 'stake', 'unlock']):
        parsed['direction_hint'] = 'neutral_infra'
    else:
        parsed['direction_hint'] = 'unknown'
    
    # Whale alert vs other
    is_whale_alert = 'amount_strk' in parsed and parsed.get('amount_strk', 0) >= 1_000_000
    parsed['is_whale_alert'] = is_whale_alert
    
    # Filter out Nansen META messages (bookkeeping, not actual movements)
    META_PATTERNS = [
        'is added to this chat',
        'is added to',
        'was added to',
        'removed from',
        'is tracked',
        'now tracked',
        'stopped tracking',
        'is now monitored',
        'is being tracked',
        'holding ',  # "holding 12.96M STRK" - just balance info, not movement
    ]
    msg_lower = msg_content.lower()
    is_meta = any(p in msg_lower for p in META_PATTERNS) and not is_whale_alert
    
    if is_meta:
        parsed['is_meta_message'] = True
        return None  # Don't forward meta messages
    
    return parsed if is_whale_alert or 'STRK' in msg_content.upper() else None


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:\n" + text[:500])
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(r, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def format_forwarded_alert(msg, parsed):
    """Format Discord alert for forwarding to Telegram."""
    author = msg.get('author', {}).get('username', 'Unknown')
    timestamp = msg.get('timestamp', '')
    
    text = f"📡 <b>Discord Alert</b> (from {author})\n\n"
    
    if parsed.get('amount_strk'):
        amt = parsed['amount_strk']
        if amt >= 1_000_000:
            text += f"<b>Amount:</b> {amt/1e6:.2f}M STRK\n"
        else:
            text += f"<b>Amount:</b> {amt:,.0f} STRK\n"
    
    if parsed.get('route'):
        text += f"<b>Route:</b> {parsed['route']}\n"
    
    if parsed.get('direction_hint'):
        text += f"<b>Hint:</b> {parsed['direction_hint']}\n"
    
    text += f"\n<i>Original message:</i>\n<code>{parsed['raw'][:200]}</code>"
    
    return text


def check_and_process(post_to_telegram=True):
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set. Cannot read Discord.")
        logger.error("See docs/DISCORD_SETUP.md for how to get a token.")
        return 0
    
    state = load_state()
    after_id = state.get('last_message_id')
    seen = set(state.get('seen_message_ids', []))
    
    logger.info(f"Checking Discord channel {DISCORD_CHANNEL_ID}...")
    if after_id:
        logger.info(f"  After message ID: {after_id}")
    
    messages = fetch_discord_messages(DISCORD_CHANNEL_ID, after_id=after_id)
    if messages is None:
        return 0
    
    logger.info(f"Fetched {len(messages)} messages")
    
    if not messages:
        state['last_check'] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return 0
    
    # Messages come newest first, sort oldest first for processing
    messages.sort(key=lambda m: int(m.get('id', 0)))
    
    new_events = 0
    for msg in messages:
        msg_id = msg.get('id')
        if msg_id in seen:
            continue
        seen.add(msg_id)
        
        # Get content — check both content field and embeds
        content = msg.get('content', '')
        for emb in msg.get('embeds', []):
            if emb.get('description'):
                content += '\n' + emb['description']
            for f in emb.get('fields', []):
                content += f"\n{f.get('name', '')}: {f.get('value', '')}"
        
        parsed = parse_nansen_alert(content)
        if not parsed:
            continue
        
        if not parsed.get('is_whale_alert'):
            continue
        
        logger.info(f"  Whale alert parsed: {parsed.get('amount_strk', 0)/1e6:.1f}M STRK · {parsed.get('direction_hint')}")
        
        # Record
        state['parsed_events'].append({
            'msg_id': msg_id,
            'timestamp': msg.get('timestamp'),
            'author': msg.get('author', {}).get('username'),
            'parsed': parsed,
        })
        
        # Forward to Telegram
        if post_to_telegram and parsed.get('amount_strk', 0) >= 5_000_000:
            send_telegram(format_forwarded_alert(msg, parsed))
        
        new_events += 1
    
    # Update state
    if messages:
        state['last_message_id'] = max(m['id'] for m in messages)
    state['seen_message_ids'] = list(seen)[-500:]
    state['parsed_events'] = state['parsed_events'][-200:]
    state['last_check'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    
    logger.info(f"Processed {new_events} new whale events")
    return new_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Single check')
    parser.add_argument('--no-telegram', action='store_true', help='Don\'t forward to Telegram')
    parser.add_argument('--test', action='store_true', help='Test connection only')
    args = parser.parse_args()
    
    if args.test:
        if not DISCORD_BOT_TOKEN:
            logger.error("DISCORD_BOT_TOKEN not set")
            return 1
        logger.info(f"Testing Discord connection to channel {DISCORD_CHANNEL_ID}...")
        msgs = fetch_discord_messages(DISCORD_CHANNEL_ID, limit=1)
        if msgs is None:
            logger.error("Connection failed. See error above.")
            return 1
        logger.info(f"✓ Connected. Last message: {msgs[0].get('id') if msgs else 'no messages'}")
        return 0
    
    check_and_process(post_to_telegram=not args.no_telegram)
    return 0


if __name__ == '__main__':
    sys.exit(main())
