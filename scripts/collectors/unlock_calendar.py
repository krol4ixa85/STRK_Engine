#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unlock_calendar.py — STRK Unlock/Vesting Context

STRK токеномика:
  · TGE: 20.02.2024, first cliff-unlock 15.04.2024
  · После: linear vesting ~127M/mo до апреля 2027
  · Дополнительно: quarterly cliff unlocks (некоторые категории)

Компонент даёт:
  - days_since_last_cliff
  - days_until_next_cliff (если есть в календаре)
  - daily_emission_strk (linear vesting)
  - cumulative_dilution_7d_pct (эмиссия / circ supply)
  - unlock_pressure: LOW / MEDIUM / HIGH

Логика pressure:
  - Приближение к cliff unlock (< 7 дней) = HIGH pressure
  - Sustained linear emission > 3% в квартал = MEDIUM
  - Otherwise LOW

Output: data/cache/unlock_signal.json
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
DATA_DIR = SCRIPT_DIR / 'data'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# STRK Tokenomics constants
TGE_DATE = datetime(2024, 2, 20, tzinfo=timezone.utc)
FIRST_CLIFF = datetime(2024, 4, 15, tzinfo=timezone.utc)
FINAL_UNLOCK = datetime(2027, 4, 15, tzinfo=timezone.utc)

# From STRK block skills:
# ~127M/mo linear vesting = 4.17M/day
DAILY_LINEAR_EMISSION = 4_170_000  # STRK

# Total supply
TOTAL_SUPPLY = 10_000_000_000

