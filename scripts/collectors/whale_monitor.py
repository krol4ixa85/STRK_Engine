#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whale_monitor.py — Real-time STRK Whale Transaction Monitor

Замена Discord alert bot (который требует Nansen подписку).
Каждый запуск (обычно /30мин) проверяет STRK Transfer events за последние
N минут. Если найден transfer >5M STRK — классифицирует и алертит в Telegram.

Логика классификации по playbook_flow:
  · Bridge → any: BRIDGE_OUT (потенциальный exit L2)
  · Any → Bridge: BRIDGE_IN (потенциальный accumulation intent)
  · CEX → CEX (same/different): REBALANCE (infrastructure)
  · CEX → EOA: DISTRIBUTION_RECEIVE (буду накапливать?)
  · EOA → CEX: DISTRIBUTION_SEND (продать намерение)
  · Custody → CEX: DISTRIBUTION_CUSTODY (crítico!)
  · Multisig → any: TEAM_ACTIVITY
  · Other: UNKNOWN_LARGE

Anti-spam:
  · Cooldown 4 часа per (from, to, similar_amount) pattern
  · State в data/cache/whale_monitor_state.json

Usage:
    python3 whale_monitor.py --once     # single check
    python3 whale_monitor.py            # loop mode
    python3 whale_monitor.py --window 60 # 60min window вместо 30min
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

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = CACHE_DIR / 'whale_monitor_state.json'
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

# Static known CEX addresses (baseline)
STATIC_LABELS = {
    '0x28c6c06298d514db089934071355e5743bf21d60': ('CEX', 'Binance 14'),
    '0x21a31ee1afc51d94c2efccaa2092ad1028285549': ('CEX', 'Binance 15'),
    '0xdfd5293d8e347dfe59e90efd55b2956a1343963d': ('CEX', 'Binance 16'),
    '0x56eddb7aa87536c09ccc2793473599fd21a8b17f': ('CEX', 'Binance 17'),
    '0x9696f59e4d72e237be84ffd425dcad154bf96976': ('CEX', 'Binance 18'),
    '0x5a52e96bacdabb82fd05763e25335261b270efcb': ('CEX', 'Binance 25'),
    '0xf977814e90da44bfa03b6295a0616a897441acec': ('CEX', 'Binance 8'),
    '0xa7efae728d2936e78bda97dc267687568dd593f4': ('CEX', 'OKX'),
    '0xe93685f3bba03016f02bd1828badd6195988d950': ('CEX', 'OKX 8'),
    '0xf89d7b9c864f589bbf53a82105107622b35eaa40': ('CEX', 'ByBit hot'),
    '0x9642b23ed1e01df1092b92641051881a322f5d4e': ('CEX', 'ByBit cold'),
    '0xce5485cfb26914c5dce00b9baf0580364dafc7a4': ('BRIDGE', 'StarkGate L1'),
    '0xa86309988947559b6e72ef716c5058f479386c0f': ('INFRA', 'Coinbase Prime Gas'),
    '0xb1c561105359f549f6e9438867b435580ba3a6b0': ('TEAM', 'Team Multisig'),
    '0xa8a5b3d0c320ac2ed724169b7f554e3740230586': ('CUSTODY', 'Transit Bridger 1'),
    '0x9b6c368d707481eb215f52b6ced3b81b281ca65c': ('CUSTODY', 'Custody Endpoint 1'),
}

CATEGORY_TO_TYPE = {
    'cex_hot_wallets_known_dynamic': 'CEX',
    'l1_infrastructure': 'BRIDGE',
    'custody_and_transit': 'CUSTODY',
    'team_and_foundation': 'TEAM',
    'watchlist': 'WATCH',
    'l2_native': 'L2',
}


def load_dynamic_labels():
    """Load addresses from wallet registry (flow_seeds.json) and merge with static."""
    labels = dict(STATIC_LABELS)  # start with static baseline
    
    if not SEEDS_FILE.exists():
        return labels
    
    try:
        with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
            seeds = json.load(f)
        
        SKIP = {'_meta', '_phantoms'}
        for cat, data in seeds.items():
            if cat in SKIP or not isinstance(data, dict):
                continue
            wallet_type = CATEGORY_TO_TYPE.get(cat, 'OTHER')
            for name, entry in data.items():
                if name.startswith('_') or not isinstance(entry, dict):
                    continue
                addr = entry.get('address', '').lower()
                if addr and addr.startswith('0x') and len(addr) == 42:
                    # Only ethereum addresses in whale_monitor scope
                    labels[addr] = (wallet_type, name.replace('_', ' ').title())
    except Exception as e:
        logging.warning(f"Could not load registry: {e}")
    
    return labels


# Load labels at module init (refreshed on each script invocation)
LABELS = load_dynamic_labels()

