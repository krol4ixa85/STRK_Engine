#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whale_holdings_collector.py — smart whale tracking через 1 параметризованную Dune query.

Как работает:
  1. Читает актуальный список активных tokens (STRK + STRONG_BUY + WATCHLIST + EXITED)
  2. Для каждого token вызывает Dune query с его address
  3. Weekly cadence (не daily) → экономия credits
  4. Rolling cache (не повторяет вызовы если данные <6 дней)
  5. Monthly archive: старые snapshots архивируются
  6. 3-month retention: >90 дней архивы удаляются

Config через ENV:
  DUNE_API_KEY                — обязательно
  DUNE_QUERY_ID_WHALE_HOLDINGS — ID твоей forked query
  WHALE_HOLDINGS_MAX_TOKENS   — max tokens за run (default 5)
  WHALE_HOLDINGS_MIN_INTERVAL_HOURS — min интервал между runs (default 168 = 7 days)

Cost estimation (при query cost ~30 credits):
  5 tokens × 30 credits × 4 runs/month = 600 credits/mo
  Fits в Free tier (2500 budget - 1670 existing = 830 available)

Файлы:
  data/history/whale_holdings.jsonl        — active history
  data/archive/whale_holdings_YYYY-MM.jsonl — monthly archives
  data/cache/whale_holdings_state.json     — last run timestamps per token

Bot integration:
  Если detected significant change (new large holder, mass exit) →
  send alert через @Lab_sector_bot
