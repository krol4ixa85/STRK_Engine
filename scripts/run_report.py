#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_report.py — Full HTML RUN report generator

Canonical structure (v5.4):
  1. HERO (title + date + version)
  2. DECISION (главное вверху)
  3. LAYMAN «Что происходит» (5 предложений простым языком)
  4. CONFLUENCE GATE (multi-signal decision layer)
  5. МЕСТО / STRUCTURE
  6. WATCHERS
  7. CONFLICT / OFF-CHAIN EVENT LAYER
  8. TRADING MAP (4 сценария: Base/Bull/Bear/Squeeze)
  9. TECHNICAL / ON-CHAIN evidence (evidence под капотом)
  10. PROBABILITY MODULE
  11. FORECAST для FORWARDTEST_LOG

Output: /mnt/user-data/outputs/STRK_RUN_[timestamp].html

single_brain_v1 changes:
  · BLOCK 4 renamed: CONFLUENCE GATE -> DECISION · single source of truth
  · Hero disclaimer: HIGH signal -> open LIQ before action
  · Layman verdict: 4 variants get "action = DECISION block below" footer
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_DIR = SCRIPT_DIR / 'data' / 'reports'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('report')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def safe_get(d, path, default='—'):
    """Safe nested dict access."""
    if not d:
        return default
    for key in path.split('.'):
        if isinstance(d, dict):
            d = d.get(key)
        else:
            return default
        if d is None:
            return default
    return d if d is not None else default


