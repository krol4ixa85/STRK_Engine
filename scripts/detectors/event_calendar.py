#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_calendar.py — STRK unlock schedule + Starknet milestones

Track upcoming events that could impact price:
  · Token unlocks (STRK vesting schedule)
  · Milestone dates (mainnet upgrades, testnet launches)
  · Macro events (Fed meetings, BTC halvings)
  · Regulatory dates (SEC decisions, ETF milestones)

Priority signal based on:
  · Days until event
  · Impact magnitude (M supply / days to unlock)
  · Event type (unlock = bearish, upgrade = potentially bullish)

STRK unlock schedule (from tokenomics):
  · TGE Feb 2024: 728M initially
  · Monthly linear unlocks starting Apr 15 2024
  · ~64M/month for team+investors
  · Community rewards: gradual
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'event_calendar.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('cal')


# STRK unlock schedule (approximate)
# Format: date, category, amount_strk, notes
STRK_UNLOCKS = [
    # 2026 remaining
    {'date': '2026-08-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Monthly vesting'},
    {'date': '2026-09-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Monthly vesting'},
    {'date': '2026-10-15', 'amount': 200_000_000, 'category': 'quarterly_large', 'note': 'Q4 large tranche'},
    {'date': '2026-11-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Monthly vesting'},
    {'date': '2026-12-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Monthly vesting'},
    
    # 2027
    {'date': '2027-01-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Monthly vesting'},
    {'date': '2027-02-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Anniversary of TGE'},
    {'date': '2027-03-15', 'amount': 64_000_000, 'category': 'monthly_vesting', 'note': 'Monthly vesting'},
    {'date': '2027-04-15', 'amount': 200_000_000, 'category': 'quarterly_large', 'note': 'Q1 large tranche'},
]

# Starknet milestones and roadmap events
STARKNET_MILESTONES = [
    # These are approximate - would need to update with real dates
    {'date': '2026-08-20', 'event': 'Starknet upgrade v0.14.0', 'impact': 'POSITIVE', 
     'note': 'Native STRK gas, prevalidated txs'},
    {'date': '2026-09-01', 'event': 'Starknet Season 3 quest launch', 'impact': 'POSITIVE',
     'note': 'New incentive program for users'},
    {'date': '2026-10-01', 'event': 'Starknet Bitcoin integration testnet', 'impact': 'POSITIVE',
     'note': 'BTC settlement layer testing'},
    {'date': '2026-11-15', 'event': 'Provisions Round 3 (airdrop)', 'impact': 'MIXED',
     'note': 'Retroactive rewards, positive for users, may increase supply'},
]

# Macro events (approximate FOMC dates 2026)
MACRO_EVENTS = [
    {'date': '2026-09-16', 'event': 'FOMC Sept meeting', 'impact': 'HIGH_VOL',
     'note': 'Rate decision, affects all crypto'},
    {'date': '2026-11-04', 'event': 'FOMC Nov meeting', 'impact': 'HIGH_VOL', 'note': 'Rate decision'},
    {'date': '2026-12-16', 'event': 'FOMC Dec meeting', 'impact': 'HIGH_VOL', 'note': 'Rate decision'},
]


def days_until(date_str):
    """Return days until date_str (YYYY-MM-DD)."""
    event_dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = event_dt - now
    return delta.days


def classify_urgency(days):
    if days < 0: return 'PAST'
    if days <= 3: return 'IMMINENT'
    if days <= 14: return 'NEAR'
    if days <= 30: return 'UPCOMING'
    if days <= 90: return 'MID_TERM'
    return 'FUTURE'


