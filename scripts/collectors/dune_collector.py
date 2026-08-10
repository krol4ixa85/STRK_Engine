#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dune_collector.py — Dune Analytics API collector для Starknet network stats.

Работает с Dune Free tier:
  - 40 executions/month
  - 1000 credits/month (~25 per query)
  - Rate limit 500 req/day

Стратегия: 1 aggregate query в день (30/mo), 1 weekly deep query (4/mo) = 34/40.

ENV:
  DUNE_API_KEY — из https://dune.com/settings/api (free tier доступен)
  DUNE_QUERY_ID_DAILY — id сохранённого daily query в Dune UI
  DUNE_QUERY_ID_WEEKLY — id сохранённого weekly query (optional)

Output:
  data/cache/dune_starknet.json — daily metrics
  data/cache/dune_starknet_weekly.json — weekly metrics (если query_id задан)

Behavior:
  - Cache 20h — не тратим executions если данные свежие
  - Poll every 3 sec до 5 min max для execution completion
  - Fallback: если API упал — используем cache без обновления
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
DAILY_CACHE = CACHE_DIR / 'dune_starknet.json'
WEEKLY_CACHE = CACHE_DIR / 'dune_starknet_weekly.json'
MONTHLY_CACHE = CACHE_DIR / 'dune_starknet_monthly.json'
CEX_FLOW_CACHE = CACHE_DIR / 'dune_cex_flow.json'

DUNE_API_BASE = 'https://api.dune.com/api/v1'
CACHE_MAX_AGE_HOURS_DAILY = 20   # ~30/мес
CACHE_MAX_AGE_HOURS_WEEKLY = 24 * 6  # ~5/мес
CACHE_MAX_AGE_HOURS_MONTHLY = 24 * 5  # ~6/мес
CACHE_MAX_AGE_HOURS_CEX_FLOW = 24  # 1 раз/день = 30/мес
POLL_INTERVAL_SEC = 3
POLL_MAX_ATTEMPTS = 100


def dune_request(path, method='GET', body=None, api_key=''):
    """HTTP request к Dune API с error handling."""
    url = f"{DUNE_API_BASE}{path}"
    headers = {'X-Dune-API-Key': api_key, 'Content-Type': 'application/json'}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:500] if e.fp else ''
        logger.error(f"HTTP {e.code}: {error_body}")
        raise
    except Exception as e:
        logger.error(f"Request failed: {e}")
        raise


def execute_query(query_id, api_key):
    """Trigger query execution. Returns execution_id."""
    resp = dune_request(f'/query/{query_id}/execute', method='POST', api_key=api_key)
    execution_id = resp.get('execution_id')
    if not execution_id:
        raise Exception(f"No execution_id in response: {resp}")
    logger.info(f"  Started execution: {execution_id}")
    return execution_id


def poll_execution(execution_id, api_key):
    """Poll execution until completion. Return result or raise."""
    for attempt in range(POLL_MAX_ATTEMPTS):
        status_resp = dune_request(f'/execution/{execution_id}/status', api_key=api_key)
        state = status_resp.get('state', 'UNKNOWN')

        if state == 'QUERY_STATE_COMPLETED':
            logger.info(f"  ✓ Completed after {attempt * POLL_INTERVAL_SEC}s")
            result = dune_request(f'/execution/{execution_id}/results', api_key=api_key)
            return result
        elif state in ('QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED'):
            error_msg = status_resp.get('error') or state
            raise Exception(f"Execution failed: {error_msg}")
        elif state in ('QUERY_STATE_PENDING', 'QUERY_STATE_EXECUTING'):
            if attempt % 5 == 0:
                logger.info(f"  ...still running ({attempt * POLL_INTERVAL_SEC}s)")
            time.sleep(POLL_INTERVAL_SEC)
        else:
            logger.warning(f"  Unknown state: {state}")
            time.sleep(POLL_INTERVAL_SEC)

    raise Exception(f"Timeout after {POLL_MAX_ATTEMPTS * POLL_INTERVAL_SEC}s")