# Thresholds
THRESHOLDS = {
    'large': 5_000_000,      # >5M STRK = alert
    'mega': 20_000_000,      # >20M STRK = critical
    'monster': 50_000_000,   # >50M = crisis-level
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('whale')


def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'last_seen_ts': 0, 'alerted_tx_hashes': [], 'alert_history': []}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, default=str)


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
    data = api_call({
        'chainid': 1, 'module': 'block', 'action': 'getblocknobytime',
        'timestamp': ts, 'closest': 'before', 'apikey': ETHERSCAN_API_KEY,
    })
    if data and data.get('status') == '1':
        return int(data['result'])
    return None


def fetch_recent_transfers(minutes_back=30):
    """Fetch STRK Transfer events in last N minutes."""
    now = datetime.now(timezone.utc)
    to_ts = int(now.timestamp())
    from_ts = int((now - timedelta(minutes=minutes_back)).timestamp())
    
    from_block = get_block_at_time(from_ts)
    time.sleep(0.3)
    if not from_block:
        return []
    
    transfer_topic = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
    all_txs = []
    
    data = api_call({
        'chainid': 1, 'module': 'logs', 'action': 'getLogs',
        'address': STRK_L1, 'topic0': transfer_topic,
        'fromBlock': from_block, 'toBlock': 'latest',
        'page': 1, 'offset': 1000, 'apikey': ETHERSCAN_API_KEY,
    })
    
    if not data or data.get('status') != '1' or not data.get('result'):
        return []
    
    for log in data['result']:
        try:
            topics = log['topics']
            if len(topics) < 3:
                continue
            from_addr = '0x' + topics[1][-40:]
            to_addr = '0x' + topics[2][-40:]
            amount = int(log['data'], 16) / 1e18
            ts = int(log['timeStamp'], 16)
            
            if from_ts <= ts <= to_ts:
                all_txs.append({
                    'from': from_addr.lower(),
                    'to': to_addr.lower(),
                    'amount': amount,
                    'ts': ts,
                    'tx_hash': log['transactionHash'],
                    'block': int(log['blockNumber'], 16),
                })
        except (KeyError, ValueError, IndexError):
            continue
    
    return all_txs


def classify_flow(tx):
    """Classify transfer route per playbook_flow."""
    from_cat, from_name = LABELS.get(tx['from'], (None, None))
    to_cat, to_name = LABELS.get(tx['to'], (None, None))
    
    from_desc = f"{from_name}" if from_name else f"EOA {tx['from'][:8]}..."
    to_desc = f"{to_name}" if to_name else f"EOA {tx['to'][:8]}..."
    route = f"{from_desc} → {to_desc}"
    
    # Watchlist addresses take priority
    if from_cat == 'WATCH' or to_cat == 'WATCH':
        if from_cat == 'WATCH':
            return 'WATCHLIST_OUTFLOW', route, f"🎯 Watched wallet {from_name} sending — potential exit/rebalance."
        else:
            return 'WATCHLIST_INFLOW', route, f"🎯 Watched wallet {to_name} receiving — accumulation or top-up."
    
    if from_cat == 'CEX' and to_cat == 'CEX':
        return 'REBALANCE', route, 'CEX↔CEX infrastructure. Not directional.'
    if from_cat == 'BRIDGE' or to_cat == 'BRIDGE':
        if to_cat == 'BRIDGE':
            return 'BRIDGE_IN', route, 'Bridging TO L2 — potential accumulation intent, verify L2 destination.'
        else:
            return 'BRIDGE_OUT', route, 'Withdrawing FROM L2 — potential exit, verify L1 destination.'
    if from_cat == 'CUSTODY' and to_cat == 'CEX':
        return 'DISTRIBUTION_CUSTODY_CEX', route, 'CRITICAL: custody sending to CEX = pre-sell.'
    if from_cat == 'CUSTODY':
        return 'CUSTODY_MOVEMENT', route, 'Custody wallet moving — check destination role.'
    if to_cat == 'CUSTODY':
        return 'CUSTODY_INFLOW', route, 'Custody receiving — accumulation (verify retention).'
    if from_cat == 'TEAM':
        return 'TEAM_OUTFLOW', route, 'Team multisig sending — check destination (unlock? grant? vest?).'
    if to_cat == 'TEAM':
        return 'TEAM_INFLOW', route, 'Team multisig receiving — unusual.'
    if from_cat == 'CEX' and to_cat is None:
        return 'CEX_WITHDRAWAL', route, 'CEX → EOA — user withdrew (potential accumulation intent).'
    if from_cat is None and to_cat == 'CEX':
        return 'CEX_DEPOSIT', route, 'EOA → CEX — potential pre-sell (verify size/frequency).'
    if from_cat == 'INFRA':
        return 'INFRA_FUNDING', route, 'Gas-funder movement (Coinbase Prime style).'
    return 'UNKNOWN_LARGE', route, 'Neither address labeled — unlabeled whale movement.'


