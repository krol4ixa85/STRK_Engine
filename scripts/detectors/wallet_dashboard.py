#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_dashboard.py — Генерация HTML-отчёта по одному кошельку

Собирает:
  · Полный балансовый профиль (текущий, историч. inflow/outflow)
  · Recent transactions (last 20)
  · Top funders & destinations (top 10 каждый)
  · Retention timeline (30d / 90d / 180d snapshots)
  · Классификация по паттерну
  · Interpretation

Выход:
  · data/dashboards/<addr>_<ts>.html
  · Ссылка отправляется в Telegram (или загружается как GitHub artifact)

Usage:
    python3 wallet_dashboard.py 0xa9d1e08c...
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
DASHBOARDS_DIR = SCRIPT_DIR / 'data' / 'dashboards'
DASHBOARDS_DIR.mkdir(parents=True, exist_ok=True)
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
GRAPH_DIR = SCRIPT_DIR / 'data' / 'graphs'

ETHERSCAN_BASE = 'https://api.etherscan.io/v2/api'
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY', '')
STRK_L1 = '0xca14007eff0db1f8135f4c25b34de49ab0d42766'

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
logger = logging.getLogger('dashboard')


def api_call(params, timeout=30):
    url = f"{ETHERSCAN_BASE}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'STRK-Engine/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error(f"API error: {e}")
        return None


def get_current_balance(address):
    d = api_call({
        'chainid': 1, 'module': 'account', 'action': 'tokenbalance',
        'contractaddress': STRK_L1, 'address': address, 'tag': 'latest',
        'apikey': ETHERSCAN_API_KEY,
    })
    if d and d.get('status') == '1':
        return int(d['result']) / 1e18
    return 0


def fetch_all_transfers(address, days_back=180):
    """Fetch full transfer history."""
    to_ts = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - days_back * 86400
    
    params = {
        'chainid': 1, 'module': 'account', 'action': 'tokentx',
        'contractaddress': STRK_L1, 'address': address,
        'startblock': 0, 'endblock': 99999999,
        'page': 1, 'offset': 1000, 'sort': 'desc',
        'apikey': ETHERSCAN_API_KEY,
    }
    data = api_call(params)
    if not data or data.get('status') != '1':
        return []
    
    filtered = []
    for tx in data.get('result', []):
        try:
            ts = int(tx['timeStamp'])
            if from_ts <= ts <= to_ts:
                filtered.append({
                    'ts': ts,
                    'date': datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M'),
                    'from': tx['from'].lower(),
                    'to': tx['to'].lower(),
                    'amount': int(tx['value']) / (10 ** int(tx.get('tokenDecimal', 18))),
                    'tx_hash': tx['hash'],
                })
        except (KeyError, ValueError):
            continue
    return filtered


def get_watchlist_metadata(address):
    """Get name/role from flow_seeds if exists."""
    if not SEEDS_FILE.exists():
        return None
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        seeds = json.load(f)
    for cat, data in seeds.items():
        if not isinstance(data, dict) or cat.startswith('_'):
            continue
        for name, entry in data.items():
            if isinstance(entry, dict) and entry.get('address', '').lower() == address.lower():
                return {'name': name, 'category': cat, 'role': entry.get('role', ''),
                        'note': entry.get('note', '')}
    return None


