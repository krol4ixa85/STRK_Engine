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

# Полный universe LAB monitoring (все 9 sectors × top tokens)
# Автоматически расширяется через auto_extend_from_lab()
TRACKED_TOKENS = [
    # L2 
    'STRK', 'ZK', 'ARB', 'OP', 'MNT',
    # RWA (real world assets)
    'LINK', 'ONDO', 'CFG',
    # LST (liquid staking)
    'ETHFI', 'EIGEN', 'RPL', 'LDO',
    # INFRA
    'GRT', 'AKT', 'RNDR',
    # DeFi
    'MORPHO', 'AAVE', 'PENDLE', 'CRV', 'UNI',
    # AI Agents
    'TAO', 'AIXBT', 'FET', 'VIRTUAL',
    # DEPIN
    'FIL',
    # Gaming
    'AXS', 'IMX', 'SAND',
    # Meme (для reference, обычно не покупаем)
    'DOGE', 'PEPE', 'WIF', 'BONK',
]

def auto_extend_from_lab():
    """Adds any currently STRONG_BUY or DIVERGENCE token that's not in TRACKED_TOKENS.
    Called before scan to ensure ALL signaled tokens have cache.
    """
    lab_path = REPO_ROOT / 'data' / 'cache' / 'strk_lab_report.json'
    if not lab_path.exists():
        return []
    
    with open(lab_path) as f:
        lab = json.load(f)
    
    added = []
    tracked_set = set(TRACKED_TOKENS)
    
    for section in ('strong_buy', 'divergence', 'buy_pressure'):
        for item in lab.get(section, []):
            tok = item.get('token', '').upper()
            if tok and tok not in tracked_set:
                TRACKED_TOKENS.append(tok)
                tracked_set.add(tok)
                added.append(tok)
    
    if added:
        print(f'  ↑ Auto-added from LAB signals: {", ".join(added)}')
    return added

FRESH_HOURS = 6  # уменьшено с 24 для более свежих данных
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
    
    # ============================================================
    # WYCKOFF PHASE DETECTION v2 (smarter)
    # ============================================================
    
    all_flows = [r.get('net_flow_m_usd', 0) or 0 for r in weekly_rows]
    positive_flows = [f for f in all_flows if f > 0]
    median_positive = sorted(positive_flows)[len(positive_flows)//2] if positive_flows else 0
    sos_threshold = max(5.0, median_positive * 3)
    
    sos_events = []
    for i, r in enumerate(weekly_rows):
        flow = r.get('net_flow_m_usd', 0) or 0
        if flow >= sos_threshold:
            sos_events.append({'week': r.get('week'), 'flow_m_usd': flow, 'index': i})
    
    negative_flows = [abs(f) for f in all_flows if f < 0]
    median_negative = sorted(negative_flows)[len(negative_flows)//2] if negative_flows else 0
    dist_threshold = max(5.0, median_negative * 3)
    
    dist_events = []
    for i, r in enumerate(weekly_rows):
        flow = r.get('net_flow_m_usd', 0) or 0
        if abs(flow) >= dist_threshold and flow < 0:
            dist_events.append({'week': r.get('week'), 'flow_m_usd': flow, 'index': i})
    
    recent_8w = weekly_rows[-8:] if len(weekly_rows) >= 8 else weekly_rows
    recent_netflow = sum(r.get('net_flow_m_usd', 0) or 0 for r in recent_8w)
    recent_positive = sum(1 for r in recent_8w if (r.get('net_flow_m_usd', 0) or 0) > 0)
    
    recent_4w = weekly_rows[-4:] if len(weekly_rows) >= 4 else weekly_rows
    recent_4w_flow = sum(r.get('net_flow_m_usd', 0) or 0 for r in recent_4w)
    
    price_90d_change = 0
    if price_now and price_90:
        price_90d_change = ((price_now - price_90) / price_90) * 100
    
    recent_sos = [e for e in sos_events if e['index'] >= len(weekly_rows) - 8]
    recent_dist = [e for e in dist_events if e['index'] >= len(weekly_rows) - 8]
    
    # Verdict logic (Wyckoff-informed)
    if len(recent_dist) >= 2 and recent_4w_flow < -10 and price_90d_change < -10:
        verdict = 'DISTRIBUTION_ACTIVE'
    elif netflow_180 < -20 and recent_4w_flow < 0 and streak == 0:
        verdict = 'MARKDOWN'
    elif len(recent_sos) >= 2 and netflow_180 > 0:
        verdict = 'LATE_ACCUMULATION_OR_MARKUP'
    elif len(recent_sos) >= 1 and recent_netflow > 5:
        verdict = 'MID_ACCUMULATION_STRONG'
    elif streak >= 4 and netflow_180 > 0:
        verdict = 'MID_ACCUMULATION'
    elif streak >= 1 and recent_netflow > 0:
        verdict = 'EARLY_ACCUMULATION'
    elif pct_positive >= 45 and netflow_180 > 0 and price_90d_change > -15:
        verdict = 'ACCUMULATION_PHASE_B'
    elif netflow_180 < 0 and pct_positive < 40:
        verdict = 'WEAKENING'
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
        'price_90d_change_pct': round(price_90d_change, 2),
        'sos_events': sos_events,
        'dist_events': dist_events,
        'recent_sos_count': len(recent_sos),
        'recent_dist_count': len(recent_dist),
        'recent_8w_netflow_m_usd': round(recent_netflow, 2),
        'recent_4w_netflow_m_usd': round(recent_4w_flow, 2),
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
    
    # v2.1 · retry с экспоненциальным бэкоффом при 429 (too many requests).
    # Раньше падало сразу — а Dune 429 очень часто ловит при одновременном
    # запуске нескольких job. Ждём и повторяем, дан бюджет ~90 сек всего.
    import time
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
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
            msg = str(e)
            is_429 = '429' in msg or 'too many' in msg.lower()
            if is_429 and attempt < max_attempts:
                wait = 15 * attempt  # 15s, 30s, 45s — суммарно ~90 сек
                print(f'  ⏳ {token}: Dune rate-limit (attempt {attempt}/{max_attempts}), жду {wait} сек...')
                time.sleep(wait)
                continue
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
    # Auto-extend TRACKED_TOKENS to include current STRONG_BUY / DIVERGENCE
    if not args.token:
        auto_extend_from_lab()
    
    if args.token:
        tokens = [args.token.upper()]
    elif args.strong_buy_only:
        # Daily scan: STRONG_BUY + DIVERGENCE (user clicks on both)
        lab_path = REPO_ROOT / 'data' / 'cache' / 'strk_lab_report.json'
        if lab_path.exists():
            with open(lab_path) as f:
                lab = json.load(f)
            sb = [item['token'] for item in lab.get('strong_buy', [])]
            dv = [item['token'] for item in lab.get('divergence', [])]
            tokens = list(set(sb + dv + ['STRK']))  # always include STRK
            print(f'Daily refresh tokens: {tokens}')
        else:
            tokens = TRACKED_TOKENS[:10]
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
        
        # Force refresh если: --force, или --strong-buy-only (daily job), 
        # или single token request (--token X)
        force_this = args.force or args.strong_buy_only or bool(args.token)
        
        if not force_this and status == 'fresh':
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
