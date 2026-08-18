#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dune_extended_collector.py — extended Dune queries (fork из community dashboards).

3 queries:
  1. Whale movements 24h (DUNE_QUERY_ID_WHALES_24H) — daily
  2. Starknet DAU (DUNE_QUERY_ID_STARKNET_DAU) — weekly
  3. Starknet new wallets (DUNE_QUERY_ID_STARKNET_NEW_WALLETS) — weekly

Cost: ~50 credits per query execute.
Monthly estimate: ~1900 credits (48% budget).

Дни выполнения:
  - Whales: каждый день (лог всё для tracking)
  - DAU / New wallets: только по воскресеньям (weekly summary)

Все queries используют existing DUNE_API_KEY.
"""
import os
import sys
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = CACHE_DIR / 'dune_extended.json'

DUNE_API_KEY = os.getenv('DUNE_API_KEY')
QUERY_IDS = {
    'whales_24h': os.getenv('DUNE_QUERY_ID_WHALES_24H'),
    'starknet_dau': os.getenv('DUNE_QUERY_ID_STARKNET_DAU'),
    'starknet_new_wallets': os.getenv('DUNE_QUERY_ID_STARKNET_NEW_WALLETS'),
}

BASE_URL = 'https://api.dune.com/api/v1'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def dune_api_request(method, endpoint, body=None):
    """Wrapper для Dune API calls."""
    if not DUNE_API_KEY:
        return None
    url = f'{BASE_URL}{endpoint}'
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            'X-Dune-API-Key': DUNE_API_KEY,
            'Content-Type': 'application/json',
        },
    )
    if body:
        req.data = json.dumps(body).encode('utf-8')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning(f'Dune API error {e.code}: {e.reason}')
        return None
    except Exception as e:
        logger.warning(f'Dune API error: {e}')
        return None


def execute_query(query_id, poll_interval=5, max_wait=300):
    """Execute query, poll до готовности, вернуть rows.
    Returns: {rows, execution_id, status, cost_credits} или None.
    """
    if not query_id:
        return None

    # 1. Trigger execution
    logger.info(f'  Triggering execution of query {query_id}...')
    exec_resp = dune_api_request('POST', f'/query/{query_id}/execute')
    if not exec_resp:
        return None
    exec_id = exec_resp.get('execution_id')
    if not exec_id:
        logger.warning(f'  No execution_id in response')
        return None

    # 2. Poll status
    logger.info(f'  Polling execution {exec_id}...')
    waited = 0
    while waited < max_wait:
        status_resp = dune_api_request('GET', f'/execution/{exec_id}/status')
        if not status_resp:
            time.sleep(poll_interval)
            waited += poll_interval
            continue
        state = status_resp.get('state')
        if state == 'QUERY_STATE_COMPLETED':
            break
        if state in ('QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED'):
            logger.warning(f'  Query failed: {state}')
            return None
        time.sleep(poll_interval)
        waited += poll_interval

    if waited >= max_wait:
        logger.warning(f'  Query timeout после {max_wait}s')
        return None

    # 3. Fetch results
    result = dune_api_request('GET', f'/execution/{exec_id}/results')
    if not result:
        return None

    rows = result.get('result', {}).get('rows', [])
    metadata = result.get('result', {}).get('metadata', {})

    return {
        'rows': rows,
        'execution_id': exec_id,
        'columns': metadata.get('column_names', []),
        'row_count': len(rows),
    }


def main():
    logger.info('=' * 60)
    logger.info('DUNE EXTENDED COLLECTOR')
    logger.info('=' * 60)

    if not DUNE_API_KEY:
        logger.error('DUNE_API_KEY not set — cannot proceed')
        return 1

    # Determine what to run based on day of week
    today = datetime.now(timezone.utc).weekday()  # 0=Mon, 6=Sun
    is_weekly = (today == 6)  # only run weekly queries on Sunday

    results = {}

    # 1. Whales 24h — каждый день
    if QUERY_IDS['whales_24h']:
        logger.info(f'\n[1/3] Whale movements 24h...')
        result = execute_query(QUERY_IDS['whales_24h'])
        if result:
            results['whales_24h'] = {
                'rows': result['rows'],
                'columns': result['columns'],
                'row_count': result['row_count'],
                'fetched_at': datetime.now(timezone.utc).isoformat(),
            }
            logger.info(f'  ✓ Got {result["row_count"]} whale events')
        else:
            logger.warning('  ✗ Failed')
    else:
        logger.info('[1/3] Skipping whales (DUNE_QUERY_ID_WHALES_24H not set)')

    # 2 & 3 Weekly only
    if is_weekly:
        # Starknet DAU
        if QUERY_IDS['starknet_dau']:
            logger.info(f'\n[2/3] Starknet DAU (weekly)...')
            result = execute_query(QUERY_IDS['starknet_dau'])
            if result:
                results['starknet_dau'] = {
                    'rows': result['rows'],
                    'columns': result['columns'],
                    'row_count': result['row_count'],
                    'fetched_at': datetime.now(timezone.utc).isoformat(),
                }
                logger.info(f'  ✓ Got {result["row_count"]} days of DAU')
            else:
                logger.warning('  ✗ Failed')
        else:
            logger.info('[2/3] Skipping DAU (DUNE_QUERY_ID_STARKNET_DAU not set)')

        # Starknet new wallets
        if QUERY_IDS['starknet_new_wallets']:
            logger.info(f'\n[3/3] Starknet new wallets (weekly)...')
            result = execute_query(QUERY_IDS['starknet_new_wallets'])
            if result:
                results['starknet_new_wallets'] = {
                    'rows': result['rows'],
                    'columns': result['columns'],
                    'row_count': result['row_count'],
                    'fetched_at': datetime.now(timezone.utc).isoformat(),
                }
                logger.info(f'  ✓ Got {result["row_count"]} days of new wallets')
            else:
                logger.warning('  ✗ Failed')
        else:
            logger.info('[3/3] Skipping new wallets (DUNE_QUERY_ID_STARKNET_NEW_WALLETS not set)')
    else:
        logger.info(f'\nSkipping weekly queries (today={today}, will run on Sunday)')

    if not results:
        logger.warning('No queries executed — check env vars')
        return 0

    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'dune_api_extended',
        'queries_executed': list(results.keys()),
        'results': results,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f'\nSaved to {OUTPUT_FILE.name}')
    logger.info('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())