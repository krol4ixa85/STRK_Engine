#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decision_layer.py — Автоматическое принятие решений вместо пользователя

Читает свежих discovery candidates и применяет rules:
  · AUTO_ACCEPT: high-confidence patterns (адрес добавляется в watchlist)
  · AUTO_REJECT: явно плохие (retention упал, CEX-финансирование)
  · QUEUE: middle-range (копится в очередь для weekly review)

Философия:
  Пользователь не хочет принимать 3+ решений в день по каждому кандидату.
  Робот берёт на себя routine decisions с высокой уверенностью.
  Пользователь получает только сводный дайджест и возможность override.

Правила auto-accept (ВСЕ должны выполняться):
  · pattern in ('ACCUMULATOR', 'PURE_HOLDER', 'MULTI_SOURCE_HOLDER')
  · retention_today_pct >= 80
  · current_balance >= 5_000_000 (серьёзный holder)
  · received_strk >= 1_000_000
  · score >= 5.0
  · n_sources >= 2 OR pattern == 'PURE_HOLDER'

Правила auto-reject (ЛЮБОЕ достаточно):
  · retention_today_pct < 30 (уже слил)
  · current_balance < 500_000
  · pattern == 'MIXED'

Прочее → QUEUE (не действие, копится для weekly review)

Все решения логируются в data/cache/decision_log.json для аудита.
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CANDIDATES_FILE = CACHE_DIR / 'auto_discovery_candidates.json'
LOG_FILE = CACHE_DIR / 'decision_log.json'
DISCOVERY_STATE = CACHE_DIR / 'auto_discovery_state.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('decision')


# ============================================================
# Rules
# ============================================================

def evaluate_candidate(c):
    """Return ('ACCEPT'/'REJECT'/'QUEUE', reason).
    
    HIGH QUALITY thresholds only - no noise allowed.
    """
    # Auto-reject conditions (any triggers)
    if c.get('retention_today_pct', 0) < 50:
        return 'REJECT', f"retention_today {c['retention_today_pct']:.0f}% < 50% (dumped)"
    if c.get('current_balance', 0) < 1_000_000:
        return 'REJECT', f"balance {c['current_balance']/1e6:.2f}M < 1M (insignificant)"
    if c.get('pattern') == 'MIXED':
        return 'REJECT', "pattern MIXED (unclear)"
    
    # Auto-accept conditions - HIGH QUALITY ONLY
    good_patterns = ('ACCUMULATOR', 'PURE_HOLDER', 'MULTI_SOURCE_HOLDER')
    if c.get('pattern') not in good_patterns:
        return 'QUEUE', f"pattern {c.get('pattern')} not high-quality"
    
    # STRICT thresholds for auto-accept:
    if c.get('retention_today_pct', 0) < 90:
        return 'QUEUE', f"retention_today {c['retention_today_pct']:.0f}% < 90% (need 90%+ for auto)"
    if c.get('current_balance', 0) < 20_000_000:
        return 'QUEUE', f"balance {c['current_balance']/1e6:.1f}M < 20M (auto requires >20M)"
    if c.get('received_strk', 0) < 2_000_000:
        return 'QUEUE', f"received {c['received_strk']/1e6:.1f}M < 2M (need larger inflow)"
    if c.get('score', 0) < 10.0:
        return 'QUEUE', f"score {c['score']} < 10.0"
    
    # Multi-source OR pure holder with big balance
    n_sources = c.get('n_sources', 0)
    if c.get('pattern') == 'PURE_HOLDER':
        # PURE_HOLDER needs balance >30M to compensate for single source
        if c.get('current_balance', 0) < 30_000_000:
            return 'QUEUE', f"PURE_HOLDER needs balance >30M (got {c['current_balance']/1e6:.1f}M)"
    else:
        # ACCUMULATOR/MULTI needs 5+ sources
        if n_sources < 5:
            return 'QUEUE', f"only {n_sources} sources, need 5+"
    
    return 'ACCEPT', f"HIGH-QUALITY: {c['pattern']}, retention {c['retention_today_pct']:.0f}%, balance {c['current_balance']/1e6:.1f}M, sources {n_sources}, score {c['score']}"


