#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
native_staking_flow.py — Native Starknet staking flow collector

Тянет total_stake через Starknet RPC на staking_contract.
Сохраняет снимок в state cache. При каждом запуске:
  · current_total_stake (STRK)
  · сравнение с предыдущим снимком → net flow с интервалом
  · сравнение со снимком 24h назад → net flow 24h
  · сравнение со снимком 7d назад → net flow 7d

State file: data/cache/native_staking_state.json
{
  "snapshots": [
    {"ts": "2026-08-06T00:00:00Z", "total_stake_strk": 950123456},
    ...
  ]
}
Держим последние 30 снимков (max ~7-10 дней при 6h cadence).

ВАЖНО: total_stake на staking_contract включает ВЕСЬ застейканный STRK,
       в том числе через Endur (xSTRK стейкает через тот же контракт).
       Для "pure native" отделяем xSTRK TVL (см. liquidity_shift.py).

Config: читает config/config.env (rpc_url, staking_contract, staking_selector).
        Если конфиг недоступен — использует дефолты из flow_seeds.json.
"""
import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = CACHE_DIR / 'native_staking_flow.json'
STATE_FILE = CACHE_DIR / 'native_staking_state.json'
CONFIG_FILE = SCRIPT_DIR / 'config' / 'config.env'

DEFAULT_STAKING_CONTRACT = '0x00ca1702e64c81d9a07b86bd2c540188d92a2c73cf5cc0e508d949015e7e84a7'
# Selector берётся из config.env — если не задан, RPC вернёт ошибку и мы напишем NOT_CHECKED
DEFAULT_RPC = 'https://rpc.starknet.lava.build'
MAX_SNAPSHOTS = 30

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('native_stake')


def load_config():
    """Read config/config.env if present."""
    conf = {
        'rpc_url': DEFAULT_RPC,
        'staking_contract': DEFAULT_STAKING_CONTRACT,
        'staking_selector': None,
    }
    if not CONFIG_FILE.exists():
        return conf
    for line in CONFIG_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            k = k.strip().lower()
            v = v.strip().strip('"').strip("'")
            if k in conf:
                conf[k] = v
    return conf


def fetch_total_stake(conf):
    """Call get_total_stake on staking contract via RPC."""
    selector = conf.get('staking_selector')
    if not selector:
        logger.warning('staking_selector not in config.env — cannot call RPC')
        return None
    payload = {
        'jsonrpc': '2.0',
        'method': 'starknet_call',
        'params': {
            'request': {
                'contract_address': conf['staking_contract'],
                'entry_point_selector': selector,
                'calldata': []
            },
            'block_id': 'latest'
        },
        'id': 1
    }
    try:
        req = urllib.request.Request(
            conf['rpc_url'],
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'User-Agent': 'STRK-Engine/1.0'}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        if 'error' in resp:
            logger.error(f'RPC error: {resp["error"]}')
            return None
        result = resp.get('result', [])
        if not result:
            return None
        # ERC20-like u256 может быть [low, high] или одиночным значением
        if len(result) >= 2:
            low = int(result[0], 16)
            high = int(result[1], 16)
            total = (high << 128) + low
        else:
            total = int(result[0], 16)
        return total / 1e18  # in STRK
    except Exception as e:
        logger.error(f'RPC call failed: {e}')
        return None


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'snapshots': []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')


def compute_deltas(state, now_ts, current_total):
    """Compute deltas over 6h/24h/7d windows from snapshot history."""
    snapshots = state.get('snapshots', [])

    def _find_at_or_before(target_ts):
        """Find closest snapshot that is at least target_ts old."""
        best = None
        for s in snapshots:
            s_dt = datetime.fromisoformat(s['ts'].replace('Z', '+00:00'))
            if s_dt <= target_ts:
                if not best or s_dt > datetime.fromisoformat(best['ts'].replace('Z', '+00:00')):
                    best = s
        return best

    result = {'prev_snapshot': None, 'delta_6h': None, 'delta_24h': None, 'delta_7d': None}
    if not snapshots:
        return result

    result['prev_snapshot'] = snapshots[-1]
    result['delta_prev'] = round(current_total - snapshots[-1]['total_stake_strk'], 2)

    d24 = _find_at_or_before(now_ts - timedelta(hours=24))
    if d24:
        result['delta_24h'] = round(current_total - d24['total_stake_strk'], 2)
        result['delta_24h_from_ts'] = d24['ts']
    d7 = _find_at_or_before(now_ts - timedelta(days=7))
    if d7:
        result['delta_7d'] = round(current_total - d7['total_stake_strk'], 2)
        result['delta_7d_from_ts'] = d7['ts']
    return result


def classify(delta_24h, current_total):
    if delta_24h is None or current_total is None or current_total <= 0:
        return 'UNKNOWN'
    pct = abs(delta_24h) / current_total * 100
    if pct < 0.5:  # native staking двигается медленно, 0.5% уже заметно
        return 'STABLE'
    elif delta_24h > 0:
        return 'STAKE_INFLOW'
    else:
        return 'STAKE_OUTFLOW'


def main():
    logger.info('=' * 60)
    logger.info('NATIVE STAKING FLOW · Starknet staking_contract')
    logger.info('=' * 60)

    conf = load_config()
    logger.info(f'Contract: {conf["staking_contract"]}')

    current = fetch_total_stake(conf)
    now = datetime.now(timezone.utc)

    if current is None:
        # If RPC unavailable/selector missing, write NOT_CHECKED
        result = {
            'as_of': now.isoformat(),
            'status': 'NOT_CHECKED',
            'reason': 'RPC unavailable or staking_selector not configured',
            'contract': conf['staking_contract'],
            'signal': 'UNKNOWN',
        }
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        logger.warning('total_stake unavailable — wrote NOT_CHECKED')
        return 0

    state = load_state()
    deltas = compute_deltas(state, now, current)
    signal = classify(deltas.get('delta_24h'), current)

    # Add new snapshot, trim old
    state['snapshots'].append({
        'ts': now.isoformat(),
        'total_stake_strk': round(current, 2),
    })
    state['snapshots'] = state['snapshots'][-MAX_SNAPSHOTS:]
    save_state(state)

    result = {
        'as_of': now.isoformat(),
        'contract': conf['staking_contract'],
        'total_stake_strk_now': round(current, 2),
        'deltas': deltas,
        'signal': signal,
        'note': ('total_stake includes STRK staked via Endur LST — '
                 'subtract endur_lst_flow.tvl_strk_now for pure native stake'),
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(f'\nTotal stake now: {current:,.0f} STRK')
    if deltas.get('delta_prev') is not None:
        logger.info(f'Δ since last snapshot: {deltas["delta_prev"]:+,.0f} STRK')
    if deltas.get('delta_24h') is not None:
        logger.info(f'Δ24h: {deltas["delta_24h"]:+,.0f} STRK')
    if deltas.get('delta_7d') is not None:
        logger.info(f'Δ7d: {deltas["delta_7d"]:+,.0f} STRK')
    logger.info(f'Signal: {signal}')
    logger.info(f'Saved: {OUTPUT_FILE}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
