#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_discovery.py — Автоматическое обнаружение новых потенциальных holder кошельков

Каждые 6 часов сканирует STRK L1 Transfer events за последние 24-48 часов.
Ищет non-CEX адреса, получившие >1M STRK с retention >70% в окне.
Классифицирует по паттерну поведения.
Отправляет в Telegram предложение с командами /accept и /reject.

Логика:
  1. Fetch STRK Transfer events (24-48h back)
  2. Aggregate by recipient
  3. Filter:
     - received >= 1M STRK
     - retention >= 70% (not immediately dumped)
     - not in KNOWN_CEX
     - not already in flow_seeds.json (не дублируем)
     - not already proposed and rejected
  4. Classify pattern:
     - ACCUMULATOR: many sources → few destinations, high retention
     - HOLDER: single source, no outflow
     - PARTIAL_HOLDER: some outflow but retained majority
     - MULTI_SOURCE: 3+ different senders
  5. Rank by score (retention × amount × new_address_bonus)
  6. Send Telegram proposals for top 3 candidates
  7. Save state to avoid re-proposing

Output:
  - Telegram messages with /accept <addr> or /reject <addr>
  - data/cache/auto_discovery_state.json (proposed history)
  - data/cache/auto_discovery_candidates.json (fresh candidates)
"""

import os
import sys
import json
import time
import logging
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
STATE_FILE = CACHE_DIR / 'auto_discovery_state.json'
CANDIDATES_FILE = CACHE_DIR / 'auto_discovery_candidates.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

# Known addresses to ignore (CEX/bridge/team)
KNOWN_IGNORE = {
    '0x28c6c06298d514db089934071355e5743bf21d60', '0x21a31ee1afc51d94c2efccaa2092ad1028285549',
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d', '0x56eddb7aa87536c09ccc2793473599fd21a8b17f',
    '0x9696f59e4d72e237be84ffd425dcad154bf96976', '0x5a52e96bacdabb82fd05763e25335261b270efcb',
    '0xf977814e90da44bfa03b6295a0616a897441acec', '0xa7efae728d2936e78bda97dc267687568dd593f4',
    '0xe93685f3bba03016f02bd1828badd6195988d950', '0xf89d7b9c864f589bbf53a82105107622b35eaa40',
    '0x9642b23ed1e01df1092b92641051881a322f5d4e', '0xce5485cfb26914c5dce00b9baf0580364dafc7a4',
    '0xa86309988947559b6e72ef716c5058f479386c0f', '0xb1c561105359f549f6e9438867b435580ba3a6b0',
    '0xa8a5b3d0c320ac2ed724169b7f554e3740230586', '0x9b6c368d707481eb215f52b6ced3b81b281ca65c',
    '0x0000000000000000000000000000000000000000',  # zero address (burn)
}

# Thresholds
MIN_RECEIVED = 1_000_000       # >= 1M STRK
MIN_RETENTION_PCT = 70          # >= 70% retention in window
DEFAULT_WINDOW_HOURS = 48       # last 48h scan

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('discovery')


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'proposed': {},   # addr -> {ts, name, decision}
        'rejected': [],   # list of rejected addrs to skip
        'accepted': [],   # list of accepted addrs (should be in seeds already)
    }


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def load_existing_addresses():
    """Get all addresses currently in flow_seeds.json (any category)."""
    if not SEEDS_FILE.exists():
        return set()
    try:
        with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
            seeds = json.load(f)
        addrs = set()
        for cat, data in seeds.items():
            if not isinstance(data, dict) or cat.startswith('_'):
                continue
            for name, entry in data.items():
                if isinstance(entry, dict):
                    a = entry.get('address', '').lower()
                    if a:
                        addrs.add(a)
        return addrs
    except Exception as e:
        logger.warning(f"Failed to load seeds: {e}")
        return set()


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
    d = api_call({
        'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
        'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY,
    })
    return int(d['result']) if d and d.get('status') == '1' else None


def fetch_transfers(hours_back=48):
    """Fetch STRK Transfer events in window."""
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(hours=hours_back)).timestamp())
    
    from_block = get_block_at_time(from_ts)
    time.sleep(0.4)
    to_block = get_block_at_time(to_ts)
    time.sleep(0.4)
    if not from_block or not to_block:
        return []
    
    transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    all_txs = []
    current = from_block
    
    for _ in range(15):
        data = api_call({
            'chainid': 1, 'module': 'logs', 'action': 'getLogs',
            'address': STRK_L1, 'topic0': transfer_topic,
            'fromBlock': current, 'toBlock': to_block,
            'page': 1, 'offset': 1000, 'apikey': ETHERSCAN_API_KEY,
        })
        if not data or data.get('status') != '1' or not data.get('result'):
            break
        logs = data['result']
        max_block = 0
        for log in logs:
            try:
                topics = log['topics']
                if len(topics) < 3:
                    continue
                from_addr = ('0x' + topics[1][-40:]).lower()
                to_addr = ('0x' + topics[2][-40:]).lower()
                amount = int(log['data'], 16) / 1e18
                block = int(log['blockNumber'], 16)
                ts = int(log['timeStamp'], 16)
                max_block = max(max_block, block)
                if from_ts <= ts <= to_ts:
                    all_txs.append({'from': from_addr, 'to': to_addr, 'amount': amount, 'ts': ts})
            except (KeyError, ValueError, IndexError):
                continue
        if len(logs) < 1000:
            break
        current = max_block + 1
        time.sleep(0.4)
    
    return all_txs


def get_current_balance(address):
    """Balance today via Etherscan tokenbalance."""
    data = api_call({
        'chainid': 1, 'module': 'account', 'action': 'tokenbalance',
        'contractaddress': STRK_L1, 'address': address, 'tag': 'latest',
        'apikey': ETHERSCAN_API_KEY,
    })
    if data and data.get('status') == '1':
        return int(data['result']) / 1e18
    return None


def classify_pattern(received, sent, sources, dests):
    """Classify wallet pattern based on transfer topology."""
    n_sources = len(sources)
    n_dests = len(dests)
    retention = (1 - sent/received) * 100 if received > 0 else 0
    
    if n_sources >= 3 and n_dests <= 1 and retention >= 90:
        return 'ACCUMULATOR', 'Multiple sources → holds (accumulation pattern)'
    if n_sources == 1 and n_dests == 0 and retention >= 95:
        return 'PURE_HOLDER', 'Single source, no outflow (fresh accumulation)'
    if n_sources >= 2 and retention >= 80:
        return 'MULTI_SOURCE_HOLDER', 'Multi-source, high retention'
    if retention >= 70:
        return 'PARTIAL_HOLDER', f'{retention:.0f}% retention'
    return 'MIXED', f'Retention {retention:.0f}%'


def score_candidate(candidate):
    """Score candidate for ranking. Higher = more interesting."""
    retention = candidate['retention_pct']
    amount = candidate['received_strk']
    n_sources = candidate['n_sources']
    
    # Base score: retention × amount (normalized)
    score = (retention / 100) * min(amount / 1e6, 100)
    
    # Bonus for multi-source (harder to fake)
    if n_sources >= 3:
        score *= 1.5
    elif n_sources >= 5:
        score *= 2.0
    
    # Bonus for pattern
    if candidate['pattern'] in ('ACCUMULATOR', 'PURE_HOLDER'):
        score *= 1.3
    
    return round(score, 2)


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:")
        logger.warning(text[:500])
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(r, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def format_proposal(candidate, rank):
    """Format Telegram message with accept/reject commands."""
    text = f"🔍 <b>Discovery #{rank} · {candidate['pattern']}</b>\n\n"
    text += f"<b>Address:</b>\n<code>{candidate['address']}</code>\n\n"
    text += f"<b>Received:</b> {candidate['received_strk']/1e6:.2f}M STRK\n"
    text += f"<b>Retention:</b> {candidate['retention_pct']:.1f}%\n"
    text += f"<b>Sources:</b> {candidate['n_sources']} unique senders\n"
    text += f"<b>Current balance:</b> {candidate['current_balance']/1e6:.2f}M STRK\n"
    text += f"<b>Score:</b> {candidate['score']}\n\n"
    text += f"<b>Pattern:</b> {candidate['pattern_reason']}\n\n"
    text += f"<b>Add to watchlist?</b>\n"
    text += f"<code>/accept {candidate['address'][:10]}...</code>\n"
    text += f"<code>/reject {candidate['address'][:10]}...</code>\n\n"
    text += f"<a href='https://etherscan.io/address/{candidate['address']}'>Etherscan</a>"
    return text


def run_discovery(hours_back=48, max_proposals=3):
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 0
    
    # By default silent - digest handles messaging
    silent = os.environ.get('DISCOVERY_SILENT', 'true').lower() == 'true'
    
    state = load_state()
    existing = load_existing_addresses()
    
    proposed_addrs = set(state.get('proposed', {}).keys())
    rejected_addrs = set(state.get('rejected', []))
    accepted_addrs = set(state.get('accepted', []))
    already_seen = existing | proposed_addrs | rejected_addrs | accepted_addrs
    
    logger.info(f"Scanning last {hours_back}h for new candidates...")
    logger.info(f"  Already known: {len(existing)} in seeds, {len(proposed_addrs)} proposed, {len(rejected_addrs)} rejected")
    
    txs = fetch_transfers(hours_back=hours_back)
    logger.info(f"Fetched {len(txs)} transfers")
    
    if not txs:
        return 0
    
    # Aggregate
    received = defaultdict(float)
    sent = defaultdict(float)
    sources = defaultdict(set)
    dests = defaultdict(set)
    for tx in txs:
        received[tx['to']] += tx['amount']
        sent[tx['from']] += tx['amount']
        sources[tx['to']].add(tx['from'])
        dests[tx['from']].add(tx['to'])
    
    # Filter candidates
    candidates = []
    for addr, r in received.items():
        if addr in KNOWN_IGNORE or addr in already_seen:
            continue
        if r < MIN_RECEIVED:
            continue
        s = sent.get(addr, 0)
        retention_pct = (1 - s/r) * 100 if r > 0 else 0
        if retention_pct < MIN_RETENTION_PCT:
            continue
        
        pattern, reason = classify_pattern(r, s, sources.get(addr, set()), dests.get(addr, set()))
        
        # Skip pure noise patterns
        if pattern == 'MIXED':
            continue
        
        candidate = {
            'address': addr,
            'received_strk': round(r, 2),
            'sent_strk': round(s, 2),
            'retention_pct': round(retention_pct, 2),
            'n_sources': len(sources.get(addr, set())),
            'n_destinations': len(dests.get(addr, set())),
            'pattern': pattern,
            'pattern_reason': reason,
        }
        candidates.append(candidate)
    
    logger.info(f"Found {len(candidates)} candidates matching criteria")
    
    if not candidates:
        return 0
    
    # Get current balance for scoring & filter dumped
    for c in candidates[:15]:  # cap balance calls
        bal = get_current_balance(c['address'])
        time.sleep(0.3)
        c['current_balance'] = round(bal, 2) if bal else 0
        c['retention_today_pct'] = (bal / c['received_strk'] * 100) if bal and c['received_strk'] > 0 else 0
        c['score'] = score_candidate(c)
    
    # Filter out those who dumped since receipt
    candidates = [c for c in candidates if c.get('retention_today_pct', 0) >= 50]
    candidates.sort(key=lambda x: -x.get('score', 0))
    
    logger.info(f"After post-window filter: {len(candidates)} candidates")
    
    # Save all candidates for reference
    with open(CANDIDATES_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'as_of': datetime.now(timezone.utc).isoformat(),
            'window_hours': hours_back,
            'candidates': candidates,
        }, f, indent=2, ensure_ascii=False)
    
    # Propose top N
    top = candidates[:max_proposals]
    sent_count = 0
    for rank, c in enumerate(top, 1):
        # In silent mode - don't spam Telegram; decision_layer will act
        if not silent:
            msg = format_proposal(c, rank)
            if send_telegram(msg):
                sent_count += 1
        else:
            sent_count += 1  # still count as "proposed"
        
        state['proposed'][c['address']] = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'pattern': c['pattern'],
            'received_strk': c['received_strk'],
            'score': c['score'],
        }
        logger.info(f"  Proposed #{rank}: {c['address']} · {c['pattern']} · {c['received_strk']/1e6:.1f}M")
        if not silent:
            time.sleep(1)
    
    save_state(state)
    logger.info(f"Sent {sent_count} discovery proposals")
    return sent_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', type=int, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument('--max', type=int, default=3, help='Max proposals per run')
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("AUTO DISCOVERY · scan for new watchlist candidates")
    logger.info("=" * 60)
    
    n = run_discovery(hours_back=args.hours, max_proposals=args.max)
    logger.info(f"\n[DONE] Proposed {n} new candidates")
    return 0


if __name__ == '__main__':
    sys.exit(main())