# ============================================================
# State management
# ============================================================

def load_log():
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'decisions': [], 'queued': []}


def save_log(log):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def load_discovery_state():
    if DISCOVERY_STATE.exists():
        try:
            with open(DISCOVERY_STATE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'proposed': {}, 'rejected': [], 'accepted': []}


def save_discovery_state(state):
    with open(DISCOVERY_STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def call_registry(args):
    """Call wallet_registry.py."""
    cmd = [sys.executable, str(SCRIPT_DIR / 'scripts' / 'wallet_registry.py')] + args
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=30, env=env)
        return r.returncode == 0, r.stdout
    except Exception as e:
        return False, str(e)


# ============================================================
# Main decision engine
# ============================================================

def run_decisions():
    """Process all fresh candidates through decision layer."""
    if not CANDIDATES_FILE.exists():
        logger.info("No candidates file yet (run auto_discovery first)")
        return {'accepted': [], 'rejected': [], 'queued': []}
    
    with open(CANDIDATES_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    candidates = data.get('candidates', [])
    if not candidates:
        logger.info("No candidates to decide on")
        return {'accepted': [], 'rejected': [], 'queued': []}
    
    log = load_log()
    disc_state = load_discovery_state()
    already_decided = set()
    for d in log.get('decisions', []):
        already_decided.add(d['address'])
    already_decided |= set(disc_state.get('accepted', []))
    already_decided |= set(disc_state.get('rejected', []))
    
    results = {'accepted': [], 'rejected': [], 'queued': []}
    
    for c in candidates:
        addr = c['address']
        if addr in already_decided:
            continue
        
        decision, reason = evaluate_candidate(c)
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'address': addr,
            'decision': decision,
            'reason': reason,
            'pattern': c.get('pattern'),
            'received_strk': c.get('received_strk'),
            'current_balance': c.get('current_balance'),
            'retention_today_pct': c.get('retention_today_pct'),
            'score': c.get('score'),
            'n_sources': c.get('n_sources'),
        }
        
        if decision == 'ACCEPT':
            # Auto-add to watchlist
            short = addr[2:10]
            name = f"discovered_{short}"
            ok, out = call_registry(['add', addr, name, 'watchlist',
                                     '--role', f"Auto-discovered {c.get('pattern', 'holder')} pattern",
                                     '--note', f"Auto-accepted by decision_layer at {entry['ts']}"])
            if ok:
                entry['registered_as'] = name
                disc_state.setdefault('accepted', []).append(addr)
                results['accepted'].append(entry)
                logger.info(f"  ACCEPT {addr[:12]}... as {name} · {reason}")
            else:
                entry['decision'] = 'ACCEPT_FAILED'
                entry['error'] = out[:200]
                results['queued'].append(entry)
                logger.error(f"  FAILED to add {addr}: {out[:100]}")
        
        elif decision == 'REJECT':
            disc_state.setdefault('rejected', []).append(addr)
            results['rejected'].append(entry)
            logger.info(f"  REJECT {addr[:12]}... · {reason}")
        
        else:  # QUEUE
            # Add to queue if not there
            queue_addrs = {q['address'] for q in log.get('queued', [])}
            if addr not in queue_addrs:
                log['queued'].append(entry)
                results['queued'].append(entry)
                logger.info(f"  QUEUE {addr[:12]}... · {reason}")
        
        log['decisions'].append(entry)
    
    # Persist
    log['decisions'] = log['decisions'][-1000:]  # cap history
    log['queued'] = [q for q in log.get('queued', []) 
                     if q['address'] not in disc_state.get('accepted', [])
                     and q['address'] not in disc_state.get('rejected', [])][-100:]
    save_log(log)
    save_discovery_state(disc_state)
    
    return results


def main():
    logger.info("=" * 60)
    logger.info("DECISION LAYER · auto-accept/reject discovery candidates")
    logger.info("=" * 60)
    
    r = run_decisions()
    logger.info(f"\n[SUMMARY]")
    logger.info(f"  Auto-accepted: {len(r['accepted'])}")
    logger.info(f"  Auto-rejected: {len(r['rejected'])}")
    logger.info(f"  Queued: {len(r['queued'])}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