# Cliff unlocks (major dates from public tokenomics)
CLIFF_UNLOCKS = [
    {'date': '2024-04-15', 'amount_strk': 700_000_000, 'category': 'first_cliff'},
    {'date': '2024-10-15', 'amount_strk': 200_000_000, 'category': 'quarterly_1'},
    {'date': '2025-04-15', 'amount_strk': 500_000_000, 'category': 'annual_1'},
    {'date': '2025-10-15', 'amount_strk': 200_000_000, 'category': 'quarterly_2'},
    {'date': '2026-04-15', 'amount_strk': 500_000_000, 'category': 'annual_2'},
    {'date': '2026-10-15', 'amount_strk': 200_000_000, 'category': 'quarterly_3'},
    {'date': '2027-04-15', 'amount_strk': 500_000_000, 'category': 'annual_3_final'},
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('unlock')


def estimate_circulating_supply(date):
    """Estimate circulating supply at given date."""
    if date < FIRST_CLIFF:
        return 1_300_000_000  # initial float post-airdrop
    
    days_since_first_cliff = (date - FIRST_CLIFF).days
    linear_emission_total = days_since_first_cliff * DAILY_LINEAR_EMISSION
    
    # Add cliff unlocks
    cliff_total = 0
    for c in CLIFF_UNLOCKS:
        c_date = datetime.strptime(c['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        if c_date <= date:
            cliff_total += c['amount_strk']
    
    return 1_300_000_000 + linear_emission_total + cliff_total


def get_next_cliff(now):
    """Find next upcoming cliff unlock."""
    for c in CLIFF_UNLOCKS:
        c_date = datetime.strptime(c['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        if c_date > now:
            days_until = (c_date - now).days
            return {
                'date': c['date'],
                'amount_strk': c['amount_strk'],
                'category': c['category'],
                'days_until': days_until,
                'pct_of_current_circ': c['amount_strk'] / estimate_circulating_supply(now) * 100,
            }
    return None


def get_last_cliff(now):
    """Find most recent past cliff unlock."""
    past = []
    for c in CLIFF_UNLOCKS:
        c_date = datetime.strptime(c['date'], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        if c_date <= now:
            past.append({
                'date': c['date'],
                'amount_strk': c['amount_strk'],
                'category': c['category'],
                'days_ago': (now - c_date).days,
            })
    return past[-1] if past else None


def classify_unlock_pressure(now):
    """Classify current unlock pressure."""
    next_cliff = get_next_cliff(now)
    last_cliff = get_last_cliff(now)
    circ = estimate_circulating_supply(now)
    
    # Weekly emission
    weekly_emission = DAILY_LINEAR_EMISSION * 7
    weekly_dilution_pct = weekly_emission / circ * 100
    
    # Quarterly emission
    quarterly_emission = DAILY_LINEAR_EMISSION * 90
    quarterly_dilution_pct = quarterly_emission / circ * 100
    
    reasons = []
    
    # Approaching cliff = HIGH pressure
    if next_cliff and next_cliff['days_until'] < 7:
        pressure = 'HIGH'
        reasons.append(f"cliff unlock in {next_cliff['days_until']} days ({next_cliff['amount_strk']/1e6:.0f}M STRK, {next_cliff['pct_of_current_circ']:.1f}% of circ)")
    elif next_cliff and next_cliff['days_until'] < 30:
        pressure = 'MEDIUM'
        reasons.append(f"cliff unlock in {next_cliff['days_until']} days approaching ({next_cliff['amount_strk']/1e6:.0f}M STRK)")
    elif quarterly_dilution_pct > 5:
        pressure = 'MEDIUM'
        reasons.append(f"quarterly linear emission {quarterly_dilution_pct:.1f}% of circ")
    else:
        pressure = 'LOW'
        reasons.append(f"linear emission {weekly_dilution_pct:.2f}%/week baseline")
    
    # Post-cliff observations
    if last_cliff and last_cliff['days_ago'] < 14:
        reasons.append(f"cliff unlock {last_cliff['days_ago']}d ago ({last_cliff['amount_strk']/1e6:.0f}M) — supply digesting")
    
    return {
        'pressure': pressure,
        'reasons': reasons,
        'circulating_supply_est': int(circ),
        'daily_emission_strk': DAILY_LINEAR_EMISSION,
        'weekly_dilution_pct': round(weekly_dilution_pct, 3),
        'quarterly_dilution_pct': round(quarterly_dilution_pct, 3),
        'next_cliff': next_cliff,
        'last_cliff': last_cliff,
        'days_to_final_unlock': (FINAL_UNLOCK - now).days if now < FINAL_UNLOCK else 0,
    }


def main():
    now = datetime.now(timezone.utc)
    
    logger.info("=" * 60)
    logger.info("UNLOCK CALENDAR · STRK Vesting Context")
    logger.info("=" * 60)
    
    result = classify_unlock_pressure(now)
    
    logger.info(f"Pressure: {result['pressure']}")
    logger.info(f"Circulating supply (est): {result['circulating_supply_est']:,}")
    logger.info(f"Daily emission: {result['daily_emission_strk']:,} STRK")
    logger.info(f"Weekly dilution: {result['weekly_dilution_pct']:.2f}%")
    logger.info(f"Quarterly dilution: {result['quarterly_dilution_pct']:.2f}%")
    
    if result['next_cliff']:
        c = result['next_cliff']
        logger.info(f"\nNext cliff: {c['date']} ({c['days_until']} days)")
        logger.info(f"  Amount: {c['amount_strk']:,} STRK ({c['pct_of_current_circ']:.2f}% of circ)")
        logger.info(f"  Category: {c['category']}")
    
    if result['last_cliff']:
        c = result['last_cliff']
        logger.info(f"\nLast cliff: {c['date']} ({c['days_ago']} days ago)")
        logger.info(f"  Amount: {c['amount_strk']:,} STRK")
    
    logger.info(f"\nDays to final unlock (Apr 2027): {result['days_to_final_unlock']}")
    
    logger.info(f"\nReasons:")
    for r in result['reasons']:
        logger.info(f"  · {r}")
    
    result['as_of'] = now.isoformat()
    output_file = CACHE_DIR / 'unlock_signal.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"\nSaved: {output_file}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
