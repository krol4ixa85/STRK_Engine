#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_digest.py — Telegram delivery для STRK Engine (digest / liq / run)

Modes (via env MODE):
  · MODE=digest — краткий heartbeat (по умолчанию, для cron 6h)
  · MODE=liq    — LIQ формат: DECISION + action + MICRO/SWING/FUND, 1 msg
  · MODE=run    — 3 сообщения "RUN · 1/3" .. "3/3" + опциональный HTML

Vocabulary горизонтов (единая по всем сообщениям):
  · MICRO (4-24h)      — шумно, не decision-relevant
  · SWING (3-14d)      — tactical context
  · FUNDAMENTAL (30-90d+) — usage/fees/staking/unlock/thesis health

Action ТОЛЬКО из DECISION (confluence_gate). Все горизонты = context.

Empty/missing cache → NOT_CHECKED (не подделываем 0).

Telegram limit: 4096 chars per message. Truncation если превышаем.

Персистентность: каждый send → data/history/alerts.jsonl (inline log_alert).
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

# ============================================================
# INLINE ALERT LOGGER (no import — гарантированно работает
# независимо от наличия alert_logger.py или sys.path)
# Пишет в data/history/alerts.jsonl одну строку JSON после каждого send.
# Никогда не бросает исключений — не может сломать digest.
# ============================================================
def _log_alert(event_type, text='', sent=True, error_msg='', extra=None):
    try:
        import hashlib
        history_dir = SCRIPT_DIR / 'data' / 'history'
        history_dir.mkdir(parents=True, exist_ok=True)
        alerts_file = history_dir / 'alerts.jsonl'
        now = datetime.now(timezone.utc)
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        # Determine sent status
        if isinstance(sent, dict):
            status = sent.get('status', 'UNKNOWN')
        elif not token or not chat_id:
            status = 'DRY_RUN'
        elif sent:
            status = 'SENT'
        else:
            status = 'FAILED'
        # Build record
        record = {
            'ts': now.isoformat(),
            'event_type': event_type,
            'sent_status': status,
            'error_msg': str(error_msg or ''),
            'chat_id_hash': ('sha256:' + hashlib.sha256(chat_id.encode()).hexdigest()[:16]) if chat_id else '',
            'text_length_chars': len(text or ''),
            'text_sha256': 'sha256:' + hashlib.sha256((text or '').encode('utf-8')).hexdigest()[:16],
            'extra': extra or {},
        }
        with open(alerts_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception:
        pass  # never raise from log function


# ============================================================
# HELPERS
# ============================================================
TELEGRAM_MAX = 4096
NOT_CHECKED = 'NOT_CHECKED'

def nv(value, default=NOT_CHECKED, formatter=None):
    """Safe value or NOT_CHECKED. Never returns fake 0 for missing data."""
    if value is None:
        return default
    if isinstance(value, (dict, list)) and not value:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        if formatter:
            return formatter(value)
    except Exception:
        return default
    return value

def truncate(text, max_len=TELEGRAM_MAX):
    """Truncate text to Telegram limit, preserving HTML if possible."""
    if len(text) <= max_len:
        return text
    # Обрезаем до max_len - 100 и добавляем предупреждение
    truncated = text[:max_len - 60]
    # Не обрываем в середине HTML тега
    last_lt = truncated.rfind('<')
    last_gt = truncated.rfind('>')
    if last_lt > last_gt:
        truncated = truncated[:last_lt]
    return truncated + '\n\n<i>...truncated (limit 4096)</i>'


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
    """Safe wallet count — не падает если wallet_registry недоступен."""
    try:
        from wallet_registry import load_registry
        wallets = load_registry()
    except (ImportError, Exception):
        # Fallback 1: try load_seeds
        try:
            from wallet_registry import load_seeds
            seeds = load_seeds()
            wallets = {}
            for cat, entries in seeds.items():
                if cat.startswith('_'):
                    continue
                if isinstance(entries, dict):
                    for name, info in entries.items():
                        if isinstance(info, dict):
                            wallets[name] = {'category': cat}
        except Exception:
            # Fallback 2: read flow_seeds.json напрямую
            try:
                seeds_file = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
                if seeds_file.exists():
                    import json as _json
                    data = _json.loads(seeds_file.read_text(encoding='utf-8'))
                    wallets = {}
                    for cat, entries in data.items():
                        if cat.startswith('_'):
                            continue
                        if isinstance(entries, dict):
                            for name, info in entries.items():
                                if isinstance(info, dict):
                                    wallets[name] = {'category': cat}
                else:
                    wallets = {}
            except Exception:
                wallets = {}
    total = len(wallets)
    by_cat = {}
    for w in wallets.values():
        cat = w.get('category', 'unknown') if isinstance(w, dict) else 'unknown'
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
        # position_hint может быть dict {'signal': 'FLAT', 'reason': '...'} или string "Position: ..."
        if isinstance(position_hint, dict) and position_hint.get('signal'):
            text += f"<b>Position hint (narrative):</b> {position_hint.get('signal', 'FLAT')}\n"
            if position_hint.get('reason'):
                text += f"<i>{position_hint.get('reason', '')[:200]}</i>\n"
        elif isinstance(position_hint, str) and position_hint.strip():
            text += f"<b>Position hint (narrative):</b>\n"
            text += f"<i>{position_hint[:300]}</i>\n" 
        
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
    
    # === SCENARIOS === Обёрнуто в try/except — падение блока НЕ роняет digest
    try:
        scenario_data = load_json('scenario_analysis.json')
        if scenario_data and scenario_data.get('scenarios'):
            text += "━━━━━━━━━━━━━━━━━━━\n"
            text += "<b>🎯 SCENARIOS (7-14d)</b>\n"
            text += "━━━━━━━━━━━━━━━━━━━\n"
            
            raw_scenarios = scenario_data.get('scenarios', {})
            primary = scenario_data.get('primary', 'BASE')
            
            # Normalize: scenario_engine пишет list, digest ждёт dict.
            # Принимаем оба формата.
            scenarios_dict = {}
            if isinstance(raw_scenarios, dict):
                # уже dict — используем как есть
                for key, value in raw_scenarios.items():
                    if isinstance(value, dict):
                        scenarios_dict[str(key).lower()] = value
            elif isinstance(raw_scenarios, list):
                # list — маппим по name/type/label
                for item in raw_scenarios:
                    if not isinstance(item, dict):
                        continue
                    # Пробуем разные ключи для имени
                    name = (item.get('name') or item.get('type') or 
                            item.get('label') or item.get('scenario') or '').lower()
                    if 'bull' in name:
                        scenarios_dict['bull'] = item
                    elif 'bear' in name:
                        scenarios_dict['bear'] = item
                    elif 'base' in name or 'neutral' in name:
                        scenarios_dict['base'] = item
            
            # Show all 3 scenarios with probabilities
            for scen_name in ['bull', 'base', 'bear']:
                scen = scenarios_dict.get(scen_name, {})
                if not isinstance(scen, dict):
                    continue
                
                # Пробуем разные форматы probability
                prob = scen.get('probability', scen.get('probability_pct', 0))
                if isinstance(prob, (int, float)) and prob > 1.5:
                    prob = prob / 100.0  # если это проценты — конвертируем в fraction
                
                # target price
                target = scen.get('target_price', 0)
                if not target:
                    # Может быть price_range list или dict
                    pr = scen.get('price_range', 0)
                    if isinstance(pr, list) and len(pr) >= 2:
                        target = (pr[0] + pr[1]) / 2
                    elif isinstance(pr, dict):
                        target = (pr.get('low', 0) + pr.get('high', 0)) / 2
                
                # price change
                change = scen.get('price_change_pct', scen.get('change_pct', 0))
                
                emoji = '🟢' if scen_name == 'bull' else ('⚪' if scen_name == 'base' else '🔴')
                is_primary = ' ⭐ PRIMARY' if scen_name.upper() == str(primary).upper() else ''
                
                text += f"{emoji} <b>{scen_name.upper()}</b> ({prob*100:.0f}%){is_primary}\n"
                text += f"   → ${target:.4f} ({change:+.1f}%)\n"
            
            text += "\n"
    except Exception as _scen_err:
        # SCENARIOS блок сломался — не критично, продолжаем digest
        logger.warning(f"SCENARIOS block skipped: {_scen_err}")
    
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


def send_telegram_document(file_path, caption=''):
    """Send file (HTML/PDF etc) to Telegram via multipart POST."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured — document not sent")
        return False
    if not Path(file_path).exists():
        logger.warning(f"Document not found: {file_path}")
        return False

    import urllib.request
    import uuid
    boundary = f"----STRK{uuid.uuid4().hex}"

    with open(file_path, 'rb') as f:
        file_bytes = f.read()

    filename = Path(file_path).name
    body = []
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'.encode())
    body.append(f'{chat_id}\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="caption"\r\n\r\n'.encode())
    body.append(f'{caption[:1024]}\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'.encode())
    body.append(b'HTML\r\n')
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
    body.append(b'Content-Type: text/html\r\n\r\n')
    body.append(file_bytes)
    body.append(f'\r\n--{boundary}--\r\n'.encode())
    data = b''.join(body)

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"Document sent: {filename}")
                return True
            logger.error(f"sendDocument error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send document: {e}")
        return False


# ============================================================
# LIQ MODE — 1 сообщение (max 2 если overflow)
# ============================================================
def format_liq():
    """LIQ формат: DECISION + action + stop/targets + MICRO/SWING/FUND context."""
    now = datetime.now(timezone.utc)
    conf = load_json('confluence_gate.json') or {}
    wyk = load_json('wyckoff_phase.json') or {}
    tech = (load_json('technical_momentum.json') or {}).get('features') or {}
    cex = load_json('cex_flow.json') or {}
    eff = load_json('effort_result.json') or {}
    cvd = load_json('cvd_analysis.json') or {}
    ct = load_json('cross_token_correlation.json') or {}
    cal = load_json('event_calendar.json') or {}
    stak = load_json('native_staking_flow.json') or {}
    scen = load_json('scenario_analysis.json') or {}
    interp = (load_json('interpretation.json') or {}).get('interpretation') or {}

    # Header
    t = f"<b>⚡ LIQ · STRK</b>\n"
    t += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"

    # DECISION (canonical action)
    sig = conf.get('signal', NOT_CHECKED)
    cfd = conf.get('confidence', NOT_CHECKED)
    action = conf.get('action', 'STAY FLAT')
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    t += f"<b>🎯 DECISION</b>\n"
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    t += f"Signal: <b>{sig}</b>\n"
    t += f"Confidence: <b>{cfd}</b>\n"
    t += f"Rally: {conf.get('rally_score', '?')}/9 | Crash: {conf.get('crash_score', '?')}/9\n\n"
    t += f"<b>Action:</b> {action}\n"

    # Stop/targets from primary scenario if available
    primary_name = str(scen.get('primary', '')).lower()
    raw_scen = scen.get('scenarios', [])
    if isinstance(raw_scen, list):
        for s in raw_scen:
            if not isinstance(s, dict):
                continue
            if primary_name and primary_name in str(s.get('type', '')).lower():
                pr = s.get('price_range', [])
                if isinstance(pr, list) and len(pr) >= 2:
                    t += f"<b>Primary range:</b> ${pr[0]:.4f} - ${pr[1]:.4f}\n"
                break
    t += "\n"

    # MICRO (4-24h)
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    t += f"<b>⚡ MICRO (4-24h)</b> <i>· noise, not decision</i>\n"
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    # 1h effort/CVD
    _micro_added = False
    for tf, r in (eff.get('timeframes') or {}).items():
        if str(tf).lower() in ('1h', '15m', '30m') and r.get('signal') != 'NEUTRAL':
            t += f"Effort {tf}: {r.get('signal', NOT_CHECKED)}\n"
            _micro_added = True
            break
    for tf, r in (cvd.get('timeframes') or {}).items():
        if str(tf).lower() in ('1h', '15m', '30m') and r.get('signal') != 'NEUTRAL':
            t += f"CVD {tf}: {r.get('signal', NOT_CHECKED)}\n"
            _micro_added = True
            break
    rsi = tech.get('rsi')
    if rsi is not None:
        t += f"RSI: {rsi:.0f}\n"
        _micro_added = True
    if not _micro_added:
        t += f"{NOT_CHECKED}\n"
    t += "\n"

    # SWING (3-14d)
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    t += f"<b>⚖ SWING (3-14d)</b> <i>· tactical</i>\n"
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    _swing_added = False
    if cex.get('signal'):
        m = cex.get('metrics', {})
        net = m.get('net_flow_pct_supply_7d')
        if net is not None:
            t += f"CEX 7d: {cex.get('signal')} ({net:+.2f}% supply)\n"
        else:
            t += f"CEX 7d: {cex.get('signal')}\n"
        _swing_added = True
    alpha = ((ct.get('strk_alpha') or {}).get('alpha_7d_pct'))
    if alpha is not None:
        t += f"STRK alpha vs L2 (7d): {alpha:+.1f}%\n"
        _swing_added = True
    for tf, r in (eff.get('timeframes') or {}).items():
        if str(tf).lower() in ('4h', '1d') and r.get('signal') != 'NEUTRAL':
            t += f"Effort {tf}: {r.get('signal', NOT_CHECKED)}\n"
            _swing_added = True
            break
    if not _swing_added:
        t += f"{NOT_CHECKED}\n"
    t += "\n"

    # FUNDAMENTAL 1 line
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    t += f"<b>🌐 FUNDAMENTAL</b> <i>· 30-90d thesis</i>\n"
    t += f"━━━━━━━━━━━━━━━━━━━\n"
    days_u = cal.get('days_to_next_unlock')
    amt_u = ((cal.get('upcoming_unlocks') or [{}])[0]).get('amount', 0) if cal.get('upcoming_unlocks') else 0
    if days_u is not None:
        u_str = f"Unlock: {days_u}d"
        if amt_u:
            u_str += f" ({amt_u/1e6:.0f}M STRK)"
        t += u_str + "\n"
    else:
        t += f"Unlock: {NOT_CHECKED}\n"
    if stak.get('net_flow_7d_strk') is not None:
        t += f"Staking 7d: {stak['net_flow_7d_strk']/1e6:+.1f}M STRK\n"
    t += "\n"

    t += "<i>→ workflow_dispatch mode=run для полного контекста (3 сообщения + HTML)</i>"

    return truncate(t)


# ============================================================
# RUN MODE — 3 сообщения
# ============================================================
def format_run_telegram():
    """Возвращает list of 3 strings — три RUN-сообщения."""
    now = datetime.now(timezone.utc)
    ts = now.strftime('%Y-%m-%d %H:%M UTC')
    conf = load_json('confluence_gate.json') or {}
    wyk = load_json('wyckoff_phase.json') or {}
    tech_full = load_json('technical_momentum.json') or {}
    tech = tech_full.get('features') or {}
    cex = load_json('cex_flow.json') or {}
    eff = load_json('effort_result.json') or {}
    cvd = load_json('cvd_analysis.json') or {}
    ct = load_json('cross_token_correlation.json') or {}
    cal = load_json('event_calendar.json') or {}
    stak = load_json('native_staking_flow.json') or {}
    scen = load_json('scenario_analysis.json') or {}
    interp = (load_json('interpretation.json') or {}).get('interpretation') or {}
    conc = load_json('concentration_metrics.json') or {}
    cohorts = load_json('cohort_tracker.json') or {}
    macro = load_json('agent_input.json') or {}
    event_l = load_json('event_layer.json') or {}
    composite = load_json('composite_signal_v2.json') or {}
    news = load_json('news_aggregator.json') or {}
    liq_shift = load_json('liquidity_shift.json') or {}
    bridge = load_json('bridge_activity.json') or {}

    # =========================
    # MSG 1/3: DECISION + gates + invalidation + watch 72h
    # =========================
    m1 = f"<b>📊 RUN · 1/3</b> <i>{ts}</i>\n\n"
    m1 += f"━━━━━━━━━━━━━━━━━━━\n"
    m1 += f"<b>🎯 DECISION</b>\n"
    m1 += f"━━━━━━━━━━━━━━━━━━━\n"
    m1 += f"Signal: <b>{conf.get('signal', NOT_CHECKED)}</b>\n"
    m1 += f"Confidence: <b>{conf.get('confidence', NOT_CHECKED)}</b>\n"
    m1 += f"<i>{conf.get('summary', '')}</i>\n\n"
    m1 += f"<b>Action:</b> {conf.get('action', 'STAY FLAT')}\n\n"

    m1 += f"<b>Rally checks:</b> {conf.get('rally_score', '?')}/9\n"
    checks = conf.get('checks', {})
    if isinstance(checks, dict):
        passed = [k for k, v in checks.items() if v]
        if passed:
            m1 += "  ✓ " + "\n  ✓ ".join(passed[:6]) + "\n"
    m1 += f"\n<b>Crash checks:</b> {conf.get('crash_score', '?')}/9\n"

    m1 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m1 += f"<b>⚠ INVALIDATION</b>\n"
    m1 += f"━━━━━━━━━━━━━━━━━━━\n"
    high_14d = tech.get('high_14d', 0)
    low_14d = tech.get('low_14d', 0)
    if high_14d and low_14d:
        m1 += f"· Break < ${low_14d:.4f} → invalidates rally\n"
        m1 += f"· Break > ${high_14d:.4f} → invalidates crash\n"
    else:
        m1 += f"· Price levels {NOT_CHECKED}\n"
    days_u = cal.get('days_to_next_unlock')
    if days_u is not None:
        m1 += f"· Unlock day (day {days_u}) — reset context\n"

    m1 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m1 += f"<b>👁 WATCH 72h</b>\n"
    m1 += f"━━━━━━━━━━━━━━━━━━━\n"
    watch = wyk.get('watch_events', []) or []
    if watch:
        for e in watch[:5]:
            m1 += f"· {e}\n"
    else:
        m1 += f"· {NOT_CHECKED}\n"

    # =========================
    # MSG 2/3: structure + TA + MICRO + SWING + flow/cohort facts
    # =========================
    m2 = f"<b>📊 RUN · 2/3</b> <i>{ts}</i>\n\n"

    # STRUCTURE
    m2 += f"━━━━━━━━━━━━━━━━━━━\n"
    m2 += f"<b>📍 STRUCTURE</b>\n"
    m2 += f"━━━━━━━━━━━━━━━━━━━\n"
    m2 += f"Phase: {wyk.get('phase', NOT_CHECKED)} · {wyk.get('sub_phase', '—')}\n"
    m2 += f"Wyckoff conf: {wyk.get('confidence', NOT_CHECKED)}\n"
    m2 += f"Regime: {wyk.get('regime', NOT_CHECKED)}\n"
    m2 += f"BTC cycle: {wyk.get('btc_cycle', NOT_CHECKED)}\n"
    price = tech.get('price') or tech.get('price_now')
    if price:
        m2 += f"Price: <b>${price:.4f}</b>\n"
    if tech.get('rsi') is not None:
        m2 += f"RSI: {tech['rsi']:.0f}\n"
    if tech.get('slope_3d_pct') is not None:
        m2 += f"Slope 3d: {tech['slope_3d_pct']:+.2f}%\n"
    if tech.get('vol_ratio_3d_vs_30d') is not None:
        m2 += f"Volume: {tech['vol_ratio_3d_vs_30d']:.2f}× avg\n"

    # MICRO
    m2 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m2 += f"<b>⚡ MICRO (4-24h)</b> <i>· noise, not decision</i>\n"
    m2 += f"━━━━━━━━━━━━━━━━━━━\n"
    _micro = []
    for tf, r in (eff.get('timeframes') or {}).items():
        if str(tf).lower() in ('1h', '15m', '30m') and r.get('signal') != 'NEUTRAL':
            _micro.append(f"Effort {tf}: {r.get('signal')}")
    for tf, r in (cvd.get('timeframes') or {}).items():
        if str(tf).lower() in ('1h', '15m', '30m') and r.get('signal') != 'NEUTRAL':
            _micro.append(f"CVD {tf}: {r.get('signal')}")
    if _micro:
        m2 += "\n".join(_micro[:4]) + "\n"
    else:
        m2 += f"{NOT_CHECKED}\n"

    # SWING
    m2 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m2 += f"<b>⚖ SWING (3-14d)</b> <i>· tactical context</i>\n"
    m2 += f"━━━━━━━━━━━━━━━━━━━\n"
    _swing = []
    for tf, r in (eff.get('timeframes') or {}).items():
        if str(tf).lower() in ('4h', '1d') and r.get('signal') != 'NEUTRAL':
            _swing.append(f"Effort {tf}: {r.get('signal')}")
    for tf, r in (cvd.get('timeframes') or {}).items():
        if str(tf).lower() in ('4h', '1d') and r.get('signal') != 'NEUTRAL':
            _swing.append(f"CVD {tf}: {r.get('signal')}")
    if cex.get('signal') and cex.get('signal') != 'NEUTRAL':
        cex_m = cex.get('metrics', {})
        net_p = cex_m.get('net_flow_pct_supply_7d')
        if net_p is not None:
            _swing.append(f"CEX 7d: {cex.get('signal')} ({net_p:+.2f}% supply)")
        else:
            _swing.append(f"CEX 7d: {cex.get('signal')}")
    alpha = ((ct.get('strk_alpha') or {}).get('alpha_7d_pct'))
    if alpha is not None:
        _swing.append(f"STRK alpha 7d: {alpha:+.1f}% vs L2 sector")
    if bridge.get('signal') or (bridge.get('classification') or {}).get('signal'):
        b_sig = (bridge.get('classification') or {}).get('signal') or bridge.get('signal')
        _swing.append(f"Bridge: {b_sig}")
    if _swing:
        m2 += "\n".join(_swing[:6]) + "\n"
    else:
        m2 += f"{NOT_CHECKED}\n"

    # COHORT
    m2 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m2 += f"<b>👥 COHORT (24h)</b>\n"
    m2 += f"━━━━━━━━━━━━━━━━━━━\n"
    coh_data = cohorts.get('cohorts', {})
    if coh_data:
        for name, info in list(coh_data.items())[:4]:
            if info.get('status') == 'no_data':
                continue
            net = info.get('net_24h_strk', 0)
            dirn = info.get('direction', '—')
            arrow = '↗' if 'INFLOW' in dirn else ('↘' if 'OUTFLOW' in dirn else '→')
            m2 += f"{name.replace('_', ' ').title()}: {arrow} {net:+,.0f} STRK\n"
    else:
        m2 += f"{NOT_CHECKED}\n"

    # =========================
    # MSG 3/3: FUNDAMENTAL + SCENARIOS + MODEL HONESTY
    # =========================
    m3 = f"<b>📊 RUN · 3/3</b> <i>{ts}</i>\n\n"

    # FUNDAMENTAL
    m3 += f"━━━━━━━━━━━━━━━━━━━\n"
    m3 += f"<b>🌐 FUNDAMENTAL</b> <i>· 30-90d thesis health</i>\n"
    m3 += f"━━━━━━━━━━━━━━━━━━━\n"
    # Unlock
    if days_u is not None:
        u_line = f"Unlock: {days_u} days"
        if cal.get('upcoming_unlocks'):
            amt = cal['upcoming_unlocks'][0].get('amount', 0)
            if amt:
                u_line += f" ({amt/1e6:.0f}M STRK)"
        m3 += u_line + "\n"
    else:
        m3 += f"Unlock: {NOT_CHECKED}\n"
    # Staking
    if stak.get('net_flow_7d_strk') is not None:
        m3 += f"Staking flow 7d: {stak['net_flow_7d_strk']/1e6:+.1f}M STRK\n"
    else:
        m3 += f"Staking: {NOT_CHECKED}\n"
    # Concentration
    if conc.get('hhi'):
        m3 += f"Concentration HHI: {conc['hhi']:.4f}\n"
    # Bridge activity
    b_sig = (bridge.get('classification') or {}).get('signal') or bridge.get('signal', NOT_CHECKED)
    m3 += f"Bridge activity: {b_sig}\n"
    # News/thesis
    if news.get('overall_signal'):
        m3 += f"News sentiment: {news['overall_signal']}\n"

    # SCENARIOS
    m3 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m3 += f"<b>🎯 SCENARIOS (7-14d)</b>\n"
    m3 += f"━━━━━━━━━━━━━━━━━━━\n"
    try:
        raw_scen = scen.get('scenarios', [])
        primary_str = str(scen.get('primary', '')).upper()
        scen_dict = {}
        if isinstance(raw_scen, list):
            for s in raw_scen:
                if not isinstance(s, dict):
                    continue
                name = str(s.get('type') or s.get('name') or s.get('label') or '').upper()
                if 'BULL' in name:
                    scen_dict['BULL'] = s
                elif 'BEAR' in name:
                    scen_dict['BEAR'] = s
                elif 'BASE' in name or 'NEUTRAL' in name:
                    scen_dict['BASE'] = s
        elif isinstance(raw_scen, dict):
            for k, v in raw_scen.items():
                if isinstance(v, dict):
                    scen_dict[str(k).upper()] = v
        for n in ['BULL', 'BASE', 'BEAR']:
            s = scen_dict.get(n)
            if not s:
                continue
            emoji = {'BULL': '🟢', 'BASE': '⚪', 'BEAR': '🔴'}[n]
            prob = s.get('probability_pct') or (s.get('probability', 0) * 100)
            pr = s.get('price_range', [])
            range_str = ''
            if isinstance(pr, list) and len(pr) >= 2:
                range_str = f' → ${pr[0]:.4f}-${pr[1]:.4f}'
            elif isinstance(pr, dict):
                range_str = f' → ${pr.get("low", 0):.4f}-${pr.get("high", 0):.4f}'
            is_p = ' ⭐ PRIMARY' if n in primary_str else ''
            m3 += f"{emoji} <b>{n}</b> ({prob:.0f}%){is_p}{range_str}\n"
    except Exception as e:
        m3 += f"{NOT_CHECKED} ({e})\n"

    # MODEL HONESTY
    m3 += f"\n━━━━━━━━━━━━━━━━━━━\n"
    m3 += f"<b>📋 MODEL HONESTY</b>\n"
    m3 += f"━━━━━━━━━━━━━━━━━━━\n"
    m3 += f"Baseline composite v2: 66.7% on 9 events\n"
    m3 += f"Shadow voters: awaiting N=15 for calibration\n"
    m3 += f"Sub-phases: {NOT_CHECKED} (not calibrated)\n"
    m3 += f"\n<i>Full HTML report attached below.</i>"

    # Truncate each to Telegram limit
    return [truncate(m1), truncate(m2), truncate(m3)]


def main():
    """Route via MODE env var: digest (default) | liq | run"""
    mode = (os.environ.get('MODE') or 'digest').lower().strip()
    logger.info("=" * 60)
    logger.info(f"STRK ENGINE · Telegram Delivery · MODE={mode}")
    logger.info("=" * 60)

    if mode == 'liq':
        # LIQ: 1 сообщение (при overflow — обрезка до 4096)
        text = format_liq()
        logger.info(f"LIQ built (length {len(text)})")
        sent = send_telegram(text)
        _log_alert(event_type="liq", text=text, sent=sent)
        if sent:
            logger.info("LIQ sent to Telegram")
        return 0 if sent else 1

    if mode == 'run':
        # RUN: 3 сообщения + HTML документ
        messages = format_run_telegram()
        logger.info(f"RUN built: 3 messages, sizes: {[len(m) for m in messages]}")
        all_sent = True
        import time
        for i, m in enumerate(messages, 1):
            sent = send_telegram(m)
            _log_alert(event_type=f"run_msg_{i}_of_3", text=m, sent=sent)
            if sent:
                logger.info(f"RUN {i}/3 sent")
            else:
                logger.error(f"RUN {i}/3 FAILED")
                all_sent = False
            # Небольшой gap между сообщениями чтобы не поймать rate limit
            if i < len(messages):
                time.sleep(1)

        # HTML документ (опционально)
        report_dir = SCRIPT_DIR / 'data' / 'reports'
        if report_dir.exists():
            # Ищем самый свежий STRK_RUN_*.html
            candidates = sorted(report_dir.glob('STRK_RUN_*.html'), key=lambda p: p.stat().st_mtime, reverse=True)
            # Preference: latest.html если есть
            latest_named = report_dir / 'STRK_RUN_latest.html'
            html_path = latest_named if latest_named.exists() else (candidates[0] if candidates else None)
            if html_path:
                doc_sent = send_telegram_document(
                    html_path,
                    caption=f"📄 STRK RUN report · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · archive, not a substitute"
                )
                _log_alert(event_type="run_html_doc", text=str(html_path), sent=doc_sent)
                if doc_sent:
                    logger.info(f"HTML doc sent: {html_path.name}")
            else:
                logger.warning("No STRK_RUN_*.html found for attachment")

        return 0 if all_sent else 1

    # Default: digest (текущее поведение)
    text = format_digest()
    logger.info(f"Digest built (length {len(text)})")
    sent = send_telegram(text)
    _log_alert(event_type="digest", text=text, sent=sent)
    if sent:
        logger.info("Digest sent to Telegram")
    return 0


if __name__ == '__main__':
    sys.exit(main())