#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orchestrator.py — STRK Engine Data Orchestrator
=================================================

Запускает все collectors + classifier в правильном порядке.
Собирает результаты в единый `agent_input.json` для чтения агентом
при LIQ/RUN отчёте.

ЧТО ДЕЛАЕТ:
  1. Запускает collectors/flow_eth.py (L1 flow)
  2. Запускает collectors/flow_starknet.py (L2 flow)
  3. Запускает classify_flow.py (playbook classification)
  4. Читает выходы (flow_map_summary.json)
  5. Пишет data/cache/agent_input.json — единый JSON для агента

ЧТО НЕ ДЕЛАЕТ:
  - Не пишет DECISION
  - Не решает NEW_ENTRY
  - Не совершает торговых операций

Философия: скрипты собирают, DECISION живёт в decision_contract/агенте.

Usage:
    python3 orchestrator.py                # standard daily run
    python3 orchestrator.py --deep         # 30d lookback (REVIEW mode)
    python3 orchestrator.py --skip-fetch   # только classify + aggregate,
                                             использует существующие CSV
"""

import os
import sys
import json
import argparse
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# ============================================================
# PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).parent.parent  # .../STRK_Engine
COLLECTORS_DIR = SCRIPT_DIR / 'scripts' / 'collectors'
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
LOG_FILE = SCRIPT_DIR / 'logs' / 'orchestrator.log'

CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger('orchestrator')

# ============================================================
# LOAD CONFIG.ENV (локально, для GitHub Actions уже есть env)
# ============================================================

def load_env():
    """Загружает переменные из config/config.env в os.environ (если не заданы)."""
    env_path = SCRIPT_DIR / 'config' / 'config.env'
    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Не перезаписываем уже существующие переменные (из GitHub Secrets)
                        if key not in os.environ:
                            os.environ[key] = value
        logger.info("Loaded config.env into environment")
    else:
        logger.warning("config.env not found. Rely on existing environment variables.")

load_env()

# ============================================================
# STRICT MODE CHECK (после загрузки .env)
# ============================================================

STRICT_NO_TRADING = os.environ.get('STRICT_NO_TRADING', 'true').lower() == 'true'
if not STRICT_NO_TRADING:
    logger.error("STRICT_NO_TRADING=false. Aborting.")
    sys.exit(1)

# ============================================================
# STEP RUNNERS
# ============================================================

def run_step(name: str, script_path: Path, extra_args: list = None) -> bool:
    """Runs a step, returns True on success."""
    args = [sys.executable, str(script_path)]
    if extra_args:
        args.extend(extra_args)
    
    logger.info(f"→ STEP: {name}")
    logger.info(f"  Command: {' '.join(args)}")
    
    # Force UTF-8 in subprocesses (critical for Windows with cp1251 default)
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,
            env=env,
        )
        if result.returncode == 0:
            logger.info(f"  ✓ {name} completed")
            return True
        else:
            logger.error(f"  ✗ {name} failed with code {result.returncode}")
            if result.stderr:
                logger.error(f"  --- stderr ---")
                for line in result.stderr.strip().split('\n'):
                    logger.error(f"  {line}")
                logger.error(f"  --- end stderr ---")
            if result.stdout:
                logger.error(f"  --- stdout ---")
                for line in result.stdout.strip().split('\n')[-20:]:
                    logger.error(f"  {line}")
                logger.error(f"  --- end stdout ---")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"  ✗ {name} timeout after 5min")
        return False
    except Exception as e:
        logger.error(f"  ✗ {name} exception: {e}")
        return False


# ============================================================
# AGGREGATE FOR AGENT
# ============================================================

def build_agent_input() -> dict:
    """
    Читает все выходы collectors + classifier, собирает в единый
    JSON для агента (входит в LIQ/RUN отчёт).
    """
    output = {
        '_meta': {
            'as_of': datetime.now(timezone.utc).isoformat(),
            'purpose': 'Aggregated data for STRK Engine LIQ/RUN report',
            'philosophy': 'Data only. No DECISION. Agent interprets.',
            'strict_no_trading': True,
        },
        'flow_map': None,       # MUST #6
        'l1_seeds': [],
        'l2_seeds': [],
        'not_checked': [],
    }
    
    # Flow map (from classify_flow.py)
    flow_map_path = CACHE_DIR / 'flow_map_summary.json'
    if flow_map_path.exists():
        with open(flow_map_path, 'r', encoding='utf-8') as f:
            output['flow_map'] = json.load(f)
    else:
        output['not_checked'].append('flow_map')
    
    # L1 seeds detail
    l1_path = CACHE_DIR / 'flow_eth_summary.json'
    if l1_path.exists():
        with open(l1_path, 'r', encoding='utf-8') as f:
            l1_data = json.load(f)
        output['l1_seeds'] = l1_data.get('seeds_summary', [])
    else:
        output['not_checked'].append('flow_eth')
    
    # L2 seeds detail
    l2_path = CACHE_DIR / 'flow_starknet_summary.json'
    if l2_path.exists():
        with open(l2_path, 'r', encoding='utf-8') as f:
            l2_data = json.load(f)
        output['l2_seeds'] = l2_data.get('seeds_summary', [])
    else:
        output['not_checked'].append('flow_starknet')
    
    return output


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deep', action='store_true', help='30d lookback')
    parser.add_argument('--skip-fetch', action='store_true',
                        help='Skip collectors, only classify (use existing CSV)')
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info(f"orchestrator.py starting · deep={args.deep} · skip_fetch={args.skip_fetch}")
    logger.info("=" * 60)
    
    steps_ok = True
    
    if not args.skip_fetch:
        # STEP 1: L1 flow
        extra = ['--deep'] if args.deep else []
        if not run_step('flow_eth (L1)', COLLECTORS_DIR / 'flow_eth.py', extra):
            steps_ok = False
        
        # STEP 2: L2 flow
        if not run_step('flow_starknet (L2)', COLLECTORS_DIR / 'flow_starknet.py', extra):
            steps_ok = False
    
    # STEP 3: Classify (даже если fetch пропустили)
    if not run_step('classify_flow', SCRIPT_DIR / 'scripts' / 'classify_flow.py'):
        steps_ok = False
    
    # STEP 4: Aggregate for agent
    logger.info("→ STEP: build agent_input.json")
    agent_input = build_agent_input()
    agent_input_path = CACHE_DIR / 'agent_input.json'
    with open(agent_input_path, 'w', encoding='utf-8') as f:
        json.dump(agent_input, f, indent=2, ensure_ascii=False)
    logger.info(f"  ✓ agent_input.json written to {agent_input_path}")
    
    # Summary
    logger.info("=" * 60)
    logger.info("ORCHESTRATOR SUMMARY")
    logger.info("=" * 60)
    
    if agent_input.get('flow_map'):
        fm = agent_input['flow_map']
        logger.info(f"  flow_class:  {fm.get('flow_class')}")
        logger.info(f"  bridge_active: {fm.get('has_bridge_activity')}")
        logger.info(f"  distribution: {fm.get('class_distribution')}")
    
    if agent_input.get('not_checked'):
        logger.info(f"  ⚠ NOT_CHECKED: {agent_input['not_checked']}")
    
    logger.info(f"  L1 seeds: {len(agent_input.get('l1_seeds', []))}")
    logger.info(f"  L2 seeds: {len(agent_input.get('l2_seeds', []))}")
    
    logger.info(f"\n[OK] Agent input: {agent_input_path}")
    logger.info("Agent reads this JSON for MUST #6 in LIQ/RUN.")
    
    # Flush handlers explicitly to avoid Windows console issues
    for handler in logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass
    
    return 0 if steps_ok else 1


if __name__ == '__main__':
    sys.exit(main())