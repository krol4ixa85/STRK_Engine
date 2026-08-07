#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_digest.py - Wyckoff phase-oriented Telegram digest
Sent to Xenia every 6 hours or on-demand via /status

Integrated:
  · single_brain_v1: единый DECISION = confluence_gate + MICRO/SWING horizon split
  · alerts_log_v1: persistence через alert_logger в data/history/alerts.jsonl
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
DECISION_LOG = CACHE_DIR / 'decision_log.json'
WHALE_EVENTS = CACHE_DIR / 'whale_events_state.json'

# alert_logger for persistent history in data/history/alerts.jsonl
# Fallback: если alert_logger.py не создан — no-op, digest всё равно отправится
try:
    from alert_logger import log_alert as _log_alert
except ImportError:
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from alert_logger import log_alert as _log_alert
    except Exception:
        def _log_alert(*a, **kw):
            return {}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('digest')


def load_json(name):
    path = CACHE_DIR / name
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except:
            return None
    return None


def get_recent_decisions(hours_back=6):
    if not DECISION_LOG.exists():
        return [], [], []
    try:
        data = json.loads(DECISION_LOG.read_text(encoding='utf-8'))
        decisions = data.get('decisions', [])
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
        recent = [d for d in decisions if d.get('timestamp', '') > cutoff]
        accepted = [d for d in recent if d.get('action') == 'ACCEPTED']
        rejected = [d for d in recent if d.get('action') == 'REJECTED']
        queued = [d for d in recent if d.get('action') == 'QUEUED_FOR_REVIEW']
        return accepted, rejected, queued
    except:
        return [], [], []


def get_whale_events_24h():
    if not WHALE_EVENTS.exists():
        return 0, 0
    try:
        data = json.loads(WHALE_EVENTS.read_text(encoding='utf-8'))
        events = data.get('recent_events', [])
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
        recent = [e for e in events if e.get('timestamp', 0) > cutoff]
        count = len(recent)
        total_strk = sum(e.get('amount_strk', 0) for e in recent)
        return count, total_strk
    except:
        return 0, 0


def count_wallets():
    from wallet_registry import load_registry
    wallets = load_registry()
    total = len(wallets)
    by_cat = {}
    for w in wallets.values():
        cat = w.get('category', 'unknown')
        by_cat[cat] = by_cat.get(cat, 0) + 1
    return total, by_cat


PHASE_EMOJI = {
    'ACCUMULATION': '🟢',
    'MARKUP': '📈',
    'DISTRIBUTION': '🟡',
    'MARKDOWN': '📉',
    'UNKNOWN': '⚪'
}

CONFIDENCE_EMOJI = {
    'HIGH': '🎯',
    'MEDIUM': '🟠',
    'LOW': '⚪',
    'UNKNOWN': '❓'
}