def build_html():
    """Build full HTML RUN report."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y-%m-%d %H:%M UTC')
    
    # Load all data
    wyckoff = load_json('wyckoff_phase.json') or {}
    tech = load_json('technical_momentum.json') or {}
    funding = load_json('funding_signal.json') or {}
    cex_flow = load_json('cex_flow.json') or {}
    concentration = load_json('concentration_metrics.json') or {}
    effort = load_json('effort_result.json') or {}
    cvd = load_json('cvd_analysis.json') or {}
    event_layer = load_json('event_layer.json') or {}
    calendar = load_json('event_calendar.json') or {}
    bridge = load_json('bridge_activity.json') or {}
    cross_token = load_json('cross_token_correlation.json') or {}
    confluence = load_json('confluence_gate.json') or {}
    scenarios_data = load_json('scenario_analysis.json') or {}
    composite = load_json('composite_signal_v2.json') or {}
    macro = load_json('agent_input.json') or {}
    news = load_json('news_aggregator.json') or {}
    sn_discord = load_json('starknet_discord.json') or {}
    
    # === Extract key values ===
    phase = wyckoff.get('phase', 'UNKNOWN')
    sub_phase = wyckoff.get('sub_phase', '—')
    wyckoff_conf = wyckoff.get('confidence', 'UNKNOWN')
    
    tech_features = tech.get('features', {})
    price_now = tech_features.get('price', 0)
    slope_3d = tech_features.get('slope_3d_pct', 0)
    vol_ratio = tech_features.get('vol_ratio_3d_vs_30d', 1)
    rsi = tech_features.get('rsi', 50)
    high_14d = tech_features.get('high_14d', 0)
    low_14d = tech_features.get('low_14d', 0)
    high_7d = tech_features.get('high_7d', high_14d)
    low_7d = tech_features.get('low_7d', low_14d)
    pct_from_high = tech_features.get('pct_from_high', 0)
    pct_from_low = tech_features.get('pct_from_low', 0)
    
    # Confluence decision
    conf_signal = confluence.get('signal', 'NO_SIGNAL')
    conf_conf = confluence.get('confidence', 'LOW')
    conf_action = confluence.get('action', 'STAY FLAT')
    conf_summary = confluence.get('summary', '')
    rally_score = confluence.get('rally_score', 0)
    crash_score = confluence.get('crash_score', 0)
    checks = confluence.get('checks', {})
    
    # Signal color - HIGH only for confirmed action
    if conf_conf == 'HIGH' and 'RALLY' in conf_signal:
        signal_color = 'green'
        signal_emoji = '🟢'
        header_bg = '0f2a12'
    elif conf_conf == 'HIGH' and 'CRASH' in conf_signal:
        signal_color = 'red'
        signal_emoji = '🔴'
        header_bg = '2a0f0f'
    elif conf_conf == 'MEDIUM':
        signal_color = 'yellow'
        signal_emoji = '🟡'
        header_bg = '2a1a0f'
    else:
        signal_color = 'dim'
        signal_emoji = '⚪'
        header_bg = '12151C'
    
    # Event layer
    ev_signal = event_layer.get('signal', 'CALM')
    ev_bull = event_layer.get('bullish_score', 0)
    ev_bear = event_layer.get('bearish_score', 0)
    
    # BTC context — сначала из composite (авторитетно), fallback на agent_input
    btc_data = macro.get('btc', {}) or {}
    btc_ctx_composite = ((composite.get('inputs') or {}).get('btc_context') or {})
    btc_price = btc_ctx_composite.get('btc_price') or btc_data.get('price') or 0
    btc_cycle = btc_ctx_composite.get('cycle') or btc_data.get('cycle') or 'UNKNOWN'
    btc_dist200 = btc_ctx_composite.get('dist200_pct')
    if btc_dist200 is None:
        btc_dist200 = btc_data.get('dist200_pct', 0)
    
    # Scenarios
    scenarios = scenarios_data.get('scenarios', [])
    
    # Funding
    fm = funding.get('funding_metrics', {})
    funding_apr = fm.get('funding_apr_pct', 0)
    short_crowded = fm.get('short_crowded', False)
    
    # === HTML START ===
    html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>STRK RUN · {timestamp}</title>
<style>
:root{{
  --bg:#0A0C10;--card:#12151C;--border:#1E2330;--text:#E2E8F0;--dim:#64748B;
  --green:#22C55E;--red:#EF4444;--yellow:#EAB308;--blue:#3B82F6;--purple:#A855F7;
  --cyan:#06B6D4;--orange:#F97316;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:'SF Mono','Fira Code',monospace;font-size:13px;padding:18px;max-width:940px;margin:0 auto;line-height:1.5}}
.g{{color:var(--green)}}.r{{color:var(--red)}}.y{{color:var(--yellow)}}.b{{color:var(--blue)}}
.c{{color:var(--cyan)}}.o{{color:var(--orange)}}.dim{{color:var(--dim)}}.p{{color:var(--purple)}}

/* HERO */
.hdr{{background:linear-gradient(135deg,#12151C,#0f1219);border:2px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}}
.hdr-title{{font-size:18px;font-weight:bold;margin-bottom:4px}}
.hdr-sub{{color:var(--dim);font-size:11px}}

/* DECISION (главное) */
.decision{{border:3px solid var(--{signal_color});border-radius:12px;padding:20px;margin-bottom:14px;background:linear-gradient(135deg,#{header_bg},#0f1219)}}
.dec-title{{font-size:16px;font-weight:bold;letter-spacing:2px;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid rgba(255,255,255,.15)}}
.dec-signal{{font-size:22px;font-weight:bold;color:var(--{signal_color});text-align:center;padding:14px 0;letter-spacing:1px}}
.dec-grid{{display:grid;grid-template-columns:160px 1fr;gap:5px 12px;font-size:12px;line-height:1.7;margin-top:12px}}
@media(max-width:640px){{.dec-grid{{grid-template-columns:1fr}}}}
.dec-k{{color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:1px;padding-top:2px}}
.dec-v{{font-weight:bold}}
.dec-action{{background:#0c1015;padding:12px;border-radius:6px;margin-top:12px;border-left:3px solid var(--{signal_color})}}
.dec-action-lbl{{color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:1px;margin-bottom:4px}}
.dec-action-val{{font-size:13px;color:var(--{signal_color});font-weight:bold}}

/* LAYMAN */
.layman{{background:linear-gradient(135deg,#0a1a20,#0f1219);border:2px solid var(--cyan);border-radius:12px;padding:18px;margin-bottom:14px}}
.layman-title{{font-size:15px;font-weight:bold;color:var(--cyan);margin-bottom:10px;letter-spacing:1px}}
.layman-item{{padding:8px 0;border-bottom:1px dashed #1a1f2a;font-size:13px;line-height:1.6}}
.layman-item:last-child{{border:none}}

/* Section base */
.sec{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:12px}}
.sec-title{{font-size:13px;font-weight:bold;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--border);letter-spacing:1px}}

/* Confluence checks */
.check{{display:grid;grid-template-columns:24px 1fr;gap:8px;padding:4px 0;font-size:12px;font-family:'SF Mono',monospace}}
.check-icon{{text-align:center;font-weight:bold}}

/* МЕСТО */
.place{{background:#0a1420;border:2px solid var(--blue);border-radius:10px;padding:14px;margin-bottom:12px}}
.place-title{{color:var(--blue)}}
.place-row{{display:grid;grid-template-columns:140px 1fr;gap:8px;padding:6px 0;border-bottom:1px solid #1a1f2a;font-size:12px}}
.place-row:last-child{{border:none}}
.place-k{{color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:1px;padding-top:2px;font-weight:bold}}

/* Event Layer */
.event{{background:#12100a;border:2px solid var(--orange);border-radius:10px;padding:14px;margin-bottom:12px}}
.event-title{{color:var(--orange)}}
.reason-list{{margin-top:8px}}
.reason{{padding:4px 8px;margin:3px 0;border-radius:4px;font-size:11.5px;line-height:1.4}}
.reason-bull{{background:#0f2a12;border-left:3px solid var(--green)}}
.reason-bear{{background:#2a0f0f;border-left:3px solid var(--red)}}

/* Sector table */
.sector-tbl{{display:grid;grid-template-columns:60px 80px 80px 80px;gap:6px;margin-top:10px;font-size:11px}}
.sector-hdr{{color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:1px;padding:6px;background:#0c1015;border-radius:4px}}
.sector-row{{padding:6px;border-radius:4px}}
.sector-row.up{{background:#0f2a12}}
.sector-row.down{{background:#2a0f0f}}

/* Scenarios */
.scenario{{border-radius:8px;padding:12px;margin-bottom:10px}}
.sc-base{{background:linear-gradient(135deg,#12151C,#0f1219);border:1px solid var(--dim)}}
.sc-bull{{background:linear-gradient(135deg,#0f2a12,#0f1219);border:1px solid var(--green)}}
.sc-bear{{background:linear-gradient(135deg,#2a0f0f,#0f1219);border:1px solid var(--red)}}
.sc-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.sc-name{{font-weight:bold;font-size:14px;letter-spacing:1px}}
.sc-prob{{background:rgba(255,255,255,.1);padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold}}
.sc-range{{color:var(--cyan);margin-bottom:6px;font-size:12px}}
.sc-desc{{font-size:11.5px;line-height:1.5;color:#c2c9d4}}
.sc-triggers{{margin-top:8px;padding-top:8px;border-top:1px dashed #1a1f2a}}
.sc-trg-title{{font-size:10px;color:var(--dim);text-transform:uppercase;margin-bottom:4px}}
.sc-trg{{font-size:11px;padding:2px 0}}

/* Evidence (под капотом) */
.evidence{{background:#0c1015;border:1px solid #1a1f2a;border-radius:6px;padding:10px;margin-bottom:8px}}
.evidence summary{{cursor:pointer;font-weight:bold;color:var(--dim);text-transform:uppercase;font-size:11px;letter-spacing:1px;padding:2px 0}}
.evidence summary:hover{{color:var(--text)}}
.evidence[open] summary{{color:var(--text);margin-bottom:8px}}
.ev-grid{{display:grid;grid-template-columns:130px 1fr;gap:4px 12px;font-size:11.5px;line-height:1.6;padding-top:4px}}
.ev-k{{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:0.5px;padding-top:2px}}

/* Buttons/toolbar */
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0;padding:12px;background:#0c1015;border-radius:8px;border:1px solid var(--border)}}
.btn{{background:#1a1f2a;border:1px solid #2a303f;color:var(--text);padding:8px 14px;border-radius:6px;font-family:inherit;font-size:11px;cursor:pointer;text-decoration:none;display:inline-block;transition:all 0.2s}}
.btn:hover{{background:#2a303f;border-color:var(--cyan)}}
.btn-run{{border-color:var(--green);color:var(--green)}}
.btn-liq{{border-color:var(--orange);color:var(--orange)}}
.btn-scenario{{border-color:var(--purple);color:var(--purple)}}
.btn-review{{border-color:var(--blue);color:var(--blue)}}

/* Watchers */
.w-summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}}
.w-count{{text-align:center;padding:10px;border-radius:6px;background:#0c1015}}
.w-count-num{{font-size:22px;font-weight:bold}}
.w-count-lbl{{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-top:2px}}

/* Forecast block */
.forecast{{background:linear-gradient(135deg,#12100a,#0f1219);border:2px solid var(--purple);border-radius:10px;padding:14px;margin-bottom:12px}}
.fc-title{{color:var(--purple)}}
.fc-code{{font-family:monospace;background:#0c1015;padding:10px;border-radius:6px;font-size:11px;line-height:1.5;white-space:pre-wrap;color:var(--cyan)}}

/* MODE PANEL */
.mode-panel{{margin-top:20px;padding:12px 16px;background:#0c1015;border:1px solid var(--border);border-radius:8px;font-size:11px;color:var(--dim);text-align:center;letter-spacing:1px}}
.mode-panel b{{color:var(--cyan)}}
</style>
</head>
<body>

<!-- ============ BLOCK 1: HERO ============ -->
<div class="hdr">
  <div class="hdr-title">STRK ENGINE · RUN REPORT</div>
  <div class="hdr-sub">{timestamp} · v4.7 · engine v6 · position: <b class="c">FLAT</b></div>
</div>

<!-- ============ BLOCK 2: DECISION (главное вверху) ============ -->
<div class="decision">
  <div class="dec-title">⚡ DECISION LAYER</div>
  <div class="dec-signal">{signal_emoji} {conf_signal}</div>
  
  <div class="dec-grid">
    <div class="dec-k">Signal</div><div class="dec-v">{conf_signal}</div>
    <div class="dec-k">Confidence</div><div class="dec-v">{conf_conf}</div>
    <div class="dec-k">Rally checks</div><div class="dec-v">{rally_score}/6+</div>
    <div class="dec-k">Crash checks</div><div class="dec-v">{crash_score}/6+</div>
    <div class="dec-k">Off-chain</div><div class="dec-v">{ev_signal} ({ev_bull} vs {ev_bear})</div>
    <div class="dec-k">Wyckoff phase</div><div class="dec-v">{phase} · {sub_phase}</div>
    <div class="dec-k">BTC cycle</div><div class="dec-v">{btc_cycle} · dist200 {btc_dist200:+.1f}%</div>
    <div class="dec-k">Position</div><div class="dec-v"><span class="c">FLAT</span></div>
  </div>
  
  <div class="dec-action">
    <div class="dec-action-lbl">Action</div>
    <div class="dec-action-val">{conf_action}</div>
  </div>
</div>

<!-- ============ TOOLBAR (кнопки/actions) ============ -->
<div class="toolbar">
  <a class="btn btn-run" href="#run">📊 RUN details</a>
  <a class="btn btn-scenario" href="#scenarios">🎯 SCENARIOS</a>
  <a class="btn btn-liq" href="#watchers">👁 WATCHERS</a>
  <a class="btn btn-review" href="#evidence">🔬 EVIDENCE</a>
  <a class="btn" href="#forecast">📝 FORECAST</a>
</div>

<!-- ============ BLOCK 3: LAYMAN «Что происходит» ============ -->
<div class="layman" id="layman">
  <div class="layman-title">🧠 ЧТО ПРОИСХОДИТ (простым языком)</div>
  {_build_layman(phase, sub_phase, conf_signal, ev_signal, price_now, high_14d, low_14d, cross_token, calendar, short_crowded, funding_apr)}
</div>

<!-- ============ BLOCK 4: DECISION (single source of truth) ============ -->
<div class="sec" id="run">
  <div class="sec-title">🎯 DECISION · single source of truth (6 checks across 3 layers)</div>
  <div style="margin-bottom:8px;padding:8px;background:#2a1f0f;border-left:3px solid var(--yellow);font-size:11px;color:var(--yellow)">
    ⚠ Любой HIGH signal → сначала открой полный LIQ отчёт, не мгновенное buy/sell.<br>
    Все другие блоки в этом отчёте — evidence base, не независимые вердикты.
  </div>
  <div style="margin-bottom:10px;color:var(--dim);font-size:11.5px">{conf_summary}</div>
  
  <div style="margin-top:12px">
    <b style="font-size:12px">Rally checks ({rally_score}/6):</b>
    {_build_checks(checks, 'rally')}
  </div>
</div>

<!-- ============ BLOCK 5: МЕСТО / STRUCTURE ============ -->
<div class="place">
  <div class="sec-title place-title">📍 STRUCTURE / МЕСТО</div>
  <div class="place-row"><div class="place-k">Price</div><div><b class="c">${price_now:.4f}</b></div></div>
  <div class="place-row"><div class="place-k">Phase (Wyckoff)</div><div>{phase} · <span class="dim">{sub_phase}</span></div></div>
  <div class="place-row"><div class="place-k">Wyckoff conf.</div><div class="{"g" if wyckoff_conf=="HIGH" else "y"}">{wyckoff_conf}</div></div>
  <div class="place-row"><div class="place-k">RSI 14</div><div>{rsi:.0f}</div></div>
  <div class="place-row"><div class="place-k">Slope 3d</div><div class="{"g" if slope_3d>0 else "r"}">{slope_3d:+.2f}%</div></div>
  <div class="place-row"><div class="place-k">Volume 3d</div><div>{vol_ratio:.2f}× avg</div></div>
  <div class="place-row"><div class="place-k">14d range</div><div>${low_14d:.4f} → ${high_14d:.4f}</div></div>
  <div class="place-row"><div class="place-k">7d range</div><div>${low_7d:.4f} → ${high_7d:.4f}</div></div>
  <div class="place-row"><div class="place-k">Distance from high</div><div class="r">{pct_from_high:+.2f}%</div></div>
  <div class="place-row"><div class="place-k">Distance from low</div><div class="g">{pct_from_low:+.2f}%</div></div>
</div>

<!-- ============ BLOCK 6: EVENT LAYER (off-chain) ============ -->
<div class="event">
  <div class="sec-title event-title">📅 OFF-CHAIN EVENT LAYER</div>
  <div style="margin-bottom:8px">
    <b>Signal:</b> <span class="{"r" if "NEGATIVE" in ev_signal else "g" if "POSITIVE" in ev_signal else "y"}">{ev_signal}</span> · 
    Bull {ev_bull} vs Bear {ev_bear}
  </div>
  
  {_build_reasons(event_layer.get('reasons', {}))}
  
  {_build_sector_table(cross_token)}
  
  {_build_calendar(calendar)}
</div>

<!-- ============ BLOCK 7: SCENARIOS (Base/Bull/Bear) ============ -->
<div class="sec" id="scenarios">
  <div class="sec-title">🎯 SCENARIOS (Base / Bull / Bear)</div>
  <div style="color:var(--dim);font-size:11px;margin-bottom:12px">Valid until: {scenarios_data.get('valid_until', 'end of week')[:10]}</div>
  
  {_build_scenarios(scenarios)}
</div>

<!-- ============ BLOCK 8: WATCHERS ============ -->
<div class="sec" id="watchers">
  <div class="sec-title">👁 WATCHERS</div>
  <div class="w-summary">
    <div class="w-count"><div class="w-count-num c">4</div><div class="w-count-lbl">Long triggers</div></div>
    <div class="w-count"><div class="w-count-num r">4</div><div class="w-count-lbl">Short triggers</div></div>
    <div class="w-count"><div class="w-count-num g">${high_14d:.4f}</div><div class="w-count-lbl">Break UP</div></div>
    <div class="w-count"><div class="w-count-num r">${low_14d:.4f}</div><div class="w-count-lbl">Break DOWN</div></div>
  </div>
  
  <div style="font-size:11.5px;line-height:1.7;color:#c2c9d4">
    <b>LONG triggers</b> (все должны сработать):<br>
    · Break ABOVE ${high_14d:.4f} on volume 1.5×+<br>
    · CEX flow flips to OUTFLOW (3d consecutive)<br>
    · Event Layer improves to SLIGHT_BULLISH+<br>
    · BTC breaks $70k (up-cycle confirmation)<br>
    <br>
    <b>SHORT triggers</b> (unlock scenario):<br>
    · Break BELOW ${low_14d:.4f} on volume<br>
    · Unlock event at day 9 (2026-08-15)<br>
    · L2 sector rotation continues OUT<br>
    · Distribution accelerates (top 5 &gt; 45%)
  </div>
</div>

<!-- ============ BLOCK 9: EVIDENCE (под капотом) ============ -->
<div id="evidence">
  <div class="sec-title" style="border:none;padding:12px 0;font-size:15px;color:var(--dim)">🔬 EVIDENCE (под капотом)</div>
  
  <details class="evidence">
    <summary>🔗 ON-CHAIN evidence · HHI, Entropy, Concentration</summary>
    <div class="ev-grid">
      <div class="ev-k">HHI concentration</div><div>{concentration.get('hhi', 0):.4f} <span class="dim">(diluted → distribution)</span></div>
      <div class="ev-k">Shannon entropy</div><div>{concentration.get('shannon_entropy_bits', 0):.2f} bits</div>
      <div class="ev-k">Gini coefficient</div><div>{concentration.get('gini', 0):.3f}</div>
      <div class="ev-k">LARGE receivers 14d</div><div>{concentration.get('large_receivers_14d', 0)}</div>
      <div class="ev-k">Top 5 share</div><div>{concentration.get('top_5_share_pct', 0):.1f}%</div>
      <div class="ev-k">Signal</div><div class="r">DISTRIBUTION_SHAPE</div>
    </div>
  </details>
  
  <details class="evidence">
    <summary>🏦 CEX FLOW evidence · 7d directional</summary>
    <div class="ev-grid">
      <div class="ev-k">Signal</div><div class="r"><b>{safe_get(cex_flow, "classification.signal")}</b></div>
      <div class="ev-k">Net 7d</div><div>{cex_flow.get('net_flow_strk', 0)/1e6:+.1f}M STRK</div>
      <div class="ev-k">Inflow 7d</div><div>{cex_flow.get('total_inflow_strk', 0)/1e6:.1f}M STRK</div>
      <div class="ev-k">Outflow 7d</div><div>{cex_flow.get('total_outflow_strk', 0)/1e6:.1f}M STRK</div>
      <div class="ev-k">Consecutive days</div><div>{cex_flow.get('consecutive_inflow_days', 0)} inflow</div>
    </div>
  </details>
  
  <details class="evidence">
    <summary>⚖ EFFORT/RESULT + CVD divergence</summary>
    <div class="ev-grid">
      <div class="ev-k">Effort/Result</div><div>{safe_get(effort, "signal", "N/A")}</div>
      <div class="ev-k">Interpretation</div><div class="dim">{safe_get(effort, "interpretation", "")[:100]}</div>
      <div class="ev-k">CVD signal</div><div>{safe_get(cvd, "signal", "N/A")}</div>
      <div class="ev-k">CVD note</div><div class="dim">{safe_get(cvd, "interpretation", "")[:100]}</div>
    </div>
  </details>
  
  <details class="evidence">
    <summary>📈 DERIVATIVES · funding, OI</summary>
    <div class="ev-grid">
      <div class="ev-k">Funding annualized</div><div>{funding_apr:+.2f}%</div>
      <div class="ev-k">Short crowded</div><div>{"🔴 YES" if short_crowded else "no"}</div>
      <div class="ev-k">3d neg funding</div><div>{fm.get('neg_funding_pct_3d', 0):.0f}%</div>
      <div class="ev-k">7d min funding</div><div>{fm.get('funding_min_7d_pct', 0):+.1f}%</div>
      <div class="ev-k">Squeeze fuel</div><div class="y">Present (+5-15% possible)</div>
    </div>
  </details>
  
  <details class="evidence">
    <summary>🌐 MACRO context · BTC cycle</summary>
    <div class="ev-grid">
      <div class="ev-k">BTC price</div><div>${btc_price:,.0f}</div>
      <div class="ev-k">Cycle</div><div class="{"r" if btc_cycle=="DOWN" else "g"}">{btc_cycle}</div>
      <div class="ev-k">Distance from MA200</div><div>{btc_dist200:+.1f}%</div>
      <div class="ev-k">Interpretation</div><div class="dim">No tailwind for STRK</div>
    </div>
  </details>
  
  <details class="evidence">
    <summary>📰 NEWS + Discord + Twitter · off-chain sources</summary>
    <div class="ev-grid">
      <div class="ev-k">News signal</div><div>{safe_get(news, "overall_signal", "N/A")}</div>
      <div class="ev-k">STRK news 7d</div><div>{news.get('strk_news_count', 0)}</div>
      <div class="ev-k">Discord signal</div><div>{safe_get(sn_discord, "signal", "N/A")}</div>
      <div class="ev-k">Bridge activity</div><div>{safe_get(bridge, "classification.signal", "N/A")}</div>
      <div class="ev-k">GitHub dev</div><div>{safe_get(load_json("github_activity.json"), "classification.signal", "N/A")}</div>
    </div>
  </details>
</div>

<!-- ============ BLOCK 10: FORECAST для FORWARDTEST_LOG ============ -->
<div class="forecast" id="forecast">
  <div class="sec-title fc-title">📝 FORECAST для FORWARDTEST_LOG</div>
  <div class="fc-code">## FORECAST_{now.strftime('%Y%m%d_%H%M')}
- **verify_after:** {(datetime.now(timezone.utc).replace(hour=23, minute=59)).isoformat()[:16]} + 72h
- **phase_now:** {phase} · {sub_phase}
- **price_now:** ${price_now:.4f}
- **confluence_signal:** {conf_signal}
- **rally_score:** {rally_score}/6+
- **crash_score:** {crash_score}/6+
- **event_layer:** {ev_signal} (B{ev_bull}/Br{ev_bear})
- **btc_cycle:** {btc_cycle}
- **expected_range_72h:** {_get_scenario_range(scenarios, 0, low_14d, high_14d)}
- **base_case_prob:** {scenarios[0].get('probability_pct', 65) if scenarios else 65}%
- **bull_case:** {_get_scenario_range(scenarios, 1, high_14d, high_14d*1.3)} ({scenarios[1].get('probability_pct', 15) if len(scenarios)>1 else 15}%)
- **bear_case:** {_get_scenario_range(scenarios, 2, low_14d*0.85, low_14d)} ({scenarios[2].get('probability_pct', 20) if len(scenarios)>2 else 20}%)
- **verdict:** {conf_action}
- **status:** PENDING</div>
</div>

<!-- ============ MODE PANEL ============ -->
<div class="mode-panel">
  <b>[ MODE ✓ ]</b> RUN · LIQ · REVIEW · DEV · SCENARIO — доступны
</div>

</body>
</html>'''
    
    return html


def _build_layman(phase, sub_phase, conf_signal, ev_signal, price, high_14d, low_14d, cross_token, calendar, short_crowded, funding_apr):
    """Build layman explanation - 5 sentences."""
    ct_signal = cross_token.get('signal', 'NEUTRAL')
    alpha_7d = cross_token.get('strk_alpha', {}).get('alpha_7d_pct', 0)
    days_to_unlock = calendar.get('days_to_next_unlock', 999)
    
    sentences = []
    
    # 1. Phase state
    if phase == 'ACCUMULATION':
        sentences.append(f"<b>Где мы:</b> STRK в фазе <b>НАКОПЛЕНИЯ</b> ({sub_phase}). Крупные игроки собирают токены у розницы. Ничего кричащего.")
    elif phase == 'MARKUP':
        sentences.append(f"<b>Где мы:</b> STRK в фазе <b>MARKUP</b> — цена растёт с распространением интереса.")
    elif phase == 'DISTRIBUTION':
        sentences.append(f"<b>Где мы:</b> STRK в фазе <b>РАСПРЕДЕЛЕНИЯ</b> — крупные продают розничным.")
    else:
        sentences.append(f"<b>Где мы:</b> Wyckoff phase = {phase}.")
    
    # 2. Sector context
    if ct_signal == 'STRK_UNDERPERFORMING':
        sentences.append(f"<b>L2 сектор:</b> STRK <b>-{abs(alpha_7d):.1f}%</b> хуже сектора за неделю — <b class='r'>деньги уходят из STRK специфически</b>. ARB и OP стабилизируются, а STRK продолжает падать.")
    elif ct_signal == 'STRK_OUTPERFORMING':
        sentences.append(f"<b>L2 сектор:</b> STRK опережает сектор на <b>+{alpha_7d:.1f}%</b> — ротация IN.")
    else:
        sentences.append(f"<b>L2 сектор:</b> STRK движется в линии с сектором.")
    
    # 3. Off-chain
    if ev_signal == 'NEGATIVE_CATALYST':
        sentences.append(f"<b>Off-chain:</b> Фундаментально плохо — низкая dev-активность, слабый bridge, unlock через <b>{days_to_unlock} дней</b>.")
    elif ev_signal == 'POSITIVE_CATALYST':
        sentences.append(f"<b>Off-chain:</b> Фундаментально хорошо — активность растёт, positive catalysts впереди.")
    else:
        sentences.append(f"<b>Off-chain:</b> Смешанные сигналы. Ближайший unlock — через {days_to_unlock} дней.")
    
    # 4. Technical
    if short_crowded:
        sentences.append(f"<b>Технически:</b> Много шортов в системе (funding {funding_apr:+.1f}% ann) — есть <b class='y'>топливо для short squeeze +5-15%</b>, но это НЕ разворот тренда.")
    else:
        sentences.append(f"<b>Технически:</b> Funding rate {funding_apr:+.1f}% ann — нормальные условия.")
    
    # 5. Verdict (single_brain: все варианты ссылаются на DECISION блок)
    if 'HIGH' in conf_signal and 'RALLY' in conf_signal:
        sentences.append(f"<b>Вердикт:</b> <b class='g'>СИГНАЛ НА LONG</b> — {sum([1 for s in sentences if s])} независимых checks согласны. <i>Финальное действие — блок DECISION ниже.</i>")
    elif 'HIGH' in conf_signal and 'CRASH' in conf_signal:
        sentences.append(f"<b>Вердикт:</b> <b class='r'>СИГНАЛ НА SHORT/REDUCE</b> — множественное подтверждение. <i>Финальное действие — блок DECISION ниже.</i>")
    elif 'MEDIUM' in conf_signal:
        sentences.append(f"<b>Вердикт:</b> <b class='y'>ЖДЁМ CONFLUENCE</b> — частичные сигналы, но не хватает подтверждений. <b>Ничего не делаем.</b> <i>См. блок DECISION.</i>")
    else:
        sentences.append(f"<b>Вердикт:</b> <b class='dim'>FLAT</b> — нет чёткой картины. Ждём. <i>См. блок DECISION.</i>")
    
    return ''.join(f'<div class="layman-item">{i+1}. {s}</div>' for i, s in enumerate(sentences))


def _build_checks(checks, kind):
    """Build check list HTML."""
    html_parts = []
    for k, v in checks.items():
        icon = '✓' if v else '✗'
        cls = 'g' if v else 'r'
        html_parts.append(f'<div class="check"><div class="check-icon {cls}">{icon}</div><div class="{cls}">{k}</div></div>')
    return ''.join(html_parts)


def _build_reasons(reasons):
    """Build bullish/bearish reasons."""
    html = '<div class="reason-list">'
    for r in reasons.get('bullish', [])[:4]:
        html += f'<div class="reason reason-bull">✓ {r}</div>'
    for r in reasons.get('bearish', [])[:4]:
        html += f'<div class="reason reason-bear">✗ {r}</div>'
    html += '</div>'
    return html


def _build_sector_table(cross_token):
    """Build L2 sector comparison table."""
    if not cross_token:
        return ''
    perfs = cross_token.get('performances', {})
    if not perfs:
        return ''
    
    html = '<div style="margin-top:14px"><b style="font-size:12px">L2 SECTOR (7d):</b>'
    html += '<div class="sector-tbl">'
    html += '<div class="sector-hdr">Token</div>'
    html += '<div class="sector-hdr">24h</div>'
    html += '<div class="sector-hdr">7d</div>'
    html += '<div class="sector-hdr">30d</div>'
    
    sorted_p = sorted(perfs.items(), key=lambda x: -x[1].get('change_7d_pct', 0))
    for symbol, p in sorted_p:
        is_strk = symbol == 'STRK'
        change_7d = p.get('change_7d_pct', 0)
        cls = 'up' if change_7d > 0 else 'down'
        strk_mark = ' style="font-weight:bold;border:1px solid var(--yellow)"' if is_strk else ''
        html += f'<div class="sector-row {cls}"{strk_mark}>{symbol}</div>'
        html += f'<div class="sector-row {cls}">{p.get("change_1d_pct", 0):+.1f}%</div>'
        html += f'<div class="sector-row {cls}">{change_7d:+.1f}%</div>'
        html += f'<div class="sector-row {cls}">{p.get("change_30d_pct", 0):+.1f}%</div>'
    
    alpha_7d = cross_token.get('strk_alpha', {}).get('alpha_7d_pct', 0)
    alpha_cls = 'r' if alpha_7d < 0 else 'g'
    html += f'</div><div style="margin-top:8px;font-size:12px">STRK alpha 7d: <b class="{alpha_cls}">{alpha_7d:+.1f}%</b></div></div>'
    return html


def _build_calendar(calendar):
    """Build calendar upcoming events."""
    if not calendar:
        return ''
    upcoming = calendar.get('upcoming_unlocks', [])[:2]
    milestones = calendar.get('upcoming_milestones', [])[:2]
    
    html = '<div style="margin-top:14px">'
    if upcoming:
        html += '<b style="font-size:12px">Next unlocks:</b><div style="font-size:11.5px;margin-top:4px">'
        for u in upcoming:
            html += f'· {u["date"]} ({u["days_until"]}d) · <b>{u["amount"]/1e6:.0f}M STRK</b><br>'
        html += '</div>'
    if milestones:
        html += '<b style="font-size:12px;margin-top:8px;display:block">Next milestones:</b><div style="font-size:11.5px;margin-top:4px">'
        for m in milestones:
            html += f'· {m["date"]} ({m["days_until"]}d) · <b class="c">{m["event"]}</b><br>'
        html += '</div>'
    html += '</div>'
    return html


def _get_scenario_range(scenarios, idx, default_low, default_high):
    """Safely get scenario price range."""
    if not scenarios or len(scenarios) <= idx:
        return f'${default_low:.4f} - ${default_high:.4f}'
    pr = scenarios[idx].get('price_range', [default_low, default_high])
    if isinstance(pr, dict):
        return f'${pr.get("low", default_low):.4f} - ${pr.get("high", default_high):.4f}'
    if isinstance(pr, list) and len(pr) >= 2:
        return f'${pr[0]:.4f} - ${pr[1]:.4f}'
    return f'${default_low:.4f} - ${default_high:.4f}'


def _build_scenarios(scenarios):
    """Build scenarios cards."""
    html = ''
    for s in scenarios:
        s_type = s.get('type', 'BASE').lower()
        cls = f'sc-{s_type}'
        
        # price_range is a list [low, high]
        pr = s.get('price_range', [0, 0])
        if isinstance(pr, dict):
            low, high = pr.get('low', 0), pr.get('high', 0)
        elif isinstance(pr, list) and len(pr) >= 2:
            low, high = pr[0], pr[1]
        else:
            low, high = 0, 0
        
        triggers_list = s.get('triggers', []) or []
        if not triggers_list and s.get('catalyst'):
            triggers_list = [s['catalyst']]
        
        html += f'''<div class="scenario {cls}">
          <div class="sc-header">
            <div class="sc-name">{s.get('type', '?')} · {s.get('label', s.get('name', ''))}</div>
            <div class="sc-prob">{s.get('probability_pct', 0)}%</div>
          </div>
          <div class="sc-range">Range: ${low:.4f} - ${high:.4f} · {s.get('timeframe_days', 14)}d</div>
          <div class="sc-desc">{s.get('narrative', s.get('description', ''))}</div>
          <div class="sc-triggers">
            <div class="sc-trg-title">Trigger / Position hint</div>
            <div class="sc-trg">📍 {s.get('catalyst', '—')}</div>
            <div class="sc-trg">💡 {s.get('position_hint', '—')}</div>
          </div>
        </div>'''
    return html


def main():
    logger.info("=" * 60)
    logger.info("STRK RUN REPORT GENERATOR")
    logger.info("=" * 60)
    
    html = build_html()
    
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
    output_file = OUTPUT_DIR / f'STRK_RUN_{timestamp}.html'
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    logger.info(f"Saved: {output_file}")
    logger.info(f"Size: {len(html)} bytes")
    
    # Also save as latest
    latest_file = OUTPUT_DIR / 'STRK_RUN_latest.html'
    with open(latest_file, 'w', encoding='utf-8') as f:
        f.write(html)
    logger.info(f"Also: {latest_file}")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())