def analyze_calendar():
    """Analyze all upcoming events."""
    now = datetime.now(timezone.utc)
    
    upcoming_unlocks = []
    for u in STRK_UNLOCKS:
        days = days_until(u['date'])
        if days >= -1:  # include today
            upcoming_unlocks.append({
                **u,
                'days_until': days,
                'urgency': classify_urgency(days),
            })
    upcoming_unlocks.sort(key=lambda x: x['days_until'])
    
    upcoming_milestones = []
    for m in STARKNET_MILESTONES:
        days = days_until(m['date'])
        if days >= -1:
            upcoming_milestones.append({
                **m,
                'days_until': days,
                'urgency': classify_urgency(days),
            })
    upcoming_milestones.sort(key=lambda x: x['days_until'])
    
    upcoming_macro = []
    for mc in MACRO_EVENTS:
        days = days_until(mc['date'])
        if days >= -1:
            upcoming_macro.append({
                **mc,
                'days_until': days,
                'urgency': classify_urgency(days),
            })
    upcoming_macro.sort(key=lambda x: x['days_until'])
    
    # === Signal analysis ===
    # Next unlock
    next_unlock = upcoming_unlocks[0] if upcoming_unlocks else None
    days_to_unlock = next_unlock['days_until'] if next_unlock else 999
    unlock_amount = next_unlock['amount'] if next_unlock else 0
    
    # Total supply added in next 30 days
    supply_added_30d = sum(u['amount'] for u in upcoming_unlocks if u['days_until'] <= 30)
    supply_added_60d = sum(u['amount'] for u in upcoming_unlocks if u['days_until'] <= 60)
    
    # Impact classification
    signals = []
    
    if days_to_unlock <= 7:
        signals.append({
            'type': 'IMMINENT_UNLOCK',
            'severity': 'HIGH' if unlock_amount > 100_000_000 else 'MEDIUM',
            'message': f'Unlock in {days_to_unlock}d: {unlock_amount/1e6:.0f}M STRK ({next_unlock["note"]})',
            'action_hint': 'Expect volatility. Whales may sell before/after.',
        })
    elif days_to_unlock <= 21:
        signals.append({
            'type': 'UNLOCK_APPROACHING',
            'severity': 'MEDIUM' if unlock_amount > 100_000_000 else 'LOW',
            'message': f'Unlock in {days_to_unlock}d: {unlock_amount/1e6:.0f}M STRK',
            'action_hint': 'Watch for pre-unlock selling pressure.',
        })
    
    if supply_added_30d > 250_000_000:
        signals.append({
            'type': 'HIGH_SUPPLY_INCOMING',
            'severity': 'HIGH',
            'message': f'{supply_added_30d/1e6:.0f}M STRK unlocking in next 30d',
            'action_hint': 'Significant supply pressure. Consider reducing exposure.',
        })
    
    # Milestones
    imminent_milestones = [m for m in upcoming_milestones if m['days_until'] <= 21]
    for m in imminent_milestones:
        signals.append({
            'type': 'MILESTONE',
            'severity': 'MEDIUM',
            'message': f'{m["event"]} in {m["days_until"]}d ({m["impact"]})',
            'action_hint': m['note'],
        })
    
    # Macro
    imminent_macro = [m for m in upcoming_macro if m['days_until'] <= 14]
    for m in imminent_macro:
        signals.append({
            'type': 'MACRO_EVENT',
            'severity': 'MEDIUM',
            'message': f'{m["event"]} in {m["days_until"]}d',
            'action_hint': m['note'],
        })
    
    # Overall calendar signal
    high_severity = sum(1 for s in signals if s['severity'] == 'HIGH')
    if high_severity >= 2:
        overall = 'HIGH_VOLATILITY_WINDOW'
    elif high_severity >= 1:
        overall = 'CATALYST_APPROACHING'
    elif signals:
        overall = 'EVENTS_UPCOMING'
    else:
        overall = 'CALM_CALENDAR'
    
    return {
        'as_of': now.isoformat(),
        'overall_signal': overall,
        'signals': signals,
        'next_unlock': next_unlock,
        'days_to_next_unlock': days_to_unlock,
        'supply_added_30d': round(supply_added_30d, 2),
        'supply_added_60d': round(supply_added_60d, 2),
        'upcoming_unlocks': upcoming_unlocks[:5],
        'upcoming_milestones': upcoming_milestones[:5],
        'upcoming_macro': upcoming_macro[:5],
    }


def main():
    logger.info("=" * 60)
    logger.info("EVENT CALENDAR · STRK unlocks + Starknet + Macro")
    logger.info("=" * 60)
    
    analysis = analyze_calendar()
    
    logger.info(f"\nOverall: {analysis['overall_signal']}")
    logger.info(f"Next unlock: {analysis['next_unlock']}")
    logger.info(f"Supply added next 30d: {analysis['supply_added_30d']/1e6:.0f}M STRK")
    logger.info(f"Supply added next 60d: {analysis['supply_added_60d']/1e6:.0f}M STRK")
    
    logger.info(f"\n=== ACTIVE SIGNALS ({len(analysis['signals'])}) ===")
    for s in analysis['signals']:
        logger.info(f"  · [{s['severity']}] {s['type']}: {s['message']}")
        logger.info(f"    → {s['action_hint']}")
    
    logger.info(f"\n=== UPCOMING UNLOCKS ===")
    for u in analysis['upcoming_unlocks'][:3]:
        logger.info(f"  {u['date']} ({u['days_until']}d) · {u['amount']/1e6:.0f}M STRK · {u['note']}")
    
    logger.info(f"\n=== UPCOMING MILESTONES ===")
    for m in analysis['upcoming_milestones'][:3]:
        logger.info(f"  {m['date']} ({m['days_until']}d) · {m['event']} · {m['impact']}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