def is_watchlist_involved(tx):
    """Check if transaction involves a watchlist address."""
    from_cat, _ = LABELS.get(tx['from'], (None, None))
    to_cat, _ = LABELS.get(tx['to'], (None, None))
    return from_cat == 'WATCH' or to_cat == 'WATCH'


def send_telegram(text):
    """DEPRECATED — whale_monitor больше не отправляет alerts в Telegram напрямую.
    Оставлено как no-op для совместимости с внешним импортом.
    Единственный источник WATCH? — scripts/detectors/watchlist_notifier.py"""
    logger.debug("send_telegram called but disabled — whale_monitor теперь silent")
    return False


def _send_telegram_disabled(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:")
        logger.warning(text[:500])
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(r, timeout=10)
        logger.info("Telegram alert sent")
    except Exception as e:
        logger.error(f"Telegram error: {e}")



# ============================================================
# ENRICHMENT (added by watchlist_notifier feature)
# ============================================================
def _load_json_safe(name):
    """Load data/cache/<name>.json safely."""
    path = SCRIPT_DIR / 'data' / 'cache' / name if 'SCRIPT_DIR' in globals() else None
    if path is None or not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _get_address_cohort(address):
    """Return (cohort_label, source) — SMART/CUSTODY/EXCHANGE_HOT/WATCHLIST/None."""
    # From LABELS dict (whale_monitor's own)
    labels = globals().get('LABELS') or {}
    lookup = labels.get(address.lower())
    if lookup:
        cat, name = lookup if isinstance(lookup, tuple) else (lookup, None)
        return cat, name
    return None, None


def _get_address_7d_net(address):
    """Return net flow this address had over ~7 days, if in cohort_tracker.
    Returns (net_strk, source_hint) or (None, None)."""
    ct = _load_json_safe('cohort_tracker.json') or {}
    cohorts = ct.get('cohorts') or {}
    # cohort_tracker aggregates by category not by address
    # Fallback: not per-address available. Return None for now.
    return None, None


def format_alert(tx, flow_class, route, interpretation, severity):
    """Short-format whale alert per Xenia's spec:
    · WATCH? header
    · Full 42-char address (не сокращение)
    · One-line route
    · ADD verdict: NEW / ALREADY / KNOWN
    · Explorer link one line
    """
    from_addr = tx.get('from', '').lower()
    to_addr = tx.get('to', '').lower()
    amount_m = tx['amount'] / 1e6
    
    # Determine which side is "watch candidate" — новый EOA получает
    from_cohort, from_name = _get_address_cohort(from_addr)
    to_cohort, to_name = _get_address_cohort(to_addr)
    
    # Priority: unknown → CEX (distribution) или SMART → unknown (accumulation)
    if from_cohort and not to_cohort:
        # Known → unknown = watch RECEIVER
        watch_addr = to_addr
        source_label = from_name if from_name else from_cohort
        arrow_route = f"{source_label} → new EOA"
        add_verdict = "NEW"
    elif to_cohort and not from_cohort:
        # Unknown → known = watch SENDER (что-то передал в CEX/SMART)
        watch_addr = from_addr
        target_label = to_name if to_name else to_cohort
        arrow_route = f"new EOA → {target_label}"
        add_verdict = "NEW"
    elif from_cohort and to_cohort:
        # Both known — уточнение к flow, не нужно watch
        watch_addr = None
        arrow_route = f"{from_name or from_cohort} → {to_name or to_cohort}"
        add_verdict = "ALREADY (both known)"
    else:
        # Both unknown — оба candidate
        watch_addr = to_addr
        arrow_route = f"new EOA → new EOA"
        add_verdict = "NEW"
    
    # Reason one-liner
    _reason_map = {
        'SMART_DISTRIBUTION': 'SMART отправляет в CEX/EOA',
        'SMART_ACCUMULATION': 'SMART получает от неизвестного',
        'CEX_INFLOW': 'Крупный перевод в CEX',
        'CEX_OUTFLOW': 'Вывод из CEX (potential accumulation)',
        'WATCHLIST_OUTFLOW': 'Watched wallet отправляет',
        'WATCHLIST_INFLOW': 'Watched wallet получает',
    }
    reason = _reason_map.get(flow_class, flow_class)
    
    # Short format per Xenia's spec
    text = f"<b>WATCH?</b>\n"
    if watch_addr:
        text += f"<code>{watch_addr}</code>\n"
    text += f"{arrow_route} · <b>{amount_m:.2f}M</b>\n"
    text += f"<b>ADD:</b> {add_verdict}\n"
    text += f"<i>{reason}</i>\n"
    _tx_hash = tx.get("tx_hash", "")
    text += f"<a href=\"https://etherscan.io/tx/{_tx_hash}\">tx</a>"
    
    return text



def check_and_alert(minutes_back=30):
    state = load_state()
    seen_hashes = set(state.get('alerted_tx_hashes', []))
    
    logger.info(f"Checking last {minutes_back} minutes for large transfers...")
    transfers = fetch_recent_transfers(minutes_back)
    logger.info(f"Found {len(transfers)} transfers in window")
    
    new_alerts = 0
    # Load history log for de-duplication
    history_dir = SCRIPT_DIR / 'data' / 'history' if 'SCRIPT_DIR' in globals() else Path(__file__).parent.parent.parent / 'data' / 'history'
    history_dir.mkdir(parents=True, exist_ok=True)
    whale_events_log = history_dir / 'whale_events.jsonl'
    
    for tx in transfers:
        if tx['tx_hash'] in seen_hashes:
            continue
        
        # Determine involvement
        from_addr = tx.get('from', '').lower()
        to_addr = tx.get('to', '').lower()
        from_cohort, _ = _get_address_cohort(from_addr)
        to_cohort, _ = _get_address_cohort(to_addr)
        both_known = from_cohort and to_cohort
        watchlist_hit = is_watchlist_involved(tx)
        
        # ==== RULE 1: FILTER ====
        # Threshold rules:
        # · both parties known → only if > 5M (crypto whales talking to each other)
        # · at least one unknown → > 500k (potential new watch candidate)
        # · watchlist involved → > 500k (already tracked activity)
        if both_known:
            min_amt = 5_000_000  # уже видим оба, нужно что-то реально крупное чтобы alert
        elif watchlist_hit:
            min_amt = 500_000
        else:
            min_amt = 500_000  # unknown side → candidate for watch, low threshold
        
        # ==== RULE 3: LOG ALL SIGNIFICANT EVENTS (даже skipped for Telegram) ====
        # Только > 100k STRK в whale_events.jsonl, чтобы был материал для 6h digest
        if tx['amount'] >= 100_000:
            try:
                event_record = {
                    'ts': datetime.fromtimestamp(tx['ts'], timezone.utc).isoformat(),
                    'tx_hash': tx['tx_hash'],
                    'amount_strk': tx['amount'],
                    'from_addr': from_addr,
                    'to_addr': to_addr,
                    'from_cohort': from_cohort,
                    'to_cohort': to_cohort,
                    'both_known': bool(both_known),
                    'watchlist_hit': watchlist_hit,
                    'alerted_to_telegram': tx['amount'] >= min_amt,
                }
                with open(whale_events_log, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(event_record, ensure_ascii=False, default=str) + '\n')
            except Exception as _e:
                logger.warning(f"Failed to log whale event: {_e}")
        
        if tx['amount'] < min_amt:
            # Not alerted to Telegram — но уже логировано выше
            continue
        
        # Determine severity
        if tx['amount'] >= THRESHOLDS['monster']:
            severity = 'monster'
        elif tx['amount'] >= THRESHOLDS['mega']:
            severity = 'mega'
        else:
            severity = 'large'
        
        # Watchlist always at least 'large'
        if watchlist_hit and severity == 'large':
            severity = 'large'  # will render with 🎯 emoji via flow class
        
        flow_class, route, interpretation = classify_flow(tx)
        
        logger.info(f"  {severity.upper()}: {tx['amount']/1e6:.2f}M STRK · {flow_class} · {route}")
        
        # NB: whale_monitor больше НЕ отправляет alerts в Telegram напрямую.
        # Всё, что нужно — уже в data/history/whale_events.jsonl.
        # WATCH? алерты формирует watchlist_notifier.py (единственный источник в чат).
        # Digest формирует WHALE 6h aggregate из log файла.
        
        # Record
        seen_hashes.add(tx['tx_hash'])
        state['alert_history'].append({
            'ts': tx['ts'],
            'amount': tx['amount'],
            'flow_class': flow_class,
            'route': route,
            'tx_hash': tx['tx_hash'],
        })
        new_alerts += 1
    
    # Keep last 500 hashes in state
    state['alerted_tx_hashes'] = list(seen_hashes)[-500:]
    state['alert_history'] = state['alert_history'][-100:]
    state['last_check'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    
    logger.info(f"Sent {new_alerts} new alerts")
    return new_alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Single check')
    parser.add_argument('--window', type=int, default=35, help='Minutes to look back (default 35)')
    parser.add_argument('--interval', type=int, default=1800, help='Loop interval sec (default 30min)')
    args = parser.parse_args()
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    if args.once:
        check_and_alert(args.window)
        return 0
    
    logger.info(f"Whale monitor started · window={args.window}min · interval={args.interval}s")
    while True:
        try:
            check_and_alert(args.window)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error: {e}")
        time.sleep(args.interval)
    return 0


if __name__ == '__main__':
    sys.exit(main())