def compute_windows(txs, address):
    """Compute inflow/outflow for 7d, 30d, 90d, 180d."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    windows = {
        '7d': now_ts - 7 * 86400,
        '30d': now_ts - 30 * 86400,
        '90d': now_ts - 90 * 86400,
        '180d': now_ts - 180 * 86400,
    }
    result = {}
    for label, cutoff in windows.items():
        inflow = sum(t['amount'] for t in txs if t['ts'] >= cutoff and t['to'] == address)
        outflow = sum(t['amount'] for t in txs if t['ts'] >= cutoff and t['from'] == address)
        result[label] = {
            'inflow': round(inflow, 2),
            'outflow': round(outflow, 2),
            'net': round(inflow - outflow, 2),
            'retention_pct': round((1 - outflow/inflow) * 100, 2) if inflow > 0 else 0,
        }
    return result


def label_addr(addr):
    if addr in LABELS:
        cat, name = LABELS[addr]
        return f"[{cat}] {name}"
    return "EOA " + addr[:12] + "..."


def render_html(address, current_bal, meta, windows, top_funders, top_dests, recent_txs):
    css = """
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
    body { background: #0a0e1a; color: #e0e6f0; padding: 20px; line-height: 1.5; }
    .container { max-width: 1200px; margin: 0 auto; }
    h1 { font-size: 24px; margin-bottom: 8px; color: #4dd4ff; }
    h2 { font-size: 18px; margin: 24px 0 12px; color: #fff; border-bottom: 1px solid #223; padding-bottom: 6px; }
    .addr { font-family: monospace; color: #8ba; word-break: break-all; font-size: 13px; }
    .meta-row { color: #8a97a8; font-size: 13px; margin: 4px 0; }
    .meta-row b { color: #fff; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 16px 0; }
    .card { background: #121826; border: 1px solid #223; border-radius: 6px; padding: 14px; }
    .card-label { font-size: 11px; color: #8a97a8; text-transform: uppercase; letter-spacing: 0.5px; }
    .card-value { font-size: 20px; font-weight: 600; margin-top: 4px; }
    .green { color: #4dff8a; } .red { color: #ff6b6b; } .yellow { color: #ffd93d; }
    table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 13px; }
    th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #223; }
    th { background: #121826; color: #8a97a8; font-weight: 500; text-transform: uppercase; font-size: 11px; }
    td.num { text-align: right; font-family: monospace; }
    td.addr-cell { font-family: monospace; font-size: 12px; }
    td a { color: #4dd4ff; text-decoration: none; }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
    .tag-CEX { background: #4a1a1a; color: #ff9999; }
    .tag-BRIDGE { background: #1a4a4a; color: #99e5ff; }
    .tag-CUSTODY { background: #4a2a1a; color: #ffcc99; }
    .tag-TEAM { background: #2a1a4a; color: #cc99ff; }
    .tag-INFRA { background: #4a4a1a; color: #ffff99; }
    .tag-UNKNOWN { background: #1a2a3a; color: #8ba; }
    .interp { background: #121826; border-left: 3px solid #4dd4ff; padding: 12px 16px; margin: 12px 0; border-radius: 4px; }
    footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #223; font-size: 12px; color: #556; text-align: center; }
    """
    
    now = datetime.now(timezone.utc)
    
    # Category badge
    category = 'UNKNOWN'
    if address.lower() in LABELS:
        category = LABELS[address.lower()][0]
    elif meta:
        category = meta.get('category', 'UNKNOWN').upper()
    
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>STRK Wallet · {address[:12]}...</title>
<style>{css}</style></head><body><div class="container">

<h1>STRK Wallet Dashboard</h1>
<div class="addr">{address}</div>
"""
    
    if meta:
        html += f"""
<div class="meta-row"><b>Name:</b> {meta['name']}</div>
<div class="meta-row"><b>Category:</b> <span class="tag tag-{category}">{meta['category']}</span></div>
"""
        if meta.get('role'):
            html += f'<div class="meta-row"><b>Role:</b> {meta["role"]}</div>'
        if meta.get('note'):
            html += f'<div class="meta-row"><b>Note:</b> {meta["note"]}</div>'
    
    html += f'<div class="meta-row" style="margin-top:12px;">Report generated: {now.strftime("%Y-%m-%d %H:%M UTC")}</div>'
    
    # Current balance card
    html += '<h2>Current State</h2>'
    html += f'''
    <div class="grid">
      <div class="card"><div class="card-label">Balance now</div><div class="card-value">{current_bal/1e6:,.2f}M STRK</div></div>
    </div>
    '''
    
    # Windows summary
    html += '<h2>Flow by Window</h2>'
    html += '<div class="grid">'
    for label, w in windows.items():
        color = 'green' if w['net'] > 0 else ('red' if w['net'] < 0 else 'yellow')
        retention_color = 'green' if w['retention_pct'] > 80 else ('yellow' if w['retention_pct'] > 40 else 'red')
        html += f'''
        <div class="card">
          <div class="card-label">{label} inflow</div><div class="card-value">{w['inflow']/1e6:.2f}M</div>
          <div class="card-label" style="margin-top:8px;">{label} outflow</div><div class="card-value red">{w['outflow']/1e6:.2f}M</div>
          <div class="card-label" style="margin-top:8px;">Net</div><div class="card-value {color}">{w['net']/1e6:+.2f}M</div>
          <div class="card-label" style="margin-top:8px;">Retention</div><div class="card-value {retention_color}">{w['retention_pct']:.1f}%</div>
        </div>
        '''
    html += '</div>'
    
    # Interpretation
    html += '<h2>Interpretation</h2><div class="interp">'
    interp_lines = []
    w30 = windows['30d']
    if w30['inflow'] > 1_000_000 and w30['retention_pct'] > 80:
        interp_lines.append('✓ Strong accumulation pattern last 30d (retention >80%)')
    if w30['outflow'] > w30['inflow']:
        interp_lines.append('⚠ Net outflow last 30d — potential distribution')
    
    cex_inflow = sum(f['total_sent_strk'] for f in top_funders if f['category'] == 'CEX')
    cex_outflow = sum(d['total_received_strk'] for d in top_dests if d['category'] == 'CEX')
    total_in = sum(f['total_sent_strk'] for f in top_funders)
    total_out = sum(d['total_received_strk'] for d in top_dests)
    
    if total_in > 0:
        cex_in_pct = cex_inflow / total_in * 100
        if cex_in_pct > 50:
            interp_lines.append(f'⚠ {cex_in_pct:.0f}% inflow from CEX — likely retail buyer')
        elif cex_in_pct < 10 and total_in > 1_000_000:
            interp_lines.append(f'✓ Only {cex_in_pct:.0f}% inflow from CEX — non-retail source (whale, OTC, or internal)')
    
    if total_out > 0:
        cex_out_pct = cex_outflow / total_out * 100
        if cex_out_pct > 50:
            interp_lines.append(f'⚠ {cex_out_pct:.0f}% outflow to CEX — pre-sell risk')
        elif cex_out_pct == 0:
            interp_lines.append('✓ No CEX outflow — internal/staking/DeFi usage')
    
    if not interp_lines:
        interp_lines.append('Neutral flow pattern.')
    
    html += '<br>'.join(interp_lines)
    html += '</div>'
    
    # Top funders
    html += '<h2>Top Funders (who sent to this wallet)</h2>'
    if top_funders:
        html += '<table><thead><tr><th>Category</th><th>Address</th><th class="num">Sent</th><th class="num">Txs</th><th class="num">Share</th></tr></thead><tbody>'
        for f in top_funders:
            label = f['label'] if f['label'] else 'EOA'
            html += f'<tr><td><span class="tag tag-{f["category"]}">{f["category"]}</span> {label}</td>'
            html += f'<td class="addr-cell"><a href="https://etherscan.io/address/{f["address"]}" target="_blank">{f["address"][:12]}...</a></td>'
            html += f'<td class="num">{f["total_sent_strk"]/1e6:.2f}M</td>'
            html += f'<td class="num">{f["tx_count"]}</td>'
            html += f'<td class="num">{f["share_of_inflow_pct"]:.1f}%</td></tr>'
        html += '</tbody></table>'
    else:
        html += '<p>No funders in window.</p>'
    
    # Top destinations
    html += '<h2>Top Destinations (who this wallet sent to)</h2>'
    if top_dests:
        html += '<table><thead><tr><th>Category</th><th>Address</th><th class="num">Received</th><th class="num">Txs</th><th class="num">Share</th></tr></thead><tbody>'
        for d in top_dests:
            label = d['label'] if d['label'] else 'EOA'
            html += f'<tr><td><span class="tag tag-{d["category"]}">{d["category"]}</span> {label}</td>'
            html += f'<td class="addr-cell"><a href="https://etherscan.io/address/{d["address"]}" target="_blank">{d["address"][:12]}...</a></td>'
            html += f'<td class="num">{d["total_received_strk"]/1e6:.2f}M</td>'
            html += f'<td class="num">{d["tx_count"]}</td>'
            html += f'<td class="num">{d["share_of_outflow_pct"]:.1f}%</td></tr>'
        html += '</tbody></table>'
    else:
        html += '<p>No destinations in window.</p>'
    
    # Recent transactions
    html += '<h2>Recent Transactions (last 20)</h2>'
    if recent_txs:
        html += '<table><thead><tr><th>Date</th><th>Direction</th><th>Counterparty</th><th class="num">Amount</th><th>Tx</th></tr></thead><tbody>'
        for tx in recent_txs[:20]:
            if tx['to'] == address.lower():
                direction = '← IN'; direction_color = 'green'; other = tx['from']
            else:
                direction = '→ OUT'; direction_color = 'red'; other = tx['to']
            other_label = label_addr(other)
            html += f'<tr><td>{tx["date"]}</td>'
            html += f'<td class="{direction_color}">{direction}</td>'
            html += f'<td class="addr-cell">{other_label}<br><span style="color:#556;">{other[:16]}...</span></td>'
            html += f'<td class="num">{tx["amount"]/1e6:.3f}M</td>'
            html += f'<td><a href="https://etherscan.io/tx/{tx["tx_hash"]}" target="_blank">↗</a></td></tr>'
        html += '</tbody></table>'
    
    html += f'''
<footer>
STRK Engine · Generated {now.isoformat()}<br>
<a href="https://etherscan.io/address/{address}" style="color:#4dd4ff;">View on Etherscan</a>
</footer>

</div></body></html>'''
    return html


def send_telegram_summary(address, path, meta, current_bal, w30):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return False
    
    name = meta['name'] if meta else address[:10] + '...'
    text = f"📊 <b>Dashboard · {name}</b>\n"
    text += f"<code>{address}</code>\n\n"
    text += f"<b>Balance:</b> {current_bal/1e6:,.2f}M STRK\n"
    text += f"<b>30d inflow:</b> {w30['inflow']/1e6:.2f}M\n"
    text += f"<b>30d outflow:</b> {w30['outflow']/1e6:.2f}M\n"
    text += f"<b>30d retention:</b> {w30['retention_pct']:.1f}%\n\n"
    text += f"<i>Full report: {path.name}</i>"
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(r, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def build_dashboard(address, days_back=180):
    address = address.lower().strip()
    logger.info(f"Building dashboard for {address}")
    
    meta = get_watchlist_metadata(address)
    if meta:
        logger.info(f"  Known as: {meta['name']} ({meta['category']})")
    
    current_bal = get_current_balance(address)
    time.sleep(0.3)
    logger.info(f"  Balance: {current_bal:,.2f} STRK")
    
    txs = fetch_all_transfers(address, days_back=days_back)
    logger.info(f"  Fetched {len(txs)} transfers")
    
    if not txs and current_bal == 0:
        logger.warning("No activity found")
        return None
    
    windows = compute_windows(txs, address)
    
    # Aggregate for graph section
    funders = defaultdict(lambda: {'total_sent': 0, 'tx_count': 0})
    dests = defaultdict(lambda: {'total_received': 0, 'tx_count': 0})
    total_in = total_out = 0
    for tx in txs:
        if tx['to'] == address:
            funders[tx['from']]['total_sent'] += tx['amount']
            funders[tx['from']]['tx_count'] += 1
            total_in += tx['amount']
        elif tx['from'] == address:
            dests[tx['to']]['total_received'] += tx['amount']
            dests[tx['to']]['tx_count'] += 1
            total_out += tx['amount']
    
    top_funders = []
    for addr, info in sorted(funders.items(), key=lambda x: -x[1]['total_sent'])[:10]:
        cat, name = LABELS.get(addr, ('UNKNOWN', None))
        top_funders.append({
            'address': addr, 'category': cat, 'label': name,
            'total_sent_strk': round(info['total_sent'], 2),
            'tx_count': info['tx_count'],
            'share_of_inflow_pct': round(info['total_sent'] / total_in * 100, 2) if total_in > 0 else 0,
        })
    
    top_dests = []
    for addr, info in sorted(dests.items(), key=lambda x: -x[1]['total_received'])[:10]:
        cat, name = LABELS.get(addr, ('UNKNOWN', None))
        top_dests.append({
            'address': addr, 'category': cat, 'label': name,
            'total_received_strk': round(info['total_received'], 2),
            'tx_count': info['tx_count'],
            'share_of_outflow_pct': round(info['total_received'] / total_out * 100, 2) if total_out > 0 else 0,
        })
    
    # Render
    html = render_html(address, current_bal, meta, windows, top_funders, top_dests, txs)
    
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
    out = DASHBOARDS_DIR / f"{address}_{ts}.html"
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    logger.info(f"  Saved: {out}")
    
    # Telegram summary
    send_telegram_summary(address, out, meta, current_bal, windows['30d'])
    
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('address', help='Wallet address (0x...)')
    parser.add_argument('--days', type=int, default=180)
    args = parser.parse_args()
    
    if not ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY not set")
        return 1
    
    logger.info("=" * 60)
    logger.info("WALLET DASHBOARD")
    logger.info("=" * 60)
    
    out = build_dashboard(args.address, days_back=args.days)
    if out:
        logger.info(f"\n[DONE] {out}")
        print(str(out))
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