def is_cache_fresh(path, max_age_hours):
    """Check if cache exists and is recent enough."""
    if not path.exists():
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cached_ts = datetime.fromisoformat(data.get('collected_at', ''))
        age_h = (datetime.now(timezone.utc) - cached_ts).total_seconds() / 3600
        return age_h < max_age_hours
    except Exception:
        return False


def fetch_dune_query(query_id, api_key, cache_file, cache_max_age):
    """Full flow: check cache → execute → poll → save."""
    if is_cache_fresh(cache_file, cache_max_age):
        with open(cache_file, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        age_h = (datetime.now(timezone.utc) -
                 datetime.fromisoformat(cached['collected_at'])).total_seconds() / 3600
        logger.info(f"Using cached data ({age_h:.1f}h old, threshold {cache_max_age}h)")
        return cached

    logger.info(f"Executing Dune query {query_id}...")
    try:
        execution_id = execute_query(query_id, api_key)
        result = poll_execution(execution_id, api_key)
    except Exception as e:
        logger.error(f"Dune fetch failed: {e}")
        # Fallback to stale cache if it exists
        if cache_file.exists():
            logger.warning("Falling back to stale cache")
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    # Parse result rows
    rows = ((result.get('result') or {}).get('rows') or [])
    metadata = (result.get('result') or {}).get('metadata') or {}

    output = {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'query_id': query_id,
        'execution_id': execution_id,
        'row_count': len(rows),
        'columns': metadata.get('column_names', []),
        'rows': rows,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"  Saved {len(rows)} rows to {cache_file.name}")
    return output


def main():
    logger.info("=" * 60)
    logger.info("DUNE COLLECTOR · Starknet Analytics")
    logger.info("=" * 60)

    api_key = os.environ.get('DUNE_API_KEY', '').strip()
    if not api_key:
        logger.error("DUNE_API_KEY not set — skipping Dune collector")
        return 0  # не error, просто skip

    query_daily = os.environ.get('DUNE_QUERY_ID_DAILY', '').strip()
    query_weekly = os.environ.get('DUNE_QUERY_ID_WEEKLY', '').strip()
    query_monthly = os.environ.get('DUNE_QUERY_ID_MONTHLY', '').strip()
    query_cex_flow = os.environ.get('DUNE_QUERY_ID_CEX_FLOW', '').strip()

    if not query_daily and not query_weekly and not query_monthly and not query_cex_flow:
        logger.error("No DUNE_QUERY_ID_* configured — skipping")
        return 0

    if query_daily:
        try:
            logger.info(f"\n=== Daily query {query_daily} ===")
            fetch_dune_query(query_daily, api_key, DAILY_CACHE, CACHE_MAX_AGE_HOURS_DAILY)
        except Exception as e:
            logger.error(f"Daily query failed: {e}")

    if query_weekly:
        try:
            logger.info(f"\n=== Weekly query {query_weekly} ===")
            fetch_dune_query(query_weekly, api_key, WEEKLY_CACHE, CACHE_MAX_AGE_HOURS_WEEKLY)
        except Exception as e:
            logger.error(f"Weekly query failed: {e}")

    if query_monthly:
        try:
            logger.info(f"\n=== Monthly query {query_monthly} ===")
            fetch_dune_query(query_monthly, api_key, MONTHLY_CACHE, CACHE_MAX_AGE_HOURS_MONTHLY)
        except Exception as e:
            logger.error(f"Monthly query failed: {e}")

    if query_cex_flow:
        try:
            logger.info(f"\n=== CEX flow query {query_cex_flow} ===")
            fetch_dune_query(query_cex_flow, api_key, CEX_FLOW_CACHE, CACHE_MAX_AGE_HOURS_CEX_FLOW)
        except Exception as e:
            logger.error(f"CEX flow query failed: {e}")

    logger.info("=" * 60)
    logger.info("Dune collector complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())