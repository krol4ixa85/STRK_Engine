#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
graph_analysis.py — Depth-1 граф для watched адресов

Для каждого адреса в watchlist (или указанного):
  1. Найти всех, кто ему отправлял STRK за последние 90 дней (funders)
  2. Найти всех, кому он отправлял (destinations)
  3. Для каждого funder/dest — метка (CEX/EOA/Contract/Known)
  4. Определить кластер: есть ли общие funders у нескольких watched адресов?
  5. Сохранить как JSON + отправить summary в Telegram

Полезно для:
  - Понять источник финансирования holder'а
  - Обнаружить кластерное накопление (один big funder → 5 разных holders)
  - Проследить путь STRK на CEX (если добавили в watchlist)

Usage:
    python3 graph_analysis.py --address 0xa9d1e08c...   # один адрес
    python3 graph_analysis.py --all-watchlist           # все watchlist
    python3 graph_analysis.py --days 90                 # окно
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
GRAPH_DIR = SCRIPT_DIR / 'data' / 'graphs'
GRAPH_DIR.mkdir(parents=True, exist_ok=True)
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

# Reuse labels
LABELS = {
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('graph')


def api_call(params, timeout=30):
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error(f"API error: {e}")
        return None


def fetch_address_transfers(address, days_back=90):
    """Fetch all STRK transfers involving this address."""
    to_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - days_back * 86400
    
    # tokentx returns transfers TO and FROM address
    params = {
        'chainid': 1,
        'module': 'account',
        'action': 'tokentx',
        'contractaddress': STRK_L1,
        'address': address,
        'startblock': 0,
        'endblock': 99999999,
        'page': 1,
        'offset': 1000,
        'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    }
    data = api_call(params)
    if not data or data.get('status') != '1':
        if data and data.get('message') != 'No transactions found':
            logger.warning(f"Etherscan status 0: {data.get('message')}")
        return []
    
    filtered = []
    for tx in data.get('result', []):
        try:
            ts = int(tx['timeStamp'])
            if from_ts <= ts <= to_ts:
                filtered.append({
                    'ts': ts,
                    'from': tx['from'].lower(),
                    'to': tx['to'].lower(),
                    'amount': int(tx['value']) / (10 ** int(tx.get('tokenDecimal', 18))),
                    'tx_hash': tx['hash'],
                })
        except (KeyError, ValueError):
            continue
    return filtered


def build_address_graph(address, days_back=90):
    """Build depth-1 graph for one address."""
    logger.info(f"\nAnalyzing {address}")
    logger.info(f"  Window: last {days_back} days")
    
    address = address.lower()
    txs = fetch_address_transfers(address, days_back)
    logger.info(f"  Fetched {len(txs)} transfers")
    
    if not txs:
        return None
    
    # Aggregate funders (senders TO address) and destinations (receivers FROM address)
    funders = defaultdict(lambda: {'total_sent': 0, 'tx_count': 0, 'first_ts': None, 'last_ts': None})
    destinations = defaultdict(lambda: {'total_received': 0, 'tx_count': 0, 'first_ts': None, 'last_ts': None})
    
    total_in = 0
    total_out = 0
    
    for tx in txs:
        if tx['to'] == address:
            f = funders[tx['from']]
            f['total_sent'] += tx['amount']
            f['tx_count'] += 1
            f['first_ts'] = min(f['first_ts'], tx['ts']) if f['first_ts'] else tx['ts']
            f['last_ts'] = max(f['last_ts'], tx['ts']) if f['last_ts'] else tx['ts']
            total_in += tx['amount']
        elif tx['from'] == address:
            d = destinations[tx['to']]
            d['total_received'] += tx['amount']
            d['tx_count'] += 1
            d['first_ts'] = min(d['first_ts'], tx['ts']) if d['first_ts'] else tx['ts']
            d['last_ts'] = max(d['last_ts'], tx['ts']) if d['last_ts'] else tx['ts']
            total_out += tx['amount']
    
    # Sort by volume, take top 10 each
    top_funders = sorted(funders.items(), key=lambda x: -x[1]['total_sent'])[:10]
    top_dests = sorted(destinations.items(), key=lambda x: -x[1]['total_received'])[:10]
    
    def label_node(addr):
        if addr in LABELS:
            cat, name = LABELS[addr]
            return {'category': cat, 'label': name}
        return {'category': 'UNKNOWN', 'label': None}
    
    funders_list = []
    for addr, info in top_funders:
        node = label_node(addr)
        funders_list.append({
            'address': addr,
            'category': node['category'],
            'label': node['label'],
            'total_sent_strk': round(info['total_sent'], 2),
            'tx_count': info['tx_count'],
            'first_seen': datetime.fromtimestamp(info['first_ts'], timezone.utc).isoformat() if info['first_ts'] else None,
            'last_seen': datetime.fromtimestamp(info['last_ts'], timezone.utc).isoformat() if info['last_ts'] else None,
            'share_of_inflow_pct': round(info['total_sent'] / total_in * 100, 2) if total_in > 0 else 0,
        })
    
    dests_list = []
    for addr, info in top_dests:
        node = label_node(addr)
        dests_list.append({
            'address': addr,
            'category': node['category'],
            'label': node['label'],
            'total_received_strk': round(info['total_received'], 2),
            'tx_count': info['tx_count'],
            'first_seen': datetime.fromtimestamp(info['first_ts'], timezone.utc).isoformat() if info['first_ts'] else None,
            'last_seen': datetime.fromtimestamp(info['last_ts'], timezone.utc).isoformat() if info['last_ts'] else None,
            'share_of_outflow_pct': round(info['total_received'] / total_out * 100, 2) if total_out > 0 else 0,
        })
    
    # Analysis
    cex_inflow_share = sum(f['share_of_inflow_pct'] for f in funders_list if f['category'] == 'CEX')
    cex_outflow_share = sum(d['share_of_outflow_pct'] for d in dests_list if d['category'] == 'CEX')
    
    interpretation = []
    if cex_inflow_share > 50:
        interpretation.append(f"⚠ {cex_inflow_share:.0f}% inflow from CEX — likely retail buyer, not smart money")
    elif cex_inflow_share < 10 and total_in > 1_000_000:
        interpretation.append(f"✓ Only {cex_inflow_share:.0f}% inflow from CEX — non-retail funding source")
    
    if cex_outflow_share > 50:
        interpretation.append(f"⚠ {cex_outflow_share:.0f}% outflow to CEX — potential distribution")
    elif cex_outflow_share == 0 and total_out > 0:
        interpretation.append(f"✓ No outflow to CEX — internal movement / staking / DeFi")
    
    if total_out < total_in * 0.1:
        interpretation.append(f"✓ Very high retention ({(1-total_out/total_in)*100:.0f}%) — long-term holder pattern")
    
    if not interpretation:
        interpretation.append("Neutral flow pattern")
    
    result = {
        'address': address,
        'as_of': datetime.now(timezone.utc).isoformat(),
        'window_days': days_back,
        'total_transfers': len(txs),
        'total_inflow_strk': round(total_in, 2),
        'total_outflow_strk': round(total_out, 2),
        'net_flow_strk': round(total_in - total_out, 2),
        'retention_pct': round((1 - total_out/total_in) * 100, 2) if total_in > 0 else 0,
        'cex_inflow_share_pct': round(cex_inflow_share, 2),
        'cex_outflow_share_pct': round(cex_outflow_share, 2),
        'top_funders': funders_list,
        'top_destinations': dests_list,
        'interpretation': interpretation,
    }
    
    logger.info(f"  Inflow: {total_in:,.0f} · Outflow: {total_out:,.0f}")
    logger.info(f"  Retention: {result['retention_pct']:.1f}%")
    logger.info(f"  Top funders: {len(funders_list)}, Top dests: {len(dests_list)}")
    logger.info(f"  CEX inflow: {cex_inflow_share:.0f}%, CEX outflow: {cex_outflow_share:.0f}%")
    
    return result


def get_watchlist_addresses():
    """Get all addresses from watchlist category."""
    if not SEEDS_FILE.exists():
        return []
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        seeds = json.load(f)
    watchlist = seeds.get('watchlist', {})
    addrs = []
    for name, entry in watchlist.items():
        if name.startswith('_') or not isinstance(entry, dict):
            continue
        a = entry.get('address', '').lower()
        if a and a.startswith('0x') and len(a) == 42:
            addrs.append((name, a))
    return addrs


def find_common_funders(graphs):
    """Find funders that appear in multiple graphs (cluster indicator)."""
    funder_appearances = defaultdict(list)
    for g in graphs:
        addr = g['address']
        for f in g['top_funders']:
            if f['category'] not in ('CEX', 'BRIDGE'):  # skip infrastructure
                funder_appearances[f['address']].append({
                    'watched': addr,
                    'sent': f['total_sent_strk'],
                    'share': f['share_of_inflow_pct'],
                })
    
    # Only interesting if 2+ watched share this funder
    clusters = []
    for funder, appearances in funder_appearances.items():
        if len(appearances) >= 2:
            clusters.append({
                'common_funder': funder,
                'watched_addresses': [a['watched'] for a in appearances],
                'total_sent_strk': sum(a['sent'] for a in appearances),
                'appearances': len(appearances),
            })
    
    clusters.sort(key=lambda x: -x['total_sent_strk'])
    return clusters


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
                          'disable_web_page_preview': True}).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(r, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def format_graph_summary(g, name=None):
    label = name or g['address'][:10] + '...'
    text = f"🕸 <b>Graph · {label}</b>\n"
    text += f"<code>{g['address']}</code>\n\n"
    text += f"<b>{g['window_days']}-day flow:</b>\n"
    text += f"  Inflow: {g['total_inflow_strk']/1e6:.2f}M\n"
    text += f"  Outflow: {g['total_outflow_strk']/1e6:.2f}M\n"
    text += f"  Retention: {g['retention_pct']:.1f}%\n\n"
    
    text += f"<b>Top funders</b> ({len(g['top_funders'])}):\n"
    for f in g['top_funders'][:5]:
        cat = f['category']
        lbl = f['label'] or f['address'][:10] + '...'
        text += f"  · [{cat}] {lbl}: {f['total_sent_strk']/1e6:.2f}M ({f['share_of_inflow_pct']:.0f}%)\n"
    
    text += f"\n<b>Top destinations</b> ({len(g['top_destinations'])}):\n"
    for d in g['top_destinations'][:5]:
        cat = d['category']
        lbl = d['label'] or d['address'][:10] + '...'
        text += f"  · [{cat}] {lbl}: {d['total_received_strk']/1e6:.2f}M ({d['share_of_outflow_pct']:.0f}%)\n"
    
    text += f"\n<b>Interpretation:</b>\n"
    for i in g['interpretation']:
        text += f"  {i}\n"
    
    text += f"\n<a href='https://etherscan.io/address/{g['address']}'>Etherscan</a>"
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--address', help='Single address to analyze')
    parser.add_argument('--all-watchlist', action='store_true', help='All watchlist addresses')
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    logger.info("=" * 60)
    logger.info("GRAPH ANALYSIS · depth-1 funders/destinations")
    logger.info("=" * 60)
    
    if args.address:
        g = build_address_graph(args.address, args.days)
        if g:
            out = GRAPH_DIR / f"{args.address}.json"
            with open(out, 'w', encoding='utf-8') as f:
                json.dump(g, f, indent=2, ensure_ascii=False)
            logger.info(f"\nSaved: {out}")
            if not args.no_telegram:
                send_telegram(format_graph_summary(g))
    
    elif args.all_watchlist:
        addrs = get_watchlist_addresses()
        logger.info(f"Analyzing {len(addrs)} watchlist addresses")
        
        graphs = []
        for name, addr in addrs:
            g = build_address_graph(addr, args.days)
            if g:
                g['watchlist_name'] = name
                graphs.append(g)
                out = GRAPH_DIR / f"{addr}.json"
                with open(out, 'w', encoding='utf-8') as f:
                    json.dump(g, f, indent=2, ensure_ascii=False)
            time.sleep(0.5)
        
        # Find common funders (clusters)
        clusters = find_common_funders(graphs)
        if clusters:
            logger.info(f"\n{len(clusters)} common funder clusters detected!")
            cluster_out = GRAPH_DIR / '_clusters.json'
            with open(cluster_out, 'w', encoding='utf-8') as f:
                json.dump({
                    'as_of': datetime.now(timezone.utc).isoformat(),
                    'clusters': clusters,
                }, f, indent=2, ensure_ascii=False)
            
            # Send Telegram cluster alert
            if not args.no_telegram and clusters:
                msg = "🔗 <b>Cluster Alert</b>\n\n"
                msg += f"Common funders for {len(addrs)} watchlist addresses:\n\n"
                for c in clusters[:5]:
                    msg += f"· <code>{c['common_funder'][:10]}...</code>\n"
                    msg += f"  → {c['appearances']} watched, total {c['total_sent_strk']/1e6:.1f}M\n"
                send_telegram(msg)
    
    else:
        parser.print_help()
        return 1
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
