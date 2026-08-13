#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dune_sector_collector.py — collector для sector rotation Dune queries.

Отдельно от strk-specific dune_collector.py — чтобы не мешать основной pipeline.
Используется LAB режимом (portfolio rotation compass).

Queries:
  DUNE_QUERY_ID_SECTOR_NETFLOW — 8317444 (Net Flow by Token)
  DUNE_QUERY_ID_SECTOR_MOMENTUM — 8317478 (Net Flow + Price Change)

Cache: 24h — 60 exec/mo на 2 queries = ~3000 credits на Analyst tier.

ENV:
  DUNE_API_KEY — уже установлен для основного collector
  DUNE_QUERY_ID_SECTOR_NETFLOW
  DUNE_QUERY_ID_SECTOR_MOMENTUM
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'

NETFLOW_CACHE = CACHE_DIR / 'dune_sector_netflow.json'
MOMENTUM_CACHE = CACHE_DIR / 'dune_sector_momentum.json'

DUNE_API = 'https://api.dune.com/api/v1'
CACHE_MAX_AGE_HOURS = 24  # 30 exec/mo per query
POLL_INTERVAL = 3
POLL_MAX = 100


def dune_req(path, api_key, method='GET', body=None):
    url = f"{DUNE_API}{path}"
    headers = {'X-Dune-API-Key': api_key, 'Content-Type': 'application/json'}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def cache_fresh(path, max_age_h):
    if not path.exists():
        return False
    try:
        d = json.load(open(path))
        ts = datetime.fromisoformat(d.get('collected_at', ''))
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return age < max_age_h
    except Exception:
        return False


def fetch_query(qid, cache_path, name, api_key):
    if cache_fresh(cache_path, CACHE_MAX_AGE_HOURS):
        logger.info(f"  ✓ {name} cache fresh (< {CACHE_MAX_AGE_HOURS}h) — skip")
        return True

    try:
        logger.info(f"  Executing {name} (query {qid})...")
        exec_resp = dune_req(f'/query/{qid}/execute', api_key, method='POST')
        exec_id = exec_resp.get('execution_id')
        if not exec_id:
            logger.warning(f"  No execution_id: {exec_resp}")
            return False
        logger.info(f"  execution_id: {exec_id}")

        for i in range(POLL_MAX):
            st = dune_req(f'/execution/{exec_id}/status', api_key)
            state = st.get('state', 'UNKNOWN')
            if state == 'QUERY_STATE_COMPLETED':
                logger.info(f"  ✓ Completed after ~{i * POLL_INTERVAL}s")
                res = dune_req(f'/execution/{exec_id}/results', api_key)
                rows = ((res.get('result') or {}).get('rows') or [])
                meta = (res.get('result') or {}).get('metadata') or {}
                out = {
                    'collected_at': datetime.now(timezone.utc).isoformat(),
                    'query_id': qid,
                    'execution_id': exec_id,
                    'row_count': len(rows),
                    'columns': meta.get('column_names', []),
                    'rows': rows,
                }
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(out, f, indent=2, ensure_ascii=False, default=str)
                logger.info(f"  ✓ Saved {len(rows)} rows to {cache_path.name}")
                return True
            elif state in ('QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED'):
                logger.warning(f"  Query {qid} failed: {state}")
                return False
            time.sleep(POLL_INTERVAL)
        logger.warning(f"  Query {qid} timed out")
        return False
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300] if e.fp else ''
        logger.error(f"  HTTP {e.code} for {name}: {body}")
        return False
    except Exception as e:
        logger.error(f"  Exception for {name}: {e}")
        return False


def main():
    logger.info("=" * 60)
    logger.info("DUNE SECTOR COLLECTOR · Portfolio Rotation Data")
    logger.info("=" * 60)

    api_key = os.environ.get('DUNE_API_KEY', '').strip()
    if not api_key:
        print("::warning::DUNE_API_KEY not set — sector collector skip")
        return 0

    q_netflow = os.environ.get('DUNE_QUERY_ID_SECTOR_NETFLOW', '').strip()
    q_momentum = os.environ.get('DUNE_QUERY_ID_SECTOR_MOMENTUM', '').strip()

    logger.info(f"Sector netflow query:  {q_netflow or 'MISSING'}")
    logger.info(f"Sector momentum query: {q_momentum or 'MISSING'}")

    if not (q_netflow or q_momentum):
        print("::warning::No sector query IDs configured — skip")
        return 0

    if q_netflow:
        logger.info("\n=== Sector Net Flow ===")
        fetch_query(q_netflow, NETFLOW_CACHE, 'netflow', api_key)

    if q_momentum:
        logger.info("\n=== Sector Momentum (Flow + Price) ===")
        fetch_query(q_momentum, MOMENTUM_CACHE, 'momentum', api_key)

    logger.info("=" * 60)
    logger.info("Sector data collection complete")
    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())