def format_digest():
    """Format the digest text"""
    now = datetime.now(timezone.utc)
    
    # Load Wyckoff data (primary)
    wyckoff = load_json('wyckoff_phase.json')
    composite = load_json('composite_signal_v2.json')
    confluence = load_json('confluence_gate.json')
    technical = load_json('technical_momentum.json')
    
    # Recent activity
    accepted, rejected, queued = get_recent_decisions(hours_back=6)
    whale_count, whale_amt = get_whale_events_24h()
    wallet_total, wallet_by_cat = count_wallets()
    
    if not wyckoff:
        return format_fallback_digest(composite, accepted, rejected, whale_count, wallet_total)
    
    phase = wyckoff['phase']
    sub_phase = wyckoff.get('sub_phase', '')
    confidence = wyckoff['confidence']
    tech = wyckoff.get('technical', {})
    layman = wyckoff.get('layman_explanation', '')
    
    price = tech.get('price_now', 0)
    
    # === HEADER ===
    text = f"<b>🤖 STRK-GUARD · Phase Analysis</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
    text += "<i>⚠ Любой HIGH signal → открой /liq для полного контекста, а не мгновенное buy/sell.</i>\n\n"
    
    # === DECISION (single source of truth) ===
    if confluence:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🎯 DECISION</b> <i>(single source of truth)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        signal = confluence.get('signal', 'NO_SIGNAL')
        conf = confluence.get('confidence', 'LOW')
        summary = confluence.get('summary', '')
        
        # Signal emoji
        if 'RALLY' in signal and 'HIGH' in conf:
            emoji = '🟢🟢'
        elif 'RALLY' in signal:
            emoji = '🟢'
        elif 'CRASH' in signal and 'HIGH' in conf:
            emoji = '🔴🔴'
        elif 'CRASH' in signal:
            emoji = '🔴'
        elif 'PARTIAL' in signal:
            emoji = '🟡'
        else:
            emoji = '⚪'
        
        text += f"{emoji} <b>{signal}</b>\n"
        text += f"Confidence: <b>{conf}</b>\n"
        text += f"<i>{summary}</i>\n\n"
        
        # Show checks passed
        rally = confluence.get('rally_score', 0)
        crash = confluence.get('crash_score', 0)
        total = confluence.get('total_checks', 5)
        
        text += f"<b>Rally checks:</b> {rally}/{total}\n"
        text += f"<b>Crash checks:</b> {crash}/{total}\n\n"
        
        # If HIGH signal, show which checks passed
        rally_checks = confluence.get('rally_checks_passed', [])
        crash_checks = confluence.get('crash_checks_passed', [])
        
        if rally_checks and 'RALLY' in signal:
            text += f"<b>✅ Passed (rally):</b>\n"
            for c in rally_checks:
                text += f"  · {c.replace('_', ' ')}\n"
            text += "\n"
        
        if crash_checks and 'CRASH' in signal:
            text += f"<b>✅ Passed (crash):</b>\n"
            for c in crash_checks:
                text += f"  · {c.replace('_', ' ')}\n"
            text += "\n"
    
    # === INTERPRETATION LAYER (narrative · action см. DECISION) ===
    interpretation = load_json('interpretation.json')
    if interpretation and interpretation.get('interpretation', {}).get('primary'):
        interp = interpretation.get('interpretation', {})
        primary = interp.get('primary', {})
        secondary = interp.get('secondary', {})
        
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🧠 INTERPRETATION</b> <i>(narrative · action см. DECISION)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        pattern_name = primary.get('pattern', 'UNKNOWN').replace('pattern_', '').replace('_', ' ').title()
        pattern_prob = primary.get('probability', 0)
        
        text += f"<b>Primary:</b> {pattern_name}\n"
        text += f"<b>Probability:</b> {pattern_prob*100:.0f}%\n"
        text += f"<b>Direction:</b> {primary.get('direction', 'UNKNOWN')}\n\n"
        
        # Story (narrative)
        story = primary.get('story', '')
        if story:
            text += f"<i>{story[:400]}</i>\n\n"
        
        # Secondary if exists
        if secondary and secondary.get('pattern'):
            sec_name = secondary.get('pattern', '').replace('pattern_', '').replace('_', ' ').title()
            sec_prob = secondary.get('probability', 0)
            text += f"<b>Alt:</b> {sec_name} ({sec_prob*100:.0f}%)\n\n"
        
        # Position hint (narrative context — not action)
        position_hint = interp.get('position_hint', {})
        if position_hint.get('signal'):
            text += f"<b>Position hint (narrative):</b> {position_hint.get('signal', 'FLAT')}\n"
            if position_hint.get('reason'):
                text += f"<i>{position_hint.get('reason', '')[:200]}</i>\n"
        
        text += "\n"
    
    # === WHALE INTERPRETATION ===
    whale = load_json('whale_analysis.json')
    if whale:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🐋 WHALE INTERPRETATION</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        cohort = whale.get('cohort_read', 'MIXED_SIGNALS')
        text += f"<b>{cohort.replace('_', ' ')}</b>\n"
        
        stats = whale.get('stats', {})
        n = stats.get('n_events', 0)
        text += f"Events analyzed: {n}\n"
        text += f"CEX→private: {stats.get('cex_to_private_pct', 0):.0f}%\n"
        text += f"Private→CEX: {stats.get('private_to_cex_pct', 0):.0f}%\n"
        
        top_direction = whale.get('top_pattern', {}).get('direction', 'unknown')
        if top_direction != 'unknown':
            text += f"<b>Dominant:</b> {top_direction.replace('_', ' → ')}\n"
        
        text += "\n"
    
    # === COHORT BEHAVIOR ===
    cohorts = load_json('cohort_tracker.json')
    if cohorts and cohorts.get('cohorts'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>👥 COHORT BEHAVIOR</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        cohort_data = cohorts.get('cohorts', {})
        aggregate = cohorts.get('aggregate', {})
        
        # Aggregate signal
        agg_signal = aggregate.get('signal', 'UNKNOWN')
        text += f"<b>Aggregate:</b> {agg_signal}\n\n"
        
        # Show each cohort
        for cohort_name, cohort_info in cohort_data.items():
            if cohort_info.get('status') == 'no_data':
                continue
            
            n_wallets = cohort_info.get('n_wallets', 0)
            net_24h = cohort_info.get('net_24h_strk', 0)
            direction = cohort_info.get('direction', 'STABLE')
            
            display_name = cohort_name.replace('_', ' ').title()
            arrow = '↗' if 'INFLOW' in direction else ('↘' if 'OUTFLOW' in direction else '→')
            
            text += f"<b>{display_name}</b> ({n_wallets} wallets)\n"
            text += f"  {arrow} 24h: {net_24h:+,.0f} STRK\n"
        
        text += "\n"
    
    # === STRUCTURE / МЕСТО ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>📍 STRUCTURE / МЕСТО</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    regime = wyckoff.get('regime', 'UNKNOWN')
    text += f"<b>Regime:</b> {regime}\n"
    
    btc_cycle = wyckoff.get('btc_cycle', 'UNKNOWN')
    text += f"<b>Cycle (BTC):</b> {btc_cycle}\n"
    
    text += f"<b>Phase (Wyckoff):</b> {phase} {PHASE_EMOJI.get(phase, '')}\n"
    text += f"<b>Sub-phase:</b> {sub_phase}\n"
    text += f"<b>Wyckoff confidence:</b> {CONFIDENCE_EMOJI.get(confidence, '')} {confidence}\n\n"
    
    # Simple explanation with Composite context disclaimer
    text += f"<b>Что это значит:</b>\n"
    text += f"<i>{layman}</i>\n"
    text += f"<i>⓵ Composite phase/BTC cycle — контекст, не вердикт. Action = DECISION выше.</i>\n\n"
    
    # === TECHNICAL MOMENTUM ===
    if technical and technical.get('features'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📈 TECHNICAL MOMENTUM</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        feat = technical['features']
        signal = technical.get('signal', 'NEUTRAL')
        conf_val = technical.get('confidence', 'LOW')
        
        text += f"<b>Signal:</b> {signal} ({conf_val})\n"
        text += f"<b>Slope 3d:</b> {feat.get('slope_3d_pct', 0):.2f}% "
        text += f"({'↑' if feat.get('slope_3d_pct', 0) > 0 else '↓'})\n"
        text += f"<b>Volume 3d/30d:</b> {feat.get('vol_ratio_3d_vs_30d', 1):.2f}x\n"
        text += f"<b>RSI:</b> {feat.get('rsi', 50):.0f}\n"
        text += f"<b>From 14d high:</b> {feat.get('pct_from_14d_high', 0):.1f}%\n"
        text += f"<b>From 14d low:</b> +{feat.get('pct_from_14d_low', 0):.1f}%\n\n"
    
    # === ON-CHAIN EVIDENCE ===
    conc_data = load_json('concentration_metrics.json')
    if conc_data and conc_data.get('metrics'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🔗 ON-CHAIN EVIDENCE</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        m = conc_data['metrics']
        text += f"<b>Signal:</b> {conc_data.get('signal', 'NEUTRAL')}\n"
        text += f"<b>HHI:</b> {m.get('hhi', 0):.4f} ({conc_data.get('hhi_regime', 'UNKNOWN')})\n"
        text += f"<b>Entropy:</b> {m.get('entropy_norm', 0):.3f}\n"
        text += f"<b>Top1 share:</b> {m.get('top1_share_pct', 0):.2f}%\n"
        text += f"<b>Top10 share:</b> {m.get('top10_share_pct', 0):.2f}%\n"
        
        # Layman
        conc_layman = conc_data.get('layman', '')
        if conc_layman:
            text += f"<i>{conc_layman[:250]}</i>\n"
        
        text += "\n"
    
    # === MICRO + SWING CONTEXT (horizon split) === Effort/CVD
    eff = load_json('effort_result.json')
    cvd_data = load_json('cvd_analysis.json')
    _micro_lines = []
    _swing_lines = []
    
    if eff:
        for tf, r in (eff.get('timeframes') or {}).items():
            if r.get('signal') == 'NEUTRAL':
                continue
            line = f"  · Effort {tf}: {r['signal']}"
            if r.get('interpretation'):
                line += f"\n    <i>{r['interpretation'][:100]}</i>"
            if str(tf).lower() in ('1h', '15m', '30m'):
                _micro_lines.append(line)
            else:
                _swing_lines.append(line)
    
    if cvd_data:
        for tf, r in (cvd_data.get('timeframes') or {}).items():
            if r.get('signal') == 'NEUTRAL':
                continue
            line = f"  · CVD {tf}: {r['signal']}"
            if r.get('interpretation'):
                line += f"\n    <i>{r['interpretation'][:100]}</i>"
            if str(tf).lower() in ('1h', '15m', '30m'):
                _micro_lines.append(line)
            else:
                _swing_lines.append(line)
    
    if _micro_lines:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚡ MICRO CONTEXT</b> <i>(тактика 4-24h · не decision-relevant)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<i>Короткий горизонт: быстро устаревает, шумный. Для интуиции, не для action.</i>\n"
        if eff:
            text += f"<b>Effort/Result consensus:</b> {eff.get('consensus', 'MIXED')}\n"
        if cvd_data:
            text += f"<b>CVD consensus:</b> {cvd_data.get('consensus', 'MIXED')}\n"
        for line in _micro_lines[:4]:
            text += line + "\n"
        text += "\n"
    
    if _swing_lines:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚖ SWING CONTEXT</b> <i>(свинг 3-14d)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for line in _swing_lines[:4]:
            text += line + "\n"
        text += "\n"
    
    if not _micro_lines and not _swing_lines and (eff or cvd_data):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚖ EFFORT/RESULT + CVD</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        if eff:
            text += f"<b>Effort/Result:</b> {eff.get('consensus', 'MIXED')}\n"
        if cvd_data:
            text += f"<b>CVD:</b> {cvd_data.get('consensus', 'MIXED')}\n"
        text += "<i>Все таймфреймы NEUTRAL.</i>\n\n"
    
    # === NEW: CEX FLOW ===
    cex = load_json('cex_flow.json')
    if cex:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🏦 CEX FLOW (7d)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"<b>Signal:</b> {cex.get('signal', 'NEUTRAL')}\n"
        text += f"<b>Regime:</b> {cex.get('regime', 'UNKNOWN')}\n"
        text += f"<b>Trend:</b> {cex.get('trend', 'FLAT')}\n\n"
        
        m = cex.get('metrics', {})
        text += f"<b>Net flow 7d:</b> {m.get('net_flow_pct_supply_7d', 0):.2f}%\n"
        text += f"<b>Inflow:</b> {m.get('inflow_total_strk', 0):,.0f} STRK\n"
        text += f"<b>Outflow:</b> {m.get('outflow_total_strk', 0):,.0f} STRK\n"
        
        # Layman
        cex_layman = cex.get('layman', '')
        if cex_layman:
            text += f"<i>{cex_layman[:250]}</i>\n"
        
        text += "\n"
    
    # === EVENT LAYER (event calendar impact) ===
    event_layer = load_json('event_layer.json')
    if event_layer and event_layer.get('signal') and event_layer.get('signal') != 'NEUTRAL':
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📅 EVENT LAYER</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        text += f"<b>Signal:</b> {event_layer.get('signal', 'NEUTRAL')}\n"
        text += f"<b>Confidence:</b> {event_layer.get('confidence', 'LOW')}\n"
        
        # Show top upcoming event
        upcoming = event_layer.get('upcoming_events', [])
        if upcoming:
            top_event = upcoming[0]
            days = top_event.get('days_until', 0)
            title = top_event.get('title', 'unknown')[:60]
            impact = top_event.get('impact', 'unknown')
            
            text += f"<b>Next event ({days}d):</b> {title}\n"
            text += f"<b>Impact:</b> {impact}\n"
        
        # Signal reason
        reason = event_layer.get('reason', '')
        if reason:
            text += f"<i>{reason[:200]}</i>\n"
        
        text += "\n"
    
    # === DERIVATIVES ===
    funding = load_json('funding_signal.json')
    if funding:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📊 DERIVATIVES</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        m = funding.get('funding_metrics', {})
        text += f"<b>Signal:</b> {funding.get('signal', 'NEUTRAL')}\n"
        text += f"<b>Funding (annualized):</b> {m.get('current_annualized_pct', 0):+.2f}%\n"
        text += f"<b>Regime:</b> {m.get('regime', 'UNKNOWN')}\n"
        
        if m.get('short_crowded'):
            text += f"⚠️ <b>Short crowded</b> — squeeze potential\n"
        if m.get('long_crowded'):
            text += f"⚠️ <b>Long crowded</b> — flush risk\n"
        text += "\n"
    
    # === MACRO CONTEXT ===
    ma = load_json('agent_input.json')
    if ma:
        btc_price = ma.get('btc', {}).get('price_usd', 0)
        btc_change = ma.get('btc', {}).get('change_24h_pct', 0)
        strk_price = ma.get('strk', {}).get('price_usd', 0)
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🌐 MACRO</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"<b>BTC:</b> ${btc_price:,.0f} ({btc_change:+.1f}%)\n"
        text += f"<b>STRK:</b> ${strk_price:.4f}\n\n"
    
    # === WHAT TO WATCH ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🔄 WHAT TO WATCH</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    watch = wyckoff.get('watch_events', [])
    for event in watch[:5]:
        text += f"· {event}\n"
    text += "\n"
    
    # === WHAT TO DO NOW === single source of truth: только confluence
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>💡 WHAT TO DO NOW</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    if confluence:
        _conf_sig = confluence.get('signal', 'NO_SIGNAL')
        _conf_lvl = confluence.get('confidence', 'LOW')
        if _conf_lvl == 'HIGH' and 'RALLY' in _conf_sig:
            text += "🟢🟢 <b>DECISION: RALLY signal, HIGH confidence</b>\n"
            text += "→ Открой /liq для evidence review. Финальное действие после LIQ.\n"
        elif _conf_lvl == 'HIGH' and 'CRASH' in _conf_sig:
            text += "🔴🔴 <b>DECISION: CRASH signal, HIGH confidence</b>\n"
            text += "→ Открой /liq для evidence review. Финальное действие после LIQ.\n"
        elif _conf_lvl == 'MEDIUM':
            text += "🟡 <b>DECISION: MEDIUM confidence — STAY FLAT</b>\n"
            text += "→ Не входить и не выходить. Ждать HIGH confluence или явное invalidation.\n"
        else:
            text += "⚪ <b>DECISION: LOW confidence / no signal — STAY FLAT</b>\n"
            text += "→ Нет достаточного основания для action. Мониторим MONITOR_72h.\n"
        text += "\n<i>💡 Все action-предложения в других блоках digest — narrative, не вердикт.</i>\n\n"
    else:
        text += "⚪ <b>DECISION: CONFLUENCE_GATE недоступен</b>\n"
        text += "→ STAY FLAT до восстановления пайплайна.\n\n"
    
    # === DISCOVERY (last 6h) - HIGH-QUALITY only ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>👁 DISCOVERY (6h)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    if accepted:
        text += f"<b>New wallets:</b> {len(accepted)}\n"
        for d in accepted[:3]:
            text += f"  ✓ {d.get('name', 'unnamed')[:20]} → {d.get('assigned_category', 'watchlist')}\n"
    else:
        text += "<b>New wallets:</b> 0\n"
    
    text += f"<b>Rejected:</b> {len(rejected)}\n"
    text += f"<b>In queue:</b> {len(queued)}\n"
    text += f"<b>Whale events 24h:</b> {whale_count} ({whale_amt:,.0f} STRK)\n\n"
    
    # === MODEL HONESTY ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📋 MODEL HONESTY</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    if wyckoff.get('confidence') == 'HIGH':
        text += f"⚠️ HIGH confidence — но backtest на 9 events дал 33% accuracy.\n"
        text += f"⚠️ N=9, не статистика. Sub-phases не откалиброваны.\n"
    elif wyckoff.get('confidence') == 'MEDIUM':
        text += f"Baseline v2: 66.7% (6/9). Все 3 fails — Distribution phase.\n"
    else:
        text += f"Baseline v2 (6 signals): 66.7% accuracy on 9 tests.\n"
    text += f"Читать: /probability для деталей.\n\n"
    
    # === SCENARIOS ===
    scenario_data = load_json('scenario_analysis.json')
    if scenario_data and scenario_data.get('scenarios'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🎯 SCENARIOS (7-14d)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        scenarios = scenario_data.get('scenarios', {})
        primary = scenario_data.get('primary', 'BASE')
        
        # Show all 3 scenarios with probabilities
        for scen_name in ['bull', 'base', 'bear']:
            scen = scenarios.get(scen_name, {})
            prob = scen.get('probability', 0)
            target = scen.get('target_price', 0)
            change = scen.get('price_change_pct', 0)
            
            emoji = '🟢' if scen_name == 'bull' else ('⚪' if scen_name == 'base' else '🔴')
            is_primary = ' ⭐ PRIMARY' if scen_name.upper() == primary else ''
            
            text += f"{emoji} <b>{scen_name.upper()}</b> ({prob*100:.0f}%){is_primary}\n"
            text += f"   → ${target:.4f} ({change:+.1f}%)\n"
        
        text += "\n"
    
    # === FOOTER ===
    text += f"<b>Coverage:</b> {wallet_total} wallets\n"
    for cat, cnt in sorted(wallet_by_cat.items()):
        text += f"  · {cat}: {cnt}\n"
    text += f"\n<i>Next update: in ~6h. If you want a full RUN report — reply /run</i>"
    
    return text


def format_fallback_digest(composite, accepted, rejected, whale_count, wallet_total):
    """Fallback if Wyckoff data missing"""
    now = datetime.now(timezone.utc)
    
    text = f"<b>🤖 STRK-GUARD</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
    text += f"⚠️ Wyckoff analysis unavailable\n\n"
    
    if composite:
        text += f"<b>Composite signal:</b>\n"
        text += f"{composite.get('direction', 'UNKNOWN')} · "
        text += f"{composite.get('strength', 0):.2f} strength · "
        text += f"{composite.get('confidence', 'UNKNOWN')} confidence\n\n"
    
    text += f"<b>Recent activity (6h):</b>\n"
    text += f"· Accepted: {len(accepted)}\n"
    text += f"· Rejected: {len(rejected)}\n"
    text += f"· Whale events 24h: {whale_count}\n\n"
    
    text += f"<b>Coverage:</b> {wallet_total} wallets\n"
    
    return text


def send_telegram(text):
    """Send digest to Telegram, or print if not configured"""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:")
        logger.warning(text[:500])
        return False
    
    import urllib.request
    import urllib.parse
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }).encode()
    
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info("Digest sent to Telegram")
                return True
            else:
                logger.error(f"Telegram error: {result}")
                return False
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")
        return False


def main():
    logger.info("=" * 60)
    logger.info("DAILY DIGEST - Wyckoff phase-oriented")
    logger.info("=" * 60)
    
    text = format_digest()
    logger.info(f"Digest built (length {len(text)})")
    
    sent = send_telegram(text)
    
    # Persistent log to data/history/alerts.jsonl (независимо от send success)
    _log_alert(event_type="digest", text=text, sent=sent)
    
    if sent:
        logger.info("Digest sent to Telegram")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
