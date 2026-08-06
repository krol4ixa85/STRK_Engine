#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_digest.py — Полный Wyckoff phase digest в Telegram"""

import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
SEEDS_FILE = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('digest')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def count_wallets():
    if not SEEDS_FILE.exists():
        return 0, {}
    with open(SEEDS_FILE, 'r', encoding='utf-8') as f:
        seeds = json.load(f)
    total = 0
    by_cat = {}
    SKIP = {'_meta', '_phantoms'}
    for cat, data in seeds.items():
        if cat in SKIP or not isinstance(data, dict):
            continue
        count = sum(1 for k, v in data.items()
                    if not k.startswith('_') and isinstance(v, dict))
        if count > 0:
            by_cat[cat] = count
            total += count
    return total, by_cat


def get_recent_decisions(hours_back=6):
    log = load_json('decision_log.json')
    if not log:
        return [], [], []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    accepted, rejected = [], []
    queued = log.get('queued', [])
    for d in log.get('decisions', []):
        try:
            ts = datetime.fromisoformat(d['ts'].replace('Z', '+00:00'))
            if ts >= cutoff:
                if d['decision'] == 'ACCEPT':
                    accepted.append(d)
                elif d['decision'] == 'REJECT':
                    rejected.append(d)
        except (ValueError, KeyError):
            continue
    return accepted, rejected, queued


def get_whale_events_24h():
    state = load_json('whale_monitor_state.json')
    if not state:
        return 0, 0
    now_ts = datetime.now(timezone.utc).timestamp()
    day_ago = now_ts - 86400
    recent = [a for a in state.get('alert_history', []) if a.get('ts', 0) > day_ago]
    total_amt = sum(a.get('amount', 0) for a in recent)
    return len(recent), total_amt


PHASE_EMOJI = {'ACCUMULATION': '🌱', 'MARKUP': '🚀', 'DISTRIBUTION': '🔥', 'MARKDOWN': '💀'}
PHASE_SHORT_DESC = {
    'ACCUMULATION': 'Smart money buying quietly',
    'MARKUP': 'Trend is UP, retail joining',
    'DISTRIBUTION': 'Smart money selling to retail',
    'MARKDOWN': 'Downtrend, panic selling',
}
CONFIDENCE_EMOJI = {'HIGH': '🟢', 'MEDIUM': '🟡', 'LOW': '⚪'}


