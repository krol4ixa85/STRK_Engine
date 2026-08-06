#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
event_layer.py — Combines GitHub + News + Calendar into single Event signal

This is the OFF-CHAIN layer that complements on-chain modules.
Independent from distribution shape.

Output signal:
  · POSITIVE_CATALYST: upcoming milestone + high dev activity + positive news
  · NEGATIVE_CATALYST: imminent large unlock + no dev activity + negative news
  · HIGH_VOL_WINDOW: FOMC + unlock combo, expect volatility
  · CALM: nothing major upcoming

Feeds into confluence_gate.py as another dimension of decision.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'event_layer.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('event')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def aggregate_event_signals():
    """Combine all off-chain signals."""
    now = datetime.now(timezone.utc)
    
    github = load_json('github_activity.json')
    news = load_json('news_aggregator.json')
    calendar = load_json('event_calendar.json')
    bridge = load_json('bridge_activity.json')
    cross_token = load_json('cross_token_correlation.json')
    sn_discord = load_json('starknet_discord.json')
    twitter = load_json('twitter_nitter.json')
    
    # === Scoring ===
    bullish_score = 0
    bearish_score = 0
    reasons = {'bullish': [], 'bearish': [], 'neutral': []}
    
    # ---- GitHub dev activity ----
    if github:
        gh_class = github.get('classification', {})
        gh_signal = gh_class.get('signal', 'NORMAL_ACTIVITY')
        major_releases = gh_class.get('major_releases', [])
        
        if gh_signal == 'HIGH_ACTIVITY_WITH_RELEASES':
            bullish_score += 3
            reasons['bullish'].append(f'High dev activity + {len(major_releases)} releases')
        elif gh_signal == 'HIGH_ACTIVITY':
            bullish_score += 2
            reasons['bullish'].append('High dev activity')
        elif gh_signal == 'VERY_LOW':
            bearish_score += 2
            reasons['bearish'].append('Very low dev activity')
        elif gh_signal == 'LOW_ACTIVITY':
            bearish_score += 1
            reasons['bearish'].append('Dev slowdown')
    
    # ---- News sentiment ----
    if news:
        news_signal = news.get('overall_signal', 'NEUTRAL')
        breakdown = news.get('sentiment_breakdown', {})
        
        if news_signal == 'CATALYST_EVENTS':
            bullish_score += 2
            reasons['bullish'].append(f'{breakdown.get("catalyst", 0)} catalyst events in news')
        elif news_signal == 'BULLISH_NEWS':
            bullish_score += 2
            reasons['bullish'].append('Positive news dominant')
        elif news_signal == 'BEARISH_NEWS':
            bearish_score += 2
            reasons['bearish'].append('Negative news dominant')
        elif news_signal == 'NO_COVERAGE':
            reasons['neutral'].append('Low STRK coverage in mainstream')
    
    # ---- Event calendar ----
    if calendar:
        signals_list = calendar.get('signals', [])
        supply_30d = calendar.get('supply_added_30d', 0)
        supply_60d = calendar.get('supply_added_60d', 0)
        days_to_unlock = calendar.get('days_to_next_unlock', 999)
        
        if days_to_unlock <= 7 and supply_30d > 100_000_000:
            bearish_score += 3
            reasons['bearish'].append(f'Imminent unlock ({days_to_unlock}d): {supply_30d/1e6:.0f}M STRK')
        elif days_to_unlock <= 14:
            bearish_score += 1
            reasons['bearish'].append(f'Unlock approaching ({days_to_unlock}d)')
        elif days_to_unlock > 30 and supply_60d < 150_000_000:
            bullish_score += 1
            reasons['bullish'].append('No immediate unlock pressure')
        
        if supply_30d > 250_000_000:
            bearish_score += 2
            reasons['bearish'].append(f'{supply_30d/1e6:.0f}M unlocking in 30d')
        
        milestones = [s for s in signals_list if s['type'] == 'MILESTONE']
        for m in milestones:
            if 'POSITIVE' in m.get('message', ''):
                bullish_score += 2
                reasons['bullish'].append(m['message'][:60])
            elif 'NEGATIVE' in m.get('message', ''):
                bearish_score += 2
                reasons['bearish'].append(m['message'][:60])
    
    # ---- NEW: Bridge activity (L1↔L2) ----
    if bridge:
        br_class = bridge.get('classification', {})
        br_signal = br_class.get('signal', 'NORMAL_ACTIVITY')
        
        if br_signal == 'BULLISH_ADOPTION':
            bullish_score += 3
            reasons['bullish'].append(f'Bridge inflows: {br_class.get("interpretation", "")[:80]}')
        elif br_signal == 'BEARISH_EXODUS':
            bearish_score += 3
            reasons['bearish'].append(f'Bridge exodus: {br_class.get("interpretation", "")[:80]}')
        elif br_signal == 'HIGH_ACTIVITY':
            bullish_score += 1
            reasons['bullish'].append('High bridge two-way activity — pre-catalyst')
        elif br_signal == 'LOW_ACTIVITY':
            bearish_score += 1
            reasons['bearish'].append('Very low bridge activity — no user interest')
    
    # ---- NEW: Cross-token correlation (STRK vs L2 peers) ----
    if cross_token:
        ct_signal = cross_token.get('signal', 'NEUTRAL')
        alpha_7d = cross_token.get('strk_alpha', {}).get('alpha_7d_pct', 0)
        
        if ct_signal == 'STRK_OUTPERFORMING':
            bullish_score += 3
            reasons['bullish'].append(f'STRK alpha +{alpha_7d:.1f}% vs L2 sector — rotation IN')
        elif ct_signal == 'STRK_UNDERPERFORMING':
            bearish_score += 3
            reasons['bearish'].append(f'STRK alpha {alpha_7d:.1f}% vs L2 sector — rotation OUT')
        elif ct_signal == 'SECTOR_MOMENTUM':
            bullish_score += 2
            reasons['bullish'].append(f'L2 sector +{cross_token.get("sector_averages", {}).get("sector_7d_pct", 0):.1f}% — beta play')
        elif ct_signal == 'SECTOR_WEAKNESS':
            bearish_score += 2
            reasons['bearish'].append('L2 sector weakness — macro headwind')
    
    # ---- NEW: Starknet Discord announcements ----
    if sn_discord:
        sd_signal = sn_discord.get('signal', 'NORMAL')
        
        if sd_signal == 'CATALYST_DETECTED':
            bullish_score += 3
            reasons['bullish'].append(f'Major announcement + partnerships in Discord (7d)')
        elif sd_signal == 'MAJOR_UPDATE':
            bullish_score += 2
            reasons['bullish'].append('Major update announced in Discord')
        elif sd_signal == 'PARTNERSHIP_FLOW':
            bullish_score += 2
            reasons['bullish'].append('Multiple partnerships announced')
        elif sd_signal == 'CONCERNING_SILENCE':
            bearish_score += 2
            reasons['bearish'].append('14+ days silence in official Discord')
        elif sd_signal in ('NO_TOKEN', 'NO_CHANNEL'):
            reasons['neutral'].append('Discord monitor not configured yet')
    
    # ---- NEW: Twitter/X (via Nitter) ----
    if twitter:
        tw_signal = twitter.get('signal', 'NORMAL')
        
        if tw_signal == 'CATALYST_TWEETS':
            bullish_score += 2
            reasons['bullish'].append(f'Catalyst tweets from team (7d)')
        elif tw_signal == 'POSITIVE_MOMENTUM':
            bullish_score += 1
            reasons['bullish'].append('Positive Twitter tone from team')
        elif tw_signal == 'NEGATIVE_TONE':
            bearish_score += 2
            reasons['bearish'].append('Negative Twitter tone from team')
        elif tw_signal == 'SILENCE':
            reasons['neutral'].append('Twitter/Nitter unavailable')
    
    # === Final classification ===
    if bullish_score >= 6 and bearish_score < 4:
        signal = 'POSITIVE_CATALYST'
        summary = f'Off-chain strongly bullish ({bullish_score} vs {bearish_score})'
    elif bearish_score >= 6 and bullish_score < 4:
        signal = 'NEGATIVE_CATALYST'
        summary = f'Off-chain strongly bearish ({bearish_score} vs {bullish_score})'
    elif bullish_score >= 4 and bearish_score >= 4:
        signal = 'HIGH_VOL_WINDOW'
        summary = f'Mixed catalysts, expect volatility ({bullish_score} bull, {bearish_score} bear)'
    elif bullish_score > bearish_score:
        signal = 'SLIGHT_BULLISH'
        summary = f'Slight bullish tilt ({bullish_score} vs {bearish_score})'
    elif bearish_score > bullish_score:
        signal = 'SLIGHT_BEARISH'
        summary = f'Slight bearish tilt ({bearish_score} vs {bullish_score})'
    else:
        signal = 'CALM'
        summary = 'No strong catalysts on horizon'
    
    return {
        'as_of': now.isoformat(),
        'signal': signal,
        'summary': summary,
        'bullish_score': bullish_score,
        'bearish_score': bearish_score,
        'reasons': reasons,
        'components': {
            'github_signal': github.get('classification', {}).get('signal') if github else None,
            'news_signal': news.get('overall_signal') if news else None,
            'calendar_signal': calendar.get('overall_signal') if calendar else None,
            'bridge_signal': bridge.get('classification', {}).get('signal') if bridge else None,
            'cross_token_signal': cross_token.get('signal') if cross_token else None,
            'discord_signal': sn_discord.get('signal') if sn_discord else None,
            'twitter_signal': twitter.get('signal') if twitter else None,
        }
    }


def main():
    logger.info("=" * 60)
    logger.info("EVENT LAYER · GitHub + News + Calendar aggregate")
    logger.info("=" * 60)
    
    result = aggregate_event_signals()
    
    logger.info(f"\nSignal: {result['signal']}")
    logger.info(f"Summary: {result['summary']}")
    logger.info(f"Bullish: {result['bullish_score']} · Bearish: {result['bearish_score']}")
    
    logger.info(f"\nComponents:")
    for k, v in result['components'].items():
        logger.info(f"  {k}: {v}")
    
    logger.info(f"\nBullish reasons:")
    for r in result['reasons']['bullish']:
        logger.info(f"  ✓ {r}")
    
    logger.info(f"\nBearish reasons:")
    for r in result['reasons']['bearish']:
        logger.info(f"  ✗ {r}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
