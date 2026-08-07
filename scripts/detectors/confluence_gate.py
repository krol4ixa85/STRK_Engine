#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
confluence_gate.py — HIGH precision confluence gating

Philosophy:
  · On-chain distribution shape hit ceiling at 66.7%
  · Technical momentum can't be backtested (OKX history limit)
  · Solution: SIGNAL HIGH only when multiple independent signals agree
  · Trade recall for precision

HIGH confidence RALLY (all must be true):
  · v2 on-chain: ACCUMULATION or NEUTRAL
  · Technical: 3d slope > +3% AND accelerating
  · Volume: 3d vol > 1.3x of 30d
  · Funding: short-crowded (contrarian bullish)
  · CEX flow: NOT strong distribution

HIGH confidence CRASH (all must be true):
  · v2 on-chain: DISTRIBUTION
  · Technical: 3d slope < -3% OR overhead resistance
  · Volume: expanding on down candles
  · Funding: long-crowded OR neutral
  · CEX flow: MILD or STRONG distribution

Anything else → LOW confidence · STAY FLAT

This should give:
  · Precision when HIGH: 80%+ (fewer signals but reliable)
  · Recall: 30-40% (only clear setups trigger)
  · False positives: minimized

VOTER_WIRE_v1 additions (see §0.28 in MASTER):
  · liquidity_shift          → RALLY bonus if LOCKING_UP/LP_ADDING/etc
                                CRASH bonus if EXTRACTING/LP_REMOVING/etc
  · bridge_activity          → RALLY if NET_INFLOW, CRASH if NET_OUTFLOW
  · cross_token_correlation  → RALLY if alpha_7d >= +5%
                                CRASH if alpha_7d <= -5%
  All thresholds HYPOTHESIS. Parallel shadow_votes.jsonl accumulation
  для калибровки через ~4 days (15 closed forecasts на окне 72h).
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'confluence_gate.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('gate')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def evaluate_confluence():
    """Check all signals for HIGH confidence trigger."""
    now = datetime.now(timezone.utc)
    
    # Load inputs
    wyckoff = load_json('wyckoff_phase.json') or {}
    tech = load_json('technical_momentum.json') or {}
    funding = load_json('funding_signal.json') or {}
    cex_flow = load_json('cex_flow.json') or {}
    event_layer = load_json('event_layer.json') or {}
    
    # Wyckoff signal
    phase = wyckoff.get('phase', 'UNKNOWN')
    wyckoff_conf = wyckoff.get('confidence', 'LOW')
    
    # Technical signal
    tech_signal = tech.get('signal', 'NEUTRAL')
    tech_conf = tech.get('confidence', 'LOW')
    tech_features = tech.get('features', {})
    
    slope_3d = tech_features.get('slope_3d_pct', 0)
    slope_accel = tech_features.get('slope_accel_pct', 0)
    vol_ratio_3d = tech_features.get('vol_ratio_3d_vs_30d', 1)
    rsi = tech_features.get('rsi', 50)
    pct_from_high = tech_features.get('pct_from_high', 0)
    pct_from_low = tech_features.get('pct_from_low', 0)
    
    # Funding regime
    fund_metrics = funding.get('funding_metrics', {})
    fund_regime = fund_metrics.get('regime', 'UNKNOWN')
    short_crowded = fund_metrics.get('short_crowded', False)
    long_crowded = fund_metrics.get('long_crowded', False)
    
    # CEX flow
    cex_signal = cex_flow.get('signal', 'NEUTRAL')
    cex_conf = cex_flow.get('confidence', 'LOW')
    
    # Event layer (off-chain events integrated)
    event_signal = 'CALM'
    event_bullish = 0
    event_bearish = 0
    if event_layer:
        event_signal = event_layer.get('signal', 'CALM')
        event_bullish = event_layer.get('bullish_score', 0)
        event_bearish = event_layer.get('bearish_score', 0)
    
    # ==========================================================
    # HIGH CONFIDENCE CHECKS
    # ==========================================================
    
    # RALLY HIGH: strict confluence (now 6 checks + event layer bonus)
    rally_checks = {
        'on_chain_ok': phase in ('ACCUMULATION', 'MARKUP') or phase == 'UNKNOWN',
        'price_up_3d': slope_3d > 3,
        'accelerating': slope_accel > 3,
        'vol_expanding': vol_ratio_3d > 1.3,
        'not_distributing_cex': cex_signal not in ('STRONG_DISTRIBUTION',),
        'events_supportive': event_signal in ('POSITIVE_CATALYST', 'SLIGHT_BULLISH', 'CALM'),
    }
    rally_score = sum(1 for v in rally_checks.values() if v)
    
    # ==========================================================
    # VOTER_WIRE_v1 · orphan collectors → live bonus checks
    # ==========================================================
    # Загрузка 3 orphan-модулей, которые до этого не голосовали.
    # Пороги HYPOTHESIS (см. §0.28 MASTER). Параллельно эти voter'ы
    # пишутся в shadow_votes.jsonl — через 15+ closed forecasts
    # можно будет отключить bonus если precision < 55%.
    
    liq_shift = load_json('liquidity_shift.json') or {}
    bridge_act = load_json('bridge_activity.json') or {}
    cross_tok = load_json('cross_token_correlation.json') or {}
    
    liq_direction = liq_shift.get('overall_direction', 'UNKNOWN')
    _liq_rally_values = ('LOCKING_UP', 'ROTATING_TO_STAKE', 'STAKE_INFLOW', 'LP_ADDING')
    _liq_crash_values = ('EXTRACTING', 'ROTATING_TO_DEX', 'STAKE_OUTFLOW', 'LP_REMOVING')
    
    bridge_sig = ((bridge_act.get('classification') or {}).get('signal')
                  or bridge_act.get('signal') or 'NEUTRAL')
    _bridge_rally_values = ('NET_INFLOW', 'STRONG_INFLOW', 'HIGH_ACTIVITY_INFLOW')
    _bridge_crash_values = ('NET_OUTFLOW', 'STRONG_OUTFLOW', 'DISTRIBUTION')
    
    strk_alpha_7d = ((cross_tok.get('strk_alpha') or {}).get('alpha_7d_pct') or 0)
    
    # Apply rally bonuses
    if liq_direction in _liq_rally_values:
        rally_score += 1
        rally_checks['liquidity_shift_bullish'] = True
    
    if bridge_sig in _bridge_rally_values:
        rally_score += 1
        rally_checks['bridge_inflow_bullish'] = True
    
    if strk_alpha_7d >= 5.0:
        rally_score += 1
        rally_checks['strk_outperforming_l2_sector'] = True
    
    # Bonus if capitulation setup + shorts crowded
    if pct_from_high < -20 and pct_from_low > 5 and short_crowded:
        rally_score += 1
        rally_checks['post_capitulation_squeeze'] = True
    
    # Bonus if event layer strongly bullish
    if event_signal == 'POSITIVE_CATALYST':
        rally_score += 1
        rally_checks['strong_off_chain_bull'] = True
    
    # CRASH HIGH: strict confluence
    crash_checks = {
        'on_chain_distribution': phase in ('DISTRIBUTION', 'MARKDOWN'),
        'price_down_3d': slope_3d < -3,
        'not_bouncing': slope_accel < 3,
        'cex_distribution': cex_signal in ('STRONG_DISTRIBUTION', 'MILD_DISTRIBUTION'),
        'not_extreme_short': not short_crowded or long_crowded,
        'events_bearish': event_signal in ('NEGATIVE_CATALYST', 'SLIGHT_BEARISH', 'HIGH_VOL_WINDOW'),
    }
    crash_score = sum(1 for v in crash_checks.values() if v)
    
    # ==========================================================
    # VOTER_WIRE_v1 · crash side of orphan bonuses
    # ==========================================================
    
    if liq_direction in _liq_crash_values:
        crash_score += 1
        crash_checks['liquidity_shift_bearish'] = True
    
    if bridge_sig in _bridge_crash_values:
        crash_score += 1
        crash_checks['bridge_outflow_bearish'] = True
    
    if strk_alpha_7d <= -5.0:
        crash_score += 1
        crash_checks['strk_underperforming_l2_sector'] = True
    
    # Bonus if event layer strongly bearish
    if event_signal == 'NEGATIVE_CATALYST':
        crash_score += 1
        crash_checks['strong_off_chain_bear'] = True
    
    # ==========================================================
    # DECISION (updated thresholds for 6+3 checks + bonuses)
    # ==========================================================
    
    if rally_score >= 5:
        signal = 'RALLY_HIGH_CONFLUENCE'
        confidence = 'HIGH'
        summary = f'STRONG RALLY SETUP - {rally_score} independent checks agree'
        checks_used = rally_checks
    elif crash_score >= 5:
        signal = 'CRASH_HIGH_CONFLUENCE'
        confidence = 'HIGH'
        summary = f'STRONG CRASH SETUP - {crash_score} independent checks agree'
        checks_used = crash_checks
    elif rally_score >= 4:
        signal = 'RALLY_MEDIUM'
        confidence = 'MEDIUM'
        summary = f'Partial rally signals ({rally_score}/9+)'
        checks_used = rally_checks
    elif crash_score >= 4:
        signal = 'CRASH_MEDIUM'
        confidence = 'MEDIUM'
        summary = f'Partial crash signals ({crash_score}/9+)'
        checks_used = crash_checks
    else:
        signal = 'NO_SIGNAL'
        confidence = 'LOW'
        summary = 'No clear confluence - stay flat'
        checks_used = {'rally_score': rally_score, 'crash_score': crash_score}
    
    # ==========================================================
    # ACTIONABLE OUTPUT
    # ==========================================================
    
    action = 'STAY FLAT'
    if confidence == 'HIGH':
        if 'RALLY' in signal:
            action = f'Consider LONG on break above ${tech_features.get("high_14d", 0) * 0.99:.4f} with stop ${tech_features.get("low_14d", 0):.4f}'
        else:
            action = f'Consider SHORT on break below ${tech_features.get("low_14d", 0):.4f} or REDUCE existing longs'
    
    return {
        'as_of': now.isoformat(),
        'signal': signal,
        'confidence': confidence,
        'summary': summary,
        'action': action,
        'rally_score': rally_score,
        'crash_score': crash_score,
        'checks': checks_used,
        'inputs_used': {
            'wyckoff_phase': phase,
            'technical_signal': tech_signal,
            'slope_3d_pct': slope_3d,
            'slope_accel_pct': slope_accel,
            'vol_ratio_3d': vol_ratio_3d,
            'rsi': rsi,
            'pct_from_high': pct_from_high,
            'pct_from_low': pct_from_low,
            'short_crowded': short_crowded,
            'long_crowded': long_crowded,
            'cex_signal': cex_signal,
            'event_signal': event_signal,
            'event_bullish': event_bullish,
            'event_bearish': event_bearish,
            # voter_wire_v1 new inputs
            'liquidity_shift_direction': liq_direction,
            'bridge_activity_signal': bridge_sig,
            'cross_token_alpha_7d_pct': strk_alpha_7d,
        },
        'voter_wire_v1': {
            'status': 'LIVE_BONUS',
            'note': 'Пороги HYPOTHESIS. Параллельно пишутся в shadow_votes.jsonl.',
            'bonuses_active': {
                'liquidity_shift': liq_direction != 'UNKNOWN',
                'bridge_activity': bridge_sig != 'NEUTRAL',
                'cross_token': abs(strk_alpha_7d) >= 5.0,
            },
            'thresholds': {
                'liquidity_shift_rally_values': list(_liq_rally_values),
                'liquidity_shift_crash_values': list(_liq_crash_values),
                'bridge_rally_values': list(_bridge_rally_values),
                'bridge_crash_values': list(_bridge_crash_values),
                'cross_token_rally_pct_gte': 5.0,
                'cross_token_crash_pct_lte': -5.0,
            },
        },
        'philosophy': 'HIGH signal = 5+ checks pass across ON-CHAIN + TECHNICAL + OFF-CHAIN + LIQUIDITY. Trades recall for precision.',
    }


def main():
    logger.info("=" * 60)
    logger.info("CONFLUENCE GATE · High-precision multi-signal + voter_wire_v1")
    logger.info("=" * 60)
    
    result = evaluate_confluence()
    
    logger.info(f"\nSignal: {result['signal']} · {result['confidence']}")
    logger.info(f"Rally score: {result['rally_score']}/9+")
    logger.info(f"Crash score: {result['crash_score']}/9+")
    logger.info(f"\nSummary: {result['summary']}")
    logger.info(f"Action: {result['action']}")
    logger.info(f"\nInputs:")
    for k, v in result['inputs_used'].items():
        logger.info(f"  {k}: {v}")
    
    logger.info(f"\nVoter Wire v1 bonuses active:")
    for k, v in result['voter_wire_v1']['bonuses_active'].items():
        logger.info(f"  {k}: {v}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