def format_digest():
    now = datetime.now(timezone.utc)
    
    wyckoff = load_json('wyckoff_phase.json')
    composite = load_json('composite_signal_v2.json')
    cross_window = load_json('cross_window_pattern.json')
    funding = load_json('funding_signal.json')
    confluence = load_json('confluence_gate.json')  # NEW
    technical = load_json('technical_momentum.json')  # NEW
    
    accepted, rejected, queued = get_recent_decisions(hours_back=6)
    whale_count, whale_amt = get_whale_events_24h()
    wallet_total, wallet_by_cat = count_wallets()
    
    if not wyckoff:
        return format_fallback_digest(composite, accepted, rejected, whale_count, wallet_total)
    
    phase = wyckoff['phase']
    sub_phase = wyckoff.get('sub_phase', '')
    confidence = wyckoff['confidence']
    tech = wyckoff.get('technical', {})
    triggers = wyckoff.get('triggers', {})
    layman = wyckoff.get('layman_explanation', '')
    price = tech.get('price_now', 0)
    
    # === Header ===
    text = f"<b>🤖 STRK-GUARD · Phase Analysis</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
    
    # === CONFLUENCE GATE (NEW - top priority decision layer) ===
    if confluence:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚡ CONFLUENCE GATE (multi-signal)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        signal = confluence.get('signal', 'NO_SIGNAL')
        conf = confluence.get('confidence', 'LOW')
        rally_s = confluence.get('rally_score', 0)
        crash_s = confluence.get('crash_score', 0)
        
        # Header emoji
        if conf == 'HIGH':
            if 'RALLY' in signal:
                emoji = '🟢🟢'
            elif 'CRASH' in signal:
                emoji = '🔴🔴'
            else:
                emoji = '⚪'
        else:
            emoji = '🟡' if conf == 'MEDIUM' else '⚪'
        
        text += f"<b>{emoji} {signal}</b>\n"
        text += f"Confidence: <b>{conf}</b>\n"
        text += f"Rally checks: {rally_s}/5-6 pass\n"
        text += f"Crash checks: {crash_s}/5 pass\n\n"
        text += f"<i>{confluence.get('summary', '')}</i>\n\n"
        
        # Show all checks
        checks = confluence.get('checks', {})
        if checks and isinstance(checks, dict) and 'rally_score' not in checks:
            text += "<b>Signals:</b>\n"
            for k, v in checks.items():
                marker = "✅" if v else "✗"
                text += f"  {marker} {k}\n"
            text += "\n"
        
        if conf == 'HIGH':
            text += f"<b>Action:</b> {confluence.get('action', 'Stay flat')}\n"
        else:
            text += f"<b>Action:</b> STAY FLAT (waiting for stronger confluence)\n"
        text += "\n"
    
    # === INTERPRETATION LAYER (NEW - narrative synthesis) ===
    interpretation = load_json('interpretation.json')
    if interpretation and interpretation.get('interpretation', {}).get('primary'):
        interp = interpretation['interpretation']
        primary = interp['primary']
        
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🧠 INTERPRETATION</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        # Direction emoji
        direction = primary.get('direction', 'NEUTRAL')
        if 'BULLISH' in direction:
            dir_emoji = '🟢'
        elif 'BEARISH' in direction:
            dir_emoji = '🔴'
        else:
            dir_emoji = '⚪'
        
        text += f"<b>{dir_emoji} {primary['label']}</b>\n"
        text += f"Confidence: <b>{primary['confidence']}%</b> · Direction: {primary['direction']}\n\n"
        
        text += f"<b>Hypothesis:</b>\n<i>{primary['hypothesis']}</i>\n\n"
        text += f"<b>Narrative:</b>\n<i>{primary['narrative']}</i>\n\n"
        text += f"<b>Position hint:</b>\n<i>{primary['position_hint']}</i>\n\n"
        
        text += f"<b>Watch for:</b>\n"
        for t in primary.get('triggers_watch', [])[:3]:
            text += f"  · {t}\n"
        
        if primary.get('invalidation'):
            text += f"\n<b>Invalidation:</b>\n<i>{primary['invalidation']}</i>\n"
        
        # Secondary if present
        secondary = interp.get('secondary')
        if secondary:
            text += f"\n<b>Also detected:</b> {secondary['label']} ({secondary['confidence']}%)\n"
        
        text += "\n"
    
    # === WHALE INTERPRETATION (NEW - top 3 events classified) ===
    whale_auto = load_json('whale_auto_analysis.json')
    if whale_auto and whale_auto.get('events'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🐋 WHALE INTERPRETATION (24h)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        for i, ev in enumerate(whale_auto['events'], 1):
            sentiment_emoji = "🔴" if ev['sentiment'] == 'BEARISH' else "🟢" if ev['sentiment'] == 'BULLISH' else "⚪"
            text += f"{i}. {sentiment_emoji} <b>{ev['amount_M_strk']}M STRK</b> · {ev['classification']}\n"
            text += f"   {ev['from']} → {ev['to']}\n"
            text += f"   <i>{ev['meaning']}</i>\n\n"
        
        cohort_read = whale_auto.get('cohort_read', {})
        text += f"<b>Cohort read:</b> <i>{cohort_read.get('summary', '')}</i>\n\n"
    
    # === COHORT TRACKER (NEW - 4 groups) ===
    cohort_data = load_json('cohort_tracker.json')
    if cohort_data and cohort_data.get('cohorts'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>👥 COHORT BEHAVIOR (24h)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        text += f"<b>Signal:</b> {cohort_data.get('aggregate_signal', '?')}\n\n"
        
        for cohort_name, data in cohort_data['cohorts'].items():
            if data.get('address_count', 0) == 0:
                continue
            behavior = data.get('behavior', '?')
            net = data.get('net_flow_strk', 0)
            active = data.get('active_addresses', 0)
            
            beh_emoji = '🟢' if 'ACCUMULATING' in behavior else '🔴' if 'DISTRIBUTING' in behavior else '⚪'
            text += f"{beh_emoji} <b>{cohort_name}</b>: {behavior}\n"
            text += f"   Net 24h: {net/1e6:+.2f}M STRK · {active} active\n"
        
        text += "\n"
    
    # === STRUCTURE ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>📍 STRUCTURE / МЕСТО</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    regime = 'RANGING'
    if tech:
        vt = tech.get('vol_trend_10', 1)
        if tech.get('structure') == 'UPTREND' and vt > 1.2:
            regime = 'TRENDING_UP'
        elif tech.get('structure') == 'DOWNTREND' and vt > 1.2:
            regime = 'TRENDING_DOWN'
        elif tech.get('structure') in ('SIDEWAYS', 'CONSOLIDATION'):
            regime = 'SQUEEZE' if tech.get('compression', 1) < 0.7 else 'RANGING'
        elif tech.get('structure') == 'VOLATILE':
            regime = 'VOLATILE_CHOP'
    
    btc_cycle = 'UNKNOWN'
    if composite and composite.get('inputs'):
        btc_data = composite['inputs'].get('btc_context', {})
        if btc_data:
            btc_cycle = btc_data.get('cycle', 'UNKNOWN')
    
    text += f"<b>Regime:</b> {regime}\n"
    text += f"<b>Cycle (BTC):</b> {btc_cycle}\n"
    text += f"<b>Phase (Wyckoff):</b> {phase} {PHASE_EMOJI.get(phase, '')}\n"
    if sub_phase:
        text += f"<b>Sub-phase:</b> {sub_phase}\n"
    text += f"<b>Wyckoff confidence:</b> {CONFIDENCE_EMOJI.get(confidence, '')} {confidence}\n\n"
    
    text += f"<b>Что это значит:</b>\n"
    text += f"<i>{layman}</i>\n\n"
    
    # === TECHNICAL MOMENTUM ===
    if technical:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📈 TECHNICAL MOMENTUM (LIVE)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        t_feats = technical.get('features', {})
        t_class = technical.get('classification', {})
        
        text += f"<b>Signal:</b> {t_class.get('signal', 'NEUTRAL')}\n"
        text += f"Slope 3d: <b>{t_feats.get('slope_3d_pct', 0):+.1f}%</b>\n"
        text += f"Slope accel: {t_feats.get('slope_accel_pct', 0):+.1f}%\n"
        text += f"Volume 3d: <b>{t_feats.get('vol_ratio_3d_vs_30d', 1):.1f}× avg</b>\n"
        text += f"RSI: {t_feats.get('rsi', 50):.0f}\n"
        text += f"Structure: {t_feats.get('structure', '?')}\n\n"
        text += "<i>Note: not backtested (OKX history limit)</i>\n\n"
    
    # === PRICE ACTION ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📊 PRICE ACTION</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"Now: <b>${price:.4f}</b>\n"
    if tech:
        text += f"7d range: ${tech.get('low_7d', 0):.4f} → ${tech.get('high_7d', 0):.4f}\n"
        text += f"14d range: ${tech.get('low_14d', 0):.4f} → ${tech.get('high_14d', 0):.4f}\n"
        text += f"Structure: <b>{tech.get('structure', '?')}</b>\n"
        vol_ratio = tech.get('vol_ratio_last', 1)
        vol_marker = "🔥 spike" if vol_ratio > 2 else ("📉 dry" if vol_ratio < 0.5 else "")
        text += f"Volume: {vol_ratio:.1f}× avg {vol_marker}\n"
        
        if tech.get('resistance_zones'):
            text += f"\n<b>Resistance:</b>\n"
            for r in tech['resistance_zones'][:2]:
                pct = (r/price - 1) * 100 if price else 0
                text += f"  ${r:.4f} (+{pct:.1f}%)\n"
        
        if tech.get('support_zones'):
            text += f"\n<b>Support:</b>\n"
            for s in tech['support_zones'][:2]:
                pct = (s/price - 1) * 100 if price else 0
                text += f"  ${s:.4f} ({pct:+.1f}%)\n"
    text += "\n"
    
    # === ON-CHAIN ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🔗 ON-CHAIN EVIDENCE</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    # === NEW: HHI + Entropy concentration metrics ===
    conc = load_json('concentration_metrics.json')
    if conc:
        hhi = conc.get('hhi', 0)
        entropy = conc.get('entropy_bits', 0)
        top_5 = conc.get('top_5_share_pct', 0)
        n_large = conc.get('large_count', 0)
        conc_sig = conc.get('concentration_signal', 'NEUTRAL')
        
        text += f"<b>HHI concentration:</b> {hhi:.3f}"
        if hhi >= 0.25:
            text += " (concentrated → accumulation)"
        elif hhi < 0.10:
            text += " (diluted → distribution)"
        text += "\n"
        text += f"<b>Entropy:</b> {entropy:.2f} bits\n"
        text += f"<b>LARGE receivers 14d:</b> {n_large}\n"
        text += f"<b>Top 5 share:</b> {top_5:.0f}%\n"
        text += f"<b>Signal:</b> {conc_sig}\n"
    else:
        # Fallback to old metrics
        if composite and composite.get('inputs'):
            d = composite['inputs'].get('distribution') or {}
            large_14d = (d.get('counts') or {}).get('LARGE', 0)
            ratio_14d = d.get('ratio_smallamt_over_largeamt', 0)
            text += f"LARGE receivers 14d: <b>{large_14d}</b>\n"
            text += f"Distribution ratio: <b>{ratio_14d:.4f}</b>\n"
    
    watchlist_cnt = wallet_by_cat.get('watchlist', 0)
    text += f"· Watched holders: <b>{watchlist_cnt}</b>\n"
    if whale_count > 0:
        text += f"⚠ Whale events 24h: {whale_count} ({whale_amt/1e6:.1f}M STRK)\n"
    else:
        text += f"· Whale events 24h: 0 (quiet)\n"
    text += "\n"
    
    # === NEW: EFFORT/RESULT & CVD ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>⚖ EFFORT/RESULT + CVD</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    eff = load_json('effort_result.json')
    if eff:
        consensus = eff.get('consensus', 'MIXED')
        text += f"<b>Effort/Result:</b> {consensus}\n"
        # Show 1 tf detail
        for tf, r in eff.get('timeframes', {}).items():
            if r.get('signal') != 'NEUTRAL':
                text += f"  · {tf}: {r['signal']}\n"
                if r.get('interpretation'):
                    text += f"    <i>{r['interpretation'][:100]}</i>\n"
                break
    
    cvd_data = load_json('cvd_analysis.json')
    if cvd_data:
        cvd_c = cvd_data.get('consensus', 'MIXED')
        text += f"<b>CVD:</b> {cvd_c}\n"
        for tf, r in cvd_data.get('timeframes', {}).items():
            if r.get('signal') != 'NEUTRAL':
                text += f"  · {tf}: {r['signal']}\n"
                if r.get('interpretation'):
                    text += f"    <i>{r['interpretation'][:100]}</i>\n"
                break
    text += "\n"
    
    # === NEW: CEX FLOW DIRECTIONAL ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🏦 CEX FLOW (7d directional)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    cex_flow_data = load_json('cex_flow.json')
    if cex_flow_data:
        cls = cex_flow_data.get('classification', {})
        cex_signal = cls.get('signal', 'NEUTRAL')
        cex_conf = cls.get('confidence', 'LOW')
        stats = cls.get('stats', {})
        
        # Emoji marker
        cex_emoji = '🔴' if 'DISTRIBUTION' in cex_signal else ('🟢' if 'ACCUMULATION' in cex_signal else '⚪')
        
        text += f"<b>Signal:</b> {cex_emoji} {cex_signal} · {cex_conf}\n"
        text += f"<b>Net 7d:</b> {stats.get('total_net_strk', 0)/1e6:+.1f}M STRK\n"
        text += f"<b>Inflow:</b> {stats.get('total_inflow_strk', 0)/1e6:.1f}M · <b>Outflow:</b> {stats.get('total_outflow_strk', 0)/1e6:.1f}M\n"
        text += f"<b>Days:</b> {stats.get('bearish_days', 0)} bearish · {stats.get('bullish_days', 0)} bullish\n"
        
        cons_b = stats.get('consecutive_bullish', 0)
        cons_r = stats.get('consecutive_bearish', 0)
        if cons_b >= 2:
            text += f"⚡ {cons_b} days consecutive OUTFLOW (accumulation signal)\n"
        elif cons_r >= 2:
            text += f"⚡ {cons_r} days consecutive INFLOW (distribution signal)\n"
        
        text += f"\n<i>{cls.get('interpretation', '')}</i>\n"
        
        # Top movers
        top_inflows = cex_flow_data.get('top_inflows', [])[:2]
        top_outflows = cex_flow_data.get('top_outflows', [])[:2]
        
        if top_inflows:
            text += "\n<b>Biggest INFLOWS to CEX (bearish):</b>\n"
            for t in top_inflows:
                text += f"  {t['amount']/1e6:.1f}M → {t['to_cex']} from <code>{t['from'][:16]}...</code>\n"
        
        if top_outflows:
            text += "\n<b>Biggest OUTFLOWS from CEX (bullish):</b>\n"
            for t in top_outflows:
                text += f"  {t['amount']/1e6:.1f}M ← {t['from_cex']} to <code>{t['to'][:16]}...</code>\n"
        
        text += "\n<i>Note: CEX flow backtest 44.4% alone — used as ortogonal secondary signal only</i>\n"
    else:
        text += "<i>No CEX flow data</i>\n"
    text += "\n"
    
    # === CEX FLOW section (existing continues) ===
    
    # === EVENT LAYER (off-chain factors) ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📅 EVENT LAYER (off-chain)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    event_layer = load_json('event_layer.json')
    if event_layer:
        ev_signal = event_layer.get('signal', 'CALM')
        text += f"<b>Signal:</b> {ev_signal}\n"
        text += f"Bullish: {event_layer.get('bullish_score', 0)} · Bearish: {event_layer.get('bearish_score', 0)}\n\n"
        
        # Components
        comps = event_layer.get('components', {})
        text += f"<b>Components:</b>\n"
        text += f"  · GitHub: {comps.get('github_signal', 'N/A')}\n"
        text += f"  · News: {comps.get('news_signal', 'N/A')}\n"
        text += f"  · Calendar: {comps.get('calendar_signal', 'N/A')}\n"
        text += f"  · Bridge: {comps.get('bridge_signal', 'N/A')}\n"
        text += f"  · L2 sector: {comps.get('cross_token_signal', 'N/A')}\n"
        text += f"  · Discord: {comps.get('discord_signal', 'N/A')}\n"
        text += f"  · Twitter: {comps.get('twitter_signal', 'N/A')}\n"
        
        reasons = event_layer.get('reasons', {})
        if reasons.get('bullish'):
            text += f"\n<b>Bullish:</b>\n"
            for r in reasons['bullish'][:4]:
                text += f"  ✓ {r}\n"
        if reasons.get('bearish'):
            text += f"\n<b>Bearish:</b>\n"
            for r in reasons['bearish'][:4]:
                text += f"  ✗ {r}\n"
    
    # Cross-token section - detailed
    cross_token = load_json('cross_token_correlation.json')
    if cross_token:
        text += f"\n<b>L2 SECTOR (STRK relative performance):</b>\n"
        perfs = cross_token.get('performances', {})
        # Show top 4 sorted by 7d
        sorted_p = sorted(perfs.items(), key=lambda x: -x[1].get('change_7d_pct', 0))
        for symbol, p in sorted_p[:5]:
            marker = "🟢" if p['change_7d_pct'] > 0 else "🔴"
            text += f"  {marker} {symbol}: 7d {p['change_7d_pct']:+.1f}%\n"
        text += f"\n  STRK alpha 7d: <b>{cross_token['strk_alpha']['alpha_7d_pct']:+.1f}%</b>\n"
    
    # Upcoming events
    calendar_data = load_json('event_calendar.json')
    if calendar_data:
        upcoming = calendar_data.get('upcoming_unlocks', [])[:2]
        milestones = calendar_data.get('upcoming_milestones', [])[:2]
        
        if upcoming:
            text += f"\n<b>Next unlocks:</b>\n"
            for u in upcoming:
                text += f"  · {u['date']} ({u['days_until']}d) · {u['amount']/1e6:.0f}M STRK\n"
        if milestones:
            text += f"\n<b>Next milestones:</b>\n"
            for m in milestones:
                text += f"  · {m['date']} ({m['days_until']}d) · {m['event']}\n"
    
    text += "\n"
    
    # === DERIVATIVES ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📈 DERIVATIVES</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    if funding:
        fm = funding.get('funding_metrics', {})
        current = fm.get('current_annualized_pct', 0)
        text += f"Funding: <b>{current:+.2f}% ann</b>\n"
        text += f"· 3d neg fundings: {fm.get('pct_negative_3d', 0):.0f}%\n"
        text += f"· 7d min: {fm.get('min_ann_7d', 0):+.1f}%\n"
        
        if fm.get('short_crowded'):
            text += "\n⚡ <b>SHORT-CROWDED</b>: shorts overloaded\n"
            text += "<i>→ Squeeze fuel present. Short-term rally +5-15% possible even if fundamentals bearish.</i>\n"
        elif fm.get('long_crowded'):
            text += "\n⚡ <b>LONG-CROWDED</b>: too many longs\n"
            text += "<i>→ Downside risk elevated on any shock.</i>\n"
    text += "\n"
    
    # === MACRO ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🌐 MACRO CONTEXT</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    if composite and composite.get('inputs'):
        btc = composite['inputs'].get('btc_context', {})
        if btc:
            text += f"BTC: <b>${btc.get('btc_price', 0):,.0f}</b>\n"
            text += f"dist200: {btc.get('dist200_pct', 0):+.1f}%\n"
            text += f"Cycle: <b>{btc.get('cycle', '?')}</b>\n"
            slope7 = btc.get('slope7_pct', 0)
            text += f"7d: {slope7:+.2f}% · 30d: {btc.get('slope30_pct', 0):+.2f}%\n"
            if btc.get('cycle') == 'DOWN_REVERSING':
                text += "<i>→ BTC turning up from down-cycle (bullish for alts)</i>\n"
            elif btc.get('cycle') == 'DOWN':
                text += "<i>→ BTC in down-cycle, no tailwind for STRK</i>\n"
    text += "\n"
    
    # === REVERSAL TRIGGERS ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🔄 WHAT TO WATCH FOR PHASE CHANGE</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    next_phase = triggers.get('next_phase', 'unknown')
    text += f"Next phase would be <b>{next_phase}</b>:\n\n"
    
    triggers_list = triggers.get('to_next_phase', [])
    for trigger_data in triggers_list[:4]:
        if isinstance(trigger_data, (list, tuple)) and len(trigger_data) >= 2:
            desc, is_met = trigger_data[0], trigger_data[1]
            marker = "✓" if is_met else "✗"
            text += f"{marker} {desc}\n"
        elif isinstance(trigger_data, str):
            text += f"· {trigger_data}\n"
    text += "\n"
    
    # === EXIT SIGNALS ===
    exit_sigs = triggers.get('exit_signals', [])
    if exit_sigs:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>💡 WHAT TO DO NOW</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for sig in exit_sigs:
            text += f"· {sig}\n"
        text += "\n"
    
    # === DISCOVERY ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>👁 DISCOVERY (last 6h) · HIGH-QUALITY only</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"✓ Auto-accepted: {len(accepted)}\n"
    for a in accepted[:5]:
        name = a.get('registered_as', 'unnamed')
        addr = a.get('address', '')
        bal_m = (a.get('current_balance', 0)) / 1e6
        pat = a.get('pattern', '?')
        text += f"\n   <b>{name}</b> · {bal_m:.1f}M · {pat}\n"
        text += f"   <code>{addr}</code>\n"
        text += f"   <a href='https://etherscan.io/address/{addr}'>Etherscan</a>\n"
    
    text += f"\n✗ Auto-rejected: {len(rejected)}\n"
    if queued:
        text += f"? Queued (medium-quality): {len(queued)}\n"
        text += "   Use /queue to review\n"
    text += f"Total watchlist: {wallet_total}\n\n"
    
    # === HONESTY DISCLAIMER ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📋 MODEL HONESTY</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Backtest на 9 STRK events (Rally 1-3, Crash 1-3, Quiet A-C):\n"
    text += "· v2 on-chain 14d: <b>66.7%</b> ← baseline\n"
    text += "· v3 HHI-only: 33.3% (failed)\n"
    text += "· v4 hybrid: 28.6% (failed)\n"
    text += "· v5 3-day: 55.6%\n"
    text += "· Ensemble v2+v5: 33.3%\n"
    text += "· CEX flow alone: 44.4%\n"
    text += "· Technical: не backtestable (OKX limit)\n\n"
    text += "<b>Уязвимое место:</b> STRK on-chain distribution shape "
    text += "хроническая (много receivers всегда). Rally-2, Rally-3 показали "
    text += "DISTRIBUTION patterns но выросли +175%/+99%. On-chain НЕ leads-indicator для STRK rallies.\n\n"
    text += "<b>Решение:</b> Confluence gate (сверху). HIGH signal когда "
    text += "5+ independent checks согласны по 3 слоям (on-chain + technical + off-chain). "
    text += "Trades recall for precision. Действуй только на HIGH.</i>\n\n"
    
    # === SCENARIOS (Base/Bull/Bear) ===
    scenarios_data = load_json('scenario_analysis.json')
    if scenarios_data and scenarios_data.get('scenarios'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🎯 SCENARIOS (14d outlook)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for s in scenarios_data['scenarios']:
            s_type = s.get('type', 'BASE')
            prob = s.get('probability_pct', 0)
            pr = s.get('price_range', [0, 0])
            if isinstance(pr, list) and len(pr) >= 2:
                low, high = pr[0], pr[1]
            elif isinstance(pr, dict):
                low, high = pr.get('low', 0), pr.get('high', 0)
            else:
                low, high = 0, 0
            
            emoji = "⚪" if s_type == 'BASE' else "🟢" if s_type == 'BULL' else "🔴"
            text += f"<b>{emoji} {s_type} ({prob}%)</b> · ${low:.4f}—${high:.4f}\n"
            narrative = s.get('narrative', s.get('description', ''))
            text += f"<i>{narrative[:180]}</i>\n"
            text += f"→ {s.get('position_hint', '—')[:100]}\n\n"
    
    # === FULL REPORT LINK ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📄 FULL RUN REPORT</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Полный HTML отчёт с 11 блоками:\n"
    text += "· DECISION LAYER (главное)\n"
    text += "· LAYMAN (простой язык)\n"
    text += "· CONFLUENCE GATE (checks)\n"
    text += "· МЕСТО / STRUCTURE\n"
    text += "· OFF-CHAIN EVENT LAYER\n"
    text += "· L2 SECTOR table\n"
    text += "· SCENARIOS (Base/Bull/Bear)\n"
    text += "· WATCHERS (long/short triggers)\n"
    text += "· EVIDENCE (под капотом)\n"
    text += "· FORECAST для FORWARDTEST_LOG\n\n"
    text += "→ data/reports/STRK_RUN_latest.html\n"
    text += "→ GitHub Actions artifacts</i>"
    
    return text


def format_fallback_digest(composite, accepted, rejected, whale_count, wallet_total):
    now = datetime.now(timezone.utc)
    text = f"<b>🤖 STRK-GUARD · Digest (basic)</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
    text += "<i>Wyckoff phase data not available.</i>\n"
    if composite:
        text += f"Signal: {composite.get('signal', '?')}\n"
    text += f"Watchlist: {wallet_total} · Whales 24h: {whale_count}\n"
    return text


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:")
        logger.warning(text)
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        # Split if too long
        if len(text) > 4000:
            parts, current = [], ""
            for line in text.split('\n'):
                if len(current) + len(line) > 3800:
                    parts.append(current)
                    current = line + '\n'
                else:
                    current += line + '\n'
            if current:
                parts.append(current)
            for part in parts:
                data = json.dumps({'chat_id': chat_id, 'text': part, 'parse_mode': 'HTML',
                                  'disable_web_page_preview': True}).encode()
                r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(r, timeout=10)
        else:
            data = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
                              'disable_web_page_preview': True}).encode()
            r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(r, timeout=10)
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def main():
    logger.info("=" * 60)
    logger.info("DAILY DIGEST · Wyckoff phase-oriented")
    logger.info("=" * 60)
    text = format_digest()
    logger.info(f"Digest built (length {len(text)})")
    sent = send_telegram(text)
    if sent:
        logger.info("Digest sent to Telegram")
    return 0


if __name__ == '__main__':
    sys.exit(main())
