"""
Universal Token Scan Collector v2
==================================
Fetches per-token weekly time series from Dune (SQL v2 returns 26 weekly rows).
Aggregates rolling 30d/90d/180d + accumulation streak in Python (not SQL).
Stores in data/cache/token_scan/{TOKEN}.json for fast dashboard access.

Cache strategy:
- Fresh (<24h): skip
- Stale (1-7d): refresh
- Missing / >7d: fetch immediately

Cost: ~20-25 credits per token scan.
Budget:
    Weekly full scan (15 tokens):   ~1500 credits/month
    Daily STRONG_BUY refresh (~5):  ~900 credits/month
    On-demand at click:              ~500 credits/month
    ────────────────────────────
    Total:  ~2900 / 4000 (72%)

Requires:
    DUNE_API_KEY (env var)
    DUNE_QUERY_ID_TOKEN_SCAN (env var) - your forked query ID

Usage:
    python token_scan_collector.py                # refresh all stale
    python token_scan_collector.py --token STRK   # single token
    python token_scan_collector.py --force        # force refresh all
    python token_scan_collector.py --strong-buy-only  # daily job
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dune_client.client import DuneClient
from dune_client.query import QueryBase
from dune_client.types import QueryParameter

# ============================================================
# CONFIG
# ============================================================
REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache' / 'token_scan'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TRACKED_TOKENS = [
    'STRK', 'ZK', 'ARB', 'OP', 'MNT',           # L2
    'LINK', 'ETHFI', 'MORPHO', 'ONDO', 'CFG',   # utility (Xenia holds)
    'LDO', 'EIGEN', 'PENDLE', 'AAVE',           # LST/DeFi
    'TAO', 'RNDR', 'AIXBT', 'FET',              # AI
]

FRESH_HOURS = 24
STALE_DAYS = 7

# ============================================================
# CACHE
# ============================================================
def cache_path(token):
    return CACHE_DIR / f'{token.upper()}.json'

def cache_status(token):
    p = cache_path(token)
    if not p.exists():
        return 'missing'
    try:
        with open(p) as f:
            data = json.load(f)
        ts = datetime.fromisoformat(data['collected_at'].replace('Z', '+00:00'))
        age = datetime.now(timezone.utc) - ts
        if age < timedelta(hours=FRESH_HOURS):
            return 'fresh'
        elif age < timedelta(days=STALE_DAYS):
            return 'stale'
        return 'missing'
    except Exception:
        return 'missing'

def save_cache(token, data):
    with open(cache_path(token), 'w') as f:
        json.dump(data, f, indent=2, default=str)
    print(f'  ✓ Saved {token} → {cache_path(token).name}')

# ============================================================
# AGGREGATION IN PYTHON (from weekly rows)
# ============================================================
def aggregate_metrics(weekly_rows):
    """Compute rolling aggregates + streak from list of weekly rows.
    
    weekly_rows: [{week, net_flow_m_usd, buy_volume_m_usd, sell_volume_m_usd, close_price, ...}, ...]
    Ordered ASC by week.
    """
    if not weekly_rows:
        return {}
    
    now = datetime.now(timezone.utc)
    
    def in_last_days(row, days):
        try:
            week_str = row.get('week') or (row.get('week_start') or '')[:10]
            wk = datetime.strptime(week_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            return (now - wk).days <= days
        except Exception:
            return False
    
    # Rolling aggregates
    weeks_30 = [r for r in weekly_rows if in_last_days(r, 30)]
    weeks_90 = [r for r in weekly_rows if in_last_days(r, 90)]
    weeks_180 = weekly_rows  # already max 180d from SQL
    
    def sum_field(rows, field):
        return sum(r.get(field, 0) or 0 for r in rows)
    
    netflow_30 = sum_field(weeks_30, 'net_flow_m_usd') * 1e6
    netflow_90 = sum_field(weeks_90, 'net_flow_m_usd') * 1e6
    netflow_180 = sum_field(weeks_180, 'net_flow_m_usd') * 1e6
    
    buy_30 = sum_field(weeks_30, 'buy_volume_m_usd') * 1e6
    buy_90 = sum_field(weeks_90, 'buy_volume_m_usd') * 1e6
    buy_180 = sum_field(weeks_180, 'buy_volume_m_usd') * 1e6
    
    sell_30 = sum_field(weeks_30, 'sell_volume_m_usd') * 1e6
    sell_90 = sum_field(weeks_90, 'sell_volume_m_usd') * 1e6
    sell_180 = sum_field(weeks_180, 'sell_volume_m_usd') * 1e6
    
    # Positive weeks count
    positive_weeks = sum(1 for r in weeks_180 if (r.get('net_flow_m_usd') or 0) > 0)
    pct_positive = round(100 * positive_weeks / max(len(weeks_180), 1), 1)
    
    # Consecutive positive streak (from most recent)
    streak = 0
    for r in reversed(weekly_rows):
        if (r.get('net_flow_m_usd') or 0) > 0:
            streak += 1
        else:
            break
    
    # Prices
    def find_close(rows, days_ago):
        for r in reversed(rows):
            try:
                week_str = r.get('week') or (r.get('week_start') or '')[:10]
                wk = datetime.strptime(week_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if (now - wk).days >= days_ago:
                    return r.get('close_price')
            except Exception:
                continue
        return None
    
    price_now = weekly_rows[-1].get('close_price') if weekly_rows else None
    price_30 = find_close(weekly_rows, 30)
    price_90 = find_close(weekly_rows, 90)
    price_180 = weekly_rows[0].get('close_price') if weekly_rows else None
    
    # Phase verdict
    if netflow_180 > 0 and streak >= 8:
        verdict = 'LONG_ACCUMULATION'
    elif netflow_180 > 0 and streak >= 4:
        verdict = 'MID_ACCUMULATION'
    elif netflow_180 > 0 and streak >= 1:
        verdict = 'EARLY_ACCUMULATION'
    elif netflow_180 < 0 and streak == 0:
        verdict = 'DISTRIBUTION_OR_MARKDOWN'
    else:
        verdict = 'MIXED_OR_NEUTRAL'
    
    return {
        'netflow_30d_usd': netflow_30,
        'netflow_90d_usd': netflow_90,
        'netflow_180d_usd': netflow_180,
        'buy_30d_usd': buy_30,
        'buy_90d_usd': buy_90,
        'buy_180d_usd': buy_180,
        'sell_30d_usd': sell_30,
        'sell_90d_usd': sell_90,
        'sell_180d_usd': sell_180,
        'current_positive_streak_weeks': streak,
        'positive_weeks_180d': positive_weeks,
        'pct_positive_weeks_180d': pct_positive,
        'phase_verdict': verdict,
        'price_now': price_now,
        'price_30d_ago': price_30,
        'price_90d_ago': price_90,
        'price_180d_ago': price_180,
    }

# ============================================================
# DUNE FETCH
# ============================================================
def fetch_token_scan(token, dune_client, query_id):
    print(f'  ⏳ Fetching {token} from Dune (query {query_id})...')
    
    query = QueryBase(
        name=f'Universal Token Scan · {token}',
        query_id=int(query_id),
        params=[QueryParameter.text_type(name='token', value=token.upper())]
    )
    
    try:
        result = dune_client.run_query(query)
        rows = result.result.rows if result.result else []
        
        if not rows:
            print(f'  ⚠ {token}: no weekly data (empty result)')
            return None
        
        # Aggregate in Python
        metrics = aggregate_metrics(rows)
        
        return {
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'query_id': str(query_id),
            'execution_id': getattr(result, 'execution_id', None),
            'token': token.upper(),
            'weeks_returned': len(rows),
            **metrics,
            # 26 weekly buckets for chart
            'weekly_history': [
                {
                    'week': r.get('week') or (r.get('week_start') or '')[:10],
                    'net_flow_m_usd': r.get('net_flow_m_usd', 0),
                    'buy_volume_m_usd': r.get('buy_volume_m_usd', 0),
                    'sell_volume_m_usd': r.get('sell_volume_m_usd', 0),
                    'close_price': r.get('close_price', 0),
                    'tx_count': r.get('tx_count', 0),
                }
                for r in rows
            ]
        }
    except Exception as e:
        print(f'  ❌ {token}: {e}')
        return None

# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', help='Single token to scan')
    parser.add_argument('--force', action='store_true', help='Force refresh all')
    parser.add_argument('--strong-buy-only', action='store_true', help='Only refresh current STRONG_BUY')
    args = parser.parse_args()
    
    api_key = os.getenv('DUNE_API_KEY')
    query_id = os.getenv('DUNE_QUERY_ID_TOKEN_SCAN')
    
    if not api_key:
        print('❌ DUNE_API_KEY not set'); sys.exit(1)
    if not query_id:
        print('❌ DUNE_QUERY_ID_TOKEN_SCAN not set'); sys.exit(1)
    
    dune = DuneClient(api_key)
    
    # Determine tokens
    if args.token:
        tokens = [args.token.upper()]
    elif args.strong_buy_only:
        lab_path = REPO_ROOT / 'data' / 'cache' / 'strk_lab_report.json'
        if lab_path.exists():
            with open(lab_path) as f:
                lab = json.load(f)
            tokens = [item['token'] for item in lab.get('strong_buy', []) if item.get('token') != 'STRK']
            print(f'STRONG_BUY tokens: {tokens}')
        else:
            tokens = TRACKED_TOKENS
    else:
        tokens = TRACKED_TOKENS
    
    print(f'\n=== Token Scan Collector v2 ===')
    print(f'Target tokens: {len(tokens)}')
    print(f'Query ID: {query_id}\n')
    
    scanned = 0
    skipped = 0
    failed = 0
    
    for token in tokens:
        status = cache_status(token)
        print(f'[{token}] cache: {status}')
        
        if not args.force and status == 'fresh':
            print(f'  ↷ Skip (fresh)')
            skipped += 1
            continue
        
        data = fetch_token_scan(token, dune, query_id)
        if data:
            save_cache(token, data)
            scanned += 1
        else:
            failed += 1
    
    # Index file
    index = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'tokens': sorted([p.stem for p in CACHE_DIR.glob('*.json') if p.stem != 'index']),
        'count': len(list(CACHE_DIR.glob('*.json')))
    }
    with open(CACHE_DIR / 'index.json', 'w') as f:
        json.dump(index, f, indent=2)
    
    print(f'\n=== Summary ===')
    print(f'Scanned: {scanned} · Skipped: {skipped} · Failed: {failed}')
    print(f'Index: {index["count"]} cached tokens')

if __name__ == '__main__':
    main()