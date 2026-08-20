"""
STRK-specific Scanner via AVNU DEX (Starknet)
==============================================
Собирает данные для STRK через AVNU (Starknet native DEX) вместо dex.trades (Ethereum).
Более точная картина для STRK-specific decisions.

Использует: STRK AVNU DEX Net Flow — ENGINE Contract #37
Requires: DUNE_QUERY_ID_STRK_AVNU (GitHub Secret)

Output: data/cache/token_scan/STRK.json (overwrites generic dex.trades version)
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from dune_client.client import DuneClient

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache' / 'token_scan'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def aggregate_metrics(weekly_rows):
    """Wyckoff phase detection + rolling aggregates."""
    if not weekly_rows:
        return {}
    
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    
    def in_last_days(row, days):
        try:
            week_str = row.get('week') or (str(row.get('week_start') or ''))[:10]
            wk = datetime.strptime(week_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            return (now - wk).days <= days
        except:
            return False
    
    weeks_30 = [r for r in weekly_rows if in_last_days(r, 30)]
    weeks_90 = [r for r in weekly_rows if in_last_days(r, 90)]
    
    def sum_field(rows, field):
        return sum((r.get(field) or 0) for r in rows)
    
    netflow_30 = sum_field(weeks_30, 'net_flow_m_usd') * 1e6
    netflow_90 = sum_field(weeks_90, 'net_flow_m_usd') * 1e6
    netflow_180 = sum_field(weekly_rows, 'net_flow_m_usd') * 1e6
    
    positive_weeks = sum(1 for r in weekly_rows if (r.get('net_flow_m_usd') or 0) > 0)
    pct_positive = round(100 * positive_weeks / max(len(weekly_rows), 1), 1)
    
    # Streak
    streak = 0
    for r in reversed(weekly_rows):
        if (r.get('net_flow_m_usd') or 0) > 0:
            streak += 1
        else:
            break
    
    # SOS/dist events
    all_flows = [r.get('net_flow_m_usd', 0) or 0 for r in weekly_rows]
    positive_flows = [f for f in all_flows if f > 0]
    median_pos = sorted(positive_flows)[len(positive_flows)//2] if positive_flows else 0
    sos_threshold = max(0.5, median_pos * 3)  # STRK smaller volume — lower threshold
    
    sos_events = []
    dist_events = []
    for i, r in enumerate(weekly_rows):
        flow = r.get('net_flow_m_usd', 0) or 0
        if flow >= sos_threshold:
            sos_events.append({'week': r.get('week'), 'flow_m_usd': flow, 'index': i})
        elif flow <= -sos_threshold:
            dist_events.append({'week': r.get('week'), 'flow_m_usd': flow, 'index': i})
    
    recent_sos = [e for e in sos_events if e['index'] >= len(weekly_rows) - 8]
    recent_dist = [e for e in dist_events if e['index'] >= len(weekly_rows) - 8]
    
    price_now = weekly_rows[-1].get('close_price')
    price_180 = weekly_rows[0].get('close_price')
    price_90d_change = 0
    price_90 = None
    for r in reversed(weekly_rows):
        if in_last_days(r, 90) and not in_last_days(r, 80):
            price_90 = r.get('close_price')
            break
    if price_now and price_90:
        price_90d_change = ((price_now - price_90) / price_90) * 100
    
    recent_8w = weekly_rows[-8:] if len(weekly_rows) >= 8 else weekly_rows
    recent_4w = weekly_rows[-4:] if len(weekly_rows) >= 4 else weekly_rows
    recent_8w_flow = sum_field(recent_8w, 'net_flow_m_usd')
    recent_4w_flow = sum_field(recent_4w, 'net_flow_m_usd')
    
    # Wyckoff verdict
    if len(recent_dist) >= 2 and recent_4w_flow < -0.5 and price_90d_change < -10:
        verdict = 'DISTRIBUTION_ACTIVE'
    elif netflow_180 < -1e6 and recent_4w_flow < 0 and streak == 0:
        verdict = 'MARKDOWN'
    elif len(recent_sos) >= 2 and netflow_180 > 0:
        verdict = 'LATE_ACCUMULATION_OR_MARKUP'
    elif len(recent_sos) >= 1 and recent_8w_flow > 0.5:
        verdict = 'MID_ACCUMULATION_STRONG'
    elif streak >= 4 and netflow_180 > 0:
        verdict = 'MID_ACCUMULATION'
    elif streak >= 1 and recent_8w_flow > 0:
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
        'current_positive_streak_weeks': streak,
        'positive_weeks_180d': positive_weeks,
        'pct_positive_weeks_180d': pct_positive,
        'phase_verdict': verdict,
        'price_now': price_now,
        'price_180d_ago': price_180,
        'price_90d_change_pct': round(price_90d_change, 2),
        'sos_events': sos_events,
        'dist_events': dist_events,
        'recent_sos_count': len(recent_sos),
        'recent_dist_count': len(recent_dist),
        'recent_8w_netflow_m_usd': round(recent_8w_flow, 3),
        'recent_4w_netflow_m_usd': round(recent_4w_flow, 3),
    }


def main():
    api_key = os.getenv('DUNE_API_KEY')
    query_id = os.getenv('DUNE_QUERY_ID_STRK_AVNU')
    
    if not api_key:
        print('❌ DUNE_API_KEY not set'); sys.exit(1)
    if not query_id:
        print('❌ DUNE_QUERY_ID_STRK_AVNU not set'); sys.exit(1)
    
    dune = DuneClient(api_key)
    print(f'⏳ Fetching STRK from AVNU (query {query_id})...')
    
    from dune_client.query import QueryBase
    query = QueryBase(name='STRK AVNU DEX', query_id=int(query_id))
    
    try:
        result = dune.run_query(query)
        rows = result.result.rows if result.result else []
        print(f'✓ {len(rows)} weekly rows fetched from AVNU')
        
        if not rows:
            print('⚠ No data — check query returns weekly rows with net_flow_m_usd')
            sys.exit(0)
        
        # Normalize rows to standard format
        normalized = []
        for r in rows:
            normalized.append({
                'week': r.get('week') or r.get('week_start') or r.get('day'),
                'net_flow_m_usd': r.get('net_flow_m_usd') or r.get('net_flow_usd_m') or 0,
                'buy_volume_m_usd': r.get('buy_volume_m_usd') or 0,
                'sell_volume_m_usd': r.get('sell_volume_m_usd') or 0,
                'close_price': r.get('close_price') or r.get('avg_price') or r.get('price') or 0,
                'tx_count': r.get('tx_count') or 0,
            })
        
        metrics = aggregate_metrics(normalized)
        
        output = {
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'query_id': str(query_id),
            'source': 'AVNU DEX (Starknet native)',
            'token': 'STRK',
            'weeks_returned': len(normalized),
            **metrics,
            'weekly_history': normalized,
        }
        
        cache_path = CACHE_DIR / 'STRK.json'
        with open(cache_path, 'w') as f:
            json.dump(output, f, indent=2, default=str)
        
        print(f'✓ Saved STRK-AVNU → {cache_path}')
        print(f'  Verdict: {metrics.get("phase_verdict")}')
        print(f'  Streak: {metrics.get("current_positive_streak_weeks")} weeks')
        print(f'  180d netflow: ${(metrics.get("netflow_180d_usd", 0) or 0)/1e6:.2f}M')
        
    except Exception as e:
        print(f'❌ Error: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()