"""
import os
import sys
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
ARCHIVE_DIR = SCRIPT_DIR / 'data' / 'archive'

for d in (CACHE_DIR, HISTORY_DIR, ARCHIVE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Output files
HISTORY_FILE = HISTORY_DIR / 'whale_holdings.jsonl'
STATE_FILE = CACHE_DIR / 'whale_holdings_state.json'

# Config
DUNE_API_KEY = os.getenv('DUNE_API_KEY')
QUERY_ID = os.getenv('DUNE_QUERY_ID_WHALE_HOLDINGS')
MAX_TOKENS_PER_RUN = int(os.getenv('WHALE_HOLDINGS_MAX_TOKENS', '5'))
MIN_INTERVAL_HOURS = int(os.getenv('WHALE_HOLDINGS_MIN_INTERVAL_HOURS', '168'))  # 7 days
ARCHIVE_RETENTION_DAYS = 90
POLL_INTERVAL = 5
MAX_WAIT_SECONDS = 300

BASE_URL = 'https://api.dune.com/api/v1'

# Static token registry — token symbol → contract address
# 'chain' MUST match blockchain name in Dune (lowercase: 'ethereum', 'polygon', etc)
# STRK L1 lives on Ethereum contract, NOT Starknet native — query doesn't support Starknet
# For Starknet native tokens нужен другой Dune query
# TODO: Xenia сможет расширить этот список
TOKEN_REGISTRY = {
    # === STRK — always priority ===
    'STRK': {
        'address': '0xca14007eff0db1f8135f4c25b34de49ab0d42766',
        'chain': 'ethereum',
        'priority': 'ALWAYS',  # never skip
    },
    # === STRONG_BUY sector tokens ===
    'LINK': {
        'address': '0x514910771af9ca656af840dff83e8264ecf986ca',
        'chain': 'ethereum',
        'priority': 'HIGH',
    },
    'MORPHO': {
        'address': '0x58d97b57bb95320f9a05dc918aef65434969c2b2',
        'chain': 'ethereum',
        'priority': 'HIGH',
    },
    'ETHFI': {
        'address': '0xfe0c30065b384f05761f15d0cc899d4f9f9cc0eb',
        'chain': 'ethereum',
        'priority': 'HIGH',
    },
    # === Watchlist tokens (можно добавить) ===
    # 'MNT': {'address': '...', 'chain': 'ethereum', 'priority': 'MEDIUM'},
    # 'AAVE': {'address': '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9', 'chain': 'ethereum', 'priority': 'MEDIUM'},
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    """Load last-run timestamps per token."""
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f'State load failed: {e}')
        return {}


def save_state(state):
    """Save state atomically."""
    tmp = STATE_FILE.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def token_needs_update(token, state):
    """Check if token needs fresh Dune call (based on MIN_INTERVAL_HOURS)."""
    if token not in state:
        return True
    last_run_str = state[token].get('last_run')
    if not last_run_str:
        return True
    try:
        last_run = datetime.fromisoformat(last_run_str.replace('Z', '+00:00'))
        age_h = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600
        return age_h >= MIN_INTERVAL_HOURS
    except Exception:
        return True


# ============================================================
# SMART TOKEN SELECTION
# ============================================================

def load_current_signals():
    """Read current STRONG_BUY, WATCHLIST, EXITED tokens from LAB data."""
    signals = {
        'strong_buy': [],
        'watchlist': [],
        'recently_exited': [],
    }

    # 1. Read from strk_lab_report.json (STRONG_BUY current)
    lab_path = CACHE_DIR / 'strk_lab_report.json'
    if lab_path.exists():
        try:
            with open(lab_path, 'r', encoding='utf-8') as f:
                lab = json.load(f)
            signals['strong_buy'] = [
                str(x.get('token', '')).upper()
                for x in (lab.get('strong_buy', []) or [])
                if x.get('token')
            ]
        except Exception as e:
            logger.warning(f'Failed to read lab report: {e}')

    # 2. Read from rotation_alerts.jsonl (EXITED in last 7 days)
    rot_path = HISTORY_DIR / 'rotation_alerts.jsonl'
    if rot_path.exists():
        try:
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            with open(rot_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        alert = json.loads(line)
                        ts = datetime.fromisoformat(alert.get('ts', '').replace('Z', '+00:00'))
                        if ts >= week_ago:
                            exited = alert.get('strong_to_sell', []) or alert.get('exited_strong_buy_quiet', [])
                            for t in exited:
                                if t and str(t).upper() not in signals['recently_exited']:
                                    signals['recently_exited'].append(str(t).upper())
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
        except Exception as e:
            logger.warning(f'Failed to read rotation alerts: {e}')

    return signals


def select_tokens_for_run(state):
    """Smart selection: pick tokens that need update AND are in registry.

    Priority:
      1. ALWAYS priority (STRK) — every run
      2. HIGH priority + currently in STRONG_BUY
      3. Recently EXITED (post-mortem tracking)
      4. Other HIGH priority in registry

    Filters:
      - Only tokens with entry in TOKEN_REGISTRY (we know address)
      - Only tokens that need update (>MIN_INTERVAL_HOURS since last run)
      - Cap at MAX_TOKENS_PER_RUN
    """
    signals = load_current_signals()
    logger.info(f'  Current STRONG_BUY: {signals["strong_buy"]}')
    logger.info(f'  Recently EXITED: {signals["recently_exited"]}')

    selected = []

    # 1. ALWAYS tokens (STRK)
    for token, meta in TOKEN_REGISTRY.items():
        if meta.get('priority') == 'ALWAYS':
            if token_needs_update(token, state):
                selected.append((token, 'always'))

    # 2. Currently STRONG_BUY (if in registry + needs update)
    for token in signals['strong_buy']:
        if token in TOKEN_REGISTRY and token not in [t for t, _ in selected]:
            if token_needs_update(token, state):
                selected.append((token, 'strong_buy_current'))

    # 3. Recently EXITED (post-mortem)
    for token in signals['recently_exited']:
        if token in TOKEN_REGISTRY and token not in [t for t, _ in selected]:
            if token_needs_update(token, state):
                selected.append((token, 'recently_exited'))

    # 4. Other HIGH priority (fallback)
    for token, meta in TOKEN_REGISTRY.items():
        if meta.get('priority') == 'HIGH' and token not in [t for t, _ in selected]:
            if token_needs_update(token, state):
                selected.append((token, 'high_priority'))

    # Apply cap
    if len(selected) > MAX_TOKENS_PER_RUN:
        logger.info(f'  Selection capped: {len(selected)} → {MAX_TOKENS_PER_RUN}')
        selected = selected[:MAX_TOKENS_PER_RUN]

    return selected


# ============================================================
# DUNE API
# ============================================================

def dune_api_request(method, endpoint, body=None):
    if not DUNE_API_KEY:
        return None
    url = f'{BASE_URL}{endpoint}'
    req = urllib.request.Request(
        url, method=method,
        headers={'X-Dune-API-Key': DUNE_API_KEY, 'Content-Type': 'application/json'},
    )
    if body:
        req.data = json.dumps(body).encode('utf-8')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body_resp = e.read().decode('utf-8')
        except Exception:
            body_resp = ''
        logger.warning(f'Dune API {e.code}: {e.reason} · {body_resp[:200]}')
        return None
    except Exception as e:
        logger.warning(f'Dune API exception: {e}')
        return None


def execute_query_with_params(query_id, token_address, chain='ethereum'):
    """Execute parameterized query for given token address.
    
    Query 8374340 parameters:
      - Chain: blockchain name ('ethereum', 'polygon', etc)
      - Token Address: contract address (0x...)
      - Start Date / End Date: window для dex_pools discovery
                                (главный wallet balance calc = all-time)
    """
    # Trigger with parameters (точные имена как в Dune SQL)
    body = {
        'query_parameters': {
            'Chain': chain,
            'Token Address': token_address,
            # Rolling 30-day window для dex_pools discovery
            'Start Date': (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S'),
            'End Date': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        }
    }

    logger.info(f'    Triggering with token={token_address[:10]}...')
    exec_resp = dune_api_request('POST', f'/query/{query_id}/execute', body=body)
    if not exec_resp:
        return None
    exec_id = exec_resp.get('execution_id')
    if not exec_id:
        logger.warning('    No execution_id')
        return None

    # Poll status
    waited = 0
    while waited < MAX_WAIT_SECONDS:
        status_resp = dune_api_request('GET', f'/execution/{exec_id}/status')
        if status_resp:
            state = status_resp.get('state')
            if state == 'QUERY_STATE_COMPLETED':
                break
            if state in ('QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED'):
                logger.warning(f'    Query state: {state}')
                return None
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL

    if waited >= MAX_WAIT_SECONDS:
        logger.warning(f'    Timeout после {MAX_WAIT_SECONDS}s')
        return None

    # Fetch results
    result = dune_api_request('GET', f'/execution/{exec_id}/results')
    if not result:
        return None

    return {
        'rows': result.get('result', {}).get('rows', []),
        'columns': result.get('result', {}).get('metadata', {}).get('column_names', []),
        'execution_id': exec_id,
    }


# ============================================================
# CHANGE DETECTION
# ============================================================

def detect_significant_changes(token, current_rows, previous_rows):
    """Compare current vs previous snapshot, detect meaningful changes."""
    if not previous_rows:
        return {'is_first_snapshot': True, 'changes': []}

    # Build wallet → balance maps
    # Query 8374340 returns columns: "Wallet Address", "Token Balance", "Holder", etc
    def to_map(rows):
        m = {}
        for r in rows or []:
            # Try multiple column name variants
            wallet = (r.get('Wallet Address') or r.get('wallet_address') 
                     or r.get('address') or r.get('holder'))
            balance = (r.get('Token Balance') or r.get('token_balance') 
                      or r.get('balance') or r.get('amount'))
            if wallet and balance is not None:
                try:
                    m[str(wallet).lower()] = float(balance)
                except (ValueError, TypeError):
                    continue
        return m

    curr_map = to_map(current_rows)
    prev_map = to_map(previous_rows)

    changes = []

    # New holders (в curr нет в prev)
    for wallet, balance in curr_map.items():
        if wallet not in prev_map:
            if balance > 100_000:  # min 100k tokens to be significant
                changes.append({
                    'type': 'NEW_LARGE_HOLDER',
                    'wallet': wallet,
                    'balance': balance,
                })

    # Exited holders
    for wallet, balance in prev_map.items():
        if wallet not in curr_map:
            if balance > 100_000:
                changes.append({
                    'type': 'EXITED_LARGE_HOLDER',
                    'wallet': wallet,
                    'prev_balance': balance,
                })

    # Big accumulation (>50% increase)
    for wallet, curr_bal in curr_map.items():
        if wallet in prev_map:
            prev_bal = prev_map[wallet]
            if prev_bal > 0 and curr_bal / prev_bal >= 1.5:
                changes.append({
                    'type': 'ACCUMULATED',
                    'wallet': wallet,
                    'prev_balance': prev_bal,
                    'curr_balance': curr_bal,
                    'change_pct': ((curr_bal / prev_bal) - 1) * 100,
                })

    # Big distribution (>50% decrease)
    for wallet, curr_bal in curr_map.items():
        if wallet in prev_map:
            prev_bal = prev_map[wallet]
            if prev_bal > 0 and curr_bal / prev_bal <= 0.5:
                changes.append({
                    'type': 'DISTRIBUTED',
                    'wallet': wallet,
                    'prev_balance': prev_bal,
                    'curr_balance': curr_bal,
                    'change_pct': ((curr_bal / prev_bal) - 1) * 100,
                })

    return {
        'is_first_snapshot': False,
        'total_holders_curr': len(curr_map),
        'total_holders_prev': len(prev_map),
        'changes': changes,
        'has_significant_change': len(changes) >= 2,  # threshold
    }


# ============================================================
# ARCHIVE MANAGEMENT
# ============================================================

def get_last_archive_month():
    """Find last archived month."""
    if not ARCHIVE_DIR.exists():
        return None
    archives = sorted(ARCHIVE_DIR.glob('whale_holdings_*.jsonl'))
    if not archives:
        return None
    return archives[-1].stem.replace('whale_holdings_', '')


def maybe_archive_and_cleanup():
    """
    If current month != last archive month, archive current jsonl.
    Also delete archives older than ARCHIVE_RETENTION_DAYS.
    """
    now = datetime.now(timezone.utc)
    current_month = now.strftime('%Y-%m')

    # 1. Archive if needed
    if HISTORY_FILE.exists() and HISTORY_FILE.stat().st_size > 100:
        # Read first record to check if it's from a previous month
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
            if first_line:
                first_record = json.loads(first_line)
                first_ts = datetime.fromisoformat(first_record['ts'].replace('Z', '+00:00'))
                first_month = first_ts.strftime('%Y-%m')

                if first_month != current_month:
                    # Archive previous month
                    archive_path = ARCHIVE_DIR / f'whale_holdings_{first_month}.jsonl'
                    if not archive_path.exists():
                        # Split records by month
                        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                            lines = f.readlines()

                        prev_lines = []
                        curr_lines = []
                        for line in lines:
                            try:
                                r = json.loads(line)
                                ts = datetime.fromisoformat(r['ts'].replace('Z', '+00:00'))
                                if ts.strftime('%Y-%m') == current_month:
                                    curr_lines.append(line)
                                else:
                                    prev_lines.append(line)
                            except Exception:
                                curr_lines.append(line)  # keep unparseable

                        # Write archive
                        with open(archive_path, 'w', encoding='utf-8') as f:
                            f.writelines(prev_lines)
                        logger.info(f'  Archived {len(prev_lines)} records to {archive_path.name}')

                        # Keep only current month in active
                        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                            f.writelines(curr_lines)
        except Exception as e:
            logger.warning(f'  Archive check failed: {e}')

    # 2. Delete archives >90 days
    cutoff = now - timedelta(days=ARCHIVE_RETENTION_DAYS)
    for archive in ARCHIVE_DIR.glob('whale_holdings_*.jsonl'):
        try:
            month_str = archive.stem.replace('whale_holdings_', '')
            archive_date = datetime.strptime(month_str, '%Y-%m').replace(tzinfo=timezone.utc)
            if archive_date < cutoff:
                archive.unlink()
                logger.info(f'  Deleted old archive: {archive.name}')
        except Exception:
            continue


# ============================================================
# APPEND & LOAD PREVIOUS
# ============================================================

def append_snapshot(token, rows, changes_info):
    """Append current snapshot to history JSONL.
    
    Query 8374340 returns ALL holders (can be 1000s). Мы сохраняем только top 100
    чтобы файл не разросся + добавляем summary по категориям (Whale/Shark/Dolphin/etc).
    """
    # Категоризация по Holder field  
    categories_count = {}
    for r in rows or []:
        cat = r.get('Holder', 'Unknown')
        categories_count[cat] = categories_count.get(cat, 0) + 1
    
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'token': token,
        'holders_count_total': len(rows),
        'categories_count': categories_count,  # {🐳 Whale: 5, 🦈 Shark: 15, ...}
        'top_100_holders': rows[:100],  # Only top 100 для storage
        'changes': changes_info,
    }
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, default=str) + '\n')


def load_previous_snapshot(token):
    """Load most recent snapshot for this token."""
    if not HISTORY_FILE.exists():
        return None
    latest = None
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get('token') == token:
                        latest = r
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return latest


# ============================================================
# ALERT SENDING (integration с bot)
# ============================================================

def maybe_send_alert(token, changes_info):
    """If significant change detected, log for bot to pick up."""
    if not changes_info.get('has_significant_change'):
        return
    if changes_info.get('is_first_snapshot'):
        return

    # Write alert to standard location — bot / next run picks up
    alerts_file = HISTORY_DIR / 'whale_holdings_alerts.jsonl'
    alert = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'token': token,
        'alert_type': 'WHALE_HOLDING_CHANGE',
        'changes': changes_info.get('changes', [])[:10],  # cap at 10 events
    }
    with open(alerts_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(alert, default=str) + '\n')
    logger.info(f'    Alert logged: {len(changes_info["changes"])} changes for {token}')


# ============================================================
# MAIN
# ============================================================

def main():
    logger.info('=' * 60)
    logger.info('WHALE HOLDINGS COLLECTOR')
    logger.info('=' * 60)

    if not DUNE_API_KEY:
        logger.error('DUNE_API_KEY not set')
        return 1
    if not QUERY_ID:
        logger.error('DUNE_QUERY_ID_WHALE_HOLDINGS not set')
        return 1

    # 1. Archive management
    maybe_archive_and_cleanup()

    # 2. Load state
    state = load_state()

    # 3. Smart selection
    logger.info('\nSelecting tokens for this run...')
    selected = select_tokens_for_run(state)

    if not selected:
        logger.info('  No tokens need update in this run (all recently updated)')
        return 0

    logger.info(f'  Selected {len(selected)} tokens: {[t for t, _ in selected]}')

    # 4. Execute for each token
    successful = 0
    failed = 0
    for token, reason in selected:
        meta = TOKEN_REGISTRY.get(token)
        if not meta:
            continue

        logger.info(f'\n[{token}] Reason: {reason}')

        # Execute Dune query
        result = execute_query_with_params(QUERY_ID, meta['address'], meta.get('chain', 'ethereum'))
        if not result:
            logger.warning(f'  Failed to fetch data for {token}')
            failed += 1
            state[token] = {
                'last_attempt': datetime.now(timezone.utc).isoformat(),
                'last_status': 'failed',
            }
            continue

        rows = result['rows']
        logger.info(f'  Got {len(rows)} holders')

        # Compare with previous snapshot
        previous = load_previous_snapshot(token)
        prev_rows = previous.get('top_100_holders', previous.get('top_25_holders', [])) if previous else []
        changes_info = detect_significant_changes(token, rows, prev_rows)

        if changes_info.get('is_first_snapshot'):
            logger.info(f'  First snapshot for {token} — baseline recorded')
        else:
            n_changes = len(changes_info.get('changes', []))
            if n_changes:
                logger.info(f'  Detected {n_changes} significant changes')
                for ch in changes_info['changes'][:3]:
                    logger.info(f'    · {ch["type"]}: {ch.get("wallet", "?")[:20]}...')

        # Save snapshot
        append_snapshot(token, rows, changes_info)

        # Send alert if needed
        maybe_send_alert(token, changes_info)

        # Update state
        state[token] = {
            'last_run': datetime.now(timezone.utc).isoformat(),
            'last_status': 'success',
            'last_execution_id': result['execution_id'],
            'holders_count': len(rows),
        }
        successful += 1

    # 5. Save state
    save_state(state)

    logger.info(f'\n{"=" * 60}')
    logger.info(f'Summary: {successful} success · {failed} failed')
    logger.info(f'{"=" * 60}')

    return 0


if __name__ == '__main__':
    sys.exit(main())