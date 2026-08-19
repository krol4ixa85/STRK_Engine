"""
FULL RUN Level 4 Generator
===========================
Собирает 12-секционный deep report из существующих LAB cache файлов.

Usage:
    python full_run/generate_full_run.py                    # incrementing R{N}
    python full_run/generate_full_run.py --run-id R10       # explicit ID
    python full_run/generate_full_run.py --output custom.html

Output:
    reports/FULL_RUN_R{N}_{DATE}.html — self-contained HTML
    Also updates reports/latest.html (symlink or copy)
"""
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache'
REPORTS_DIR = REPO_ROOT / 'reports'
TEMPLATE_PATH = Path(__file__).parent / 'full_run_template.html'

REPORTS_DIR.mkdir(exist_ok=True)

# ============================================================
# LOAD DATA
# ============================================================
def load_json(name):
    p = CACHE_DIR / name
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None

def load_jsonl(name):
    p = REPO_ROOT / 'data' / 'history' / name
    if not p.exists():
        return []
    events = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    return events

def previous_run_id():
    """Find last R{N} report and return N+1."""
    existing = list(REPORTS_DIR.glob('FULL_RUN_R*.html'))
    if not existing:
        return 'R10'  # start after R9
    nums = []
    for p in existing:
        try:
            n = int(p.stem.split('_R')[1].split('_')[0])
            nums.append(n)
        except Exception:
            continue
    if not nums:
        return 'R10'
    return f'R{max(nums) + 1}'

def previous_run_data():
    """Load memory from last report for §2 Memory Engine."""
    memory_path = REPORTS_DIR / 'memory.json'
    if not memory_path.exists():
        return None
    try:
        with open(memory_path) as f:
            return json.load(f)
    except Exception:
        return None

def save_run_memory(data):
    """Save this run's key metrics for next run's §2 delta."""
    with open(REPORTS_DIR / 'memory.json', 'w') as f:
        json.dump(data, f, indent=2, default=str)

# ============================================================
# SECTIONS GENERATORS
# ============================================================
def gen_executive_summary(lab, altcycle, stables, confluence):
    """§1 — decision-first summary."""
    strk_status = lab.get('strk_status', {}) if lab else {}
    phase = strk_status.get('wyckoff_phase', 'UNKNOWN')
    triggers_hit = strk_status.get('triggers_hit', 0)
    triggers_total = strk_status.get('triggers_total', 4)
    
    alt_phase = altcycle.get('phase', {}) if altcycle else {}
    alt_name = alt_phase.get('phase_name', 'unknown')
    alt_conf = alt_phase.get('confidence', 'MEDIUM')
    
    strong_buy = lab.get('strong_buy', []) if lab else []
    utility_tokens = [x['token'] for x in strong_buy if x.get('token') != 'STRK']
    
    # Thesis verdict
    is_btc_or_early = 'BTC' in alt_phase.get('phase', '') or 'EARLY' in alt_phase.get('phase', '')
    utility_inflow = len([x for x in strong_buy if x.get('sector') in ('INFRA', 'RWA', 'LST')]) >= 2
    
    if is_btc_or_early and utility_inflow:
        thesis_verdict = '✓ THESIS INTACT'
        thesis_alert = 'ax-bull'
        thesis_summary = f'STRK utility bet остаётся valid. {alt_name} + utility sectors (INFRA/RWA/LST) в притоке. Держать план: WAIT для STRK, HOLD в utility rotation.'
    elif not is_btc_or_early:
        thesis_verdict = '⚠ MACRO SHIFT · REVIEW'
        thesis_alert = 'ax-warn'
        thesis_summary = f'Cycle перешёл в {alt_name}. Проверить: пере-оценить utility rotation, watch STRK для phase change.'
    else:
        thesis_verdict = '⚠ THESIS AT RISK'
        thesis_alert = 'ax-warn'
        thesis_summary = 'Utility sectors теряют flow. Пере-оценить timing STRK accumulation.'
    
    return {
        'THESIS_VERDICT': thesis_verdict,
        'THESIS_ALERT_CLASS': thesis_alert,
        'THESIS_SUMMARY': thesis_summary,
        'STRK_PHASE': phase,
        'STRK_PHASE_DELTA': f'{triggers_hit}/{triggers_total} triggers hit',
        'ALT_CYCLE': alt_name,
        'ALT_CYCLE_CONFIDENCE': f'Confidence: {alt_conf}',
        'UTILITY_COUNT': str(len(utility_tokens)),
        'UTILITY_LIST': ', '.join(utility_tokens[:5]) if utility_tokens else 'none',
        'DAYS_TO_REVIEW': '7',
    }

def gen_memory_engine(lab, altcycle, prev):
    """§2 — what changed since last run."""
    if not prev:
        return '<p style="color: var(--text-muted);">Первый FULL RUN · сохраняю baseline для delta в следующем report.</p>'
    
    changes = []
    strk_status = lab.get('strk_status', {}) if lab else {}
    alt_phase = altcycle.get('phase', {}) if altcycle else {}
    
    # Compare key metrics
    prev_triggers = prev.get('triggers_hit', 0)
    cur_triggers = strk_status.get('triggers_hit', 0)
    if cur_triggers != prev_triggers:
        delta_str = f'+{cur_triggers - prev_triggers}' if cur_triggers > prev_triggers else str(cur_triggers - prev_triggers)
        changes.append(f'<div class="alert-box ax-info">Triggers: {prev_triggers} → <strong>{cur_triggers}</strong> ({delta_str})</div>')
    
    prev_phase = prev.get('wyckoff_phase', '?')
    cur_phase = strk_status.get('wyckoff_phase', '?')
    if cur_phase != prev_phase:
        changes.append(f'<div class="alert-box ax-warn"><strong>PHASE CHANGE:</strong> {prev_phase} → {cur_phase}</div>')
    
    prev_alt = prev.get('alt_cycle_phase', '?')
    cur_alt = alt_phase.get('phase', '?')
    if cur_alt != prev_alt:
        changes.append(f'<div class="alert-box ax-warn"><strong>ALT-CYCLE SHIFT:</strong> {prev_alt} → {cur_alt}</div>')
    
    prev_run = prev.get('run_id', '?')
    prev_date = prev.get('generated_at', '?')[:10]
    
    changes.insert(0, f'<p style="color: var(--text-muted); font-size: 12px;">Previous run: <strong>{prev_run}</strong> · {prev_date}</p>')
    
    if len(changes) == 1:
        changes.append('<p style="color: var(--text-muted);">Нет значимых изменений с прошлого run.</p>')
    
    return ''.join(changes)

def gen_dashboard_snapshot(lab, altcycle, confluence, sector_flow):
    """§3 — snapshot ключевых метрик как в overview."""
    html = '<div class="stat-grid">'
    
    strk = lab.get('strk_status', {}) if lab else {}
    html += f'''<div class="stat-box">
        <div class="stat-label">STRK Verdict</div>
        <div class="stat-value">{strk.get('verdict', '—')}</div>
        <div class="stat-delta">{strk.get('wyckoff_phase', '—')}</div>
    </div>'''
    
    if confluence:
        rally = confluence.get('rally_score', 0)
        crash = confluence.get('crash_score', 0)
        html += f'''<div class="stat-box">
            <div class="stat-label">Confluence Gate</div>
            <div class="stat-value">Rally {rally}/9 · Crash {crash}/9</div>
            <div class="stat-delta">{confluence.get('signal', '—')}</div>
        </div>'''
    
    if altcycle:
        phase = altcycle.get('phase', {})
        btc_d = altcycle.get('metrics', {}).get('btc_dominance_pct', 0)
        html += f'''<div class="stat-box">
            <div class="stat-label">Alt-Cycle</div>
            <div class="stat-value">{phase.get('phase_name', '—')}</div>
            <div class="stat-delta">BTC.D {btc_d:.1f}%</div>
        </div>'''
    
    html += '</div>'
    return html

def gen_stables_section(stables):
    """§4 — dry powder analysis."""
    if not stables:
        return '<p style="color: var(--text-muted);">Stables data not available.</p>'
    
    signal = stables.get('signal', '—')
    dom_pct = stables.get('stables_dominance_pct', 0)
    total = stables.get('total_stables_marketcap_usd', 0)
    
    alert_class = 'ax-bull' if signal == 'HIGH_DRY_POWDER' else 'ax-warn' if signal == 'LOW_DRY_POWDER' else 'ax-info'
    
    html = f'''<div class="alert-box {alert_class}">
        <strong>{signal.replace('_', ' ')}</strong>: Stables dominance {dom_pct:.1f}%, total ${total/1e9:.1f}B
    </div>'''
    
    reasoning = stables.get('reasoning', [])
    if reasoning:
        html += '<ul style="font-size: 13px; margin: 8px 0; padding-left: 20px;">'
        for r in reasoning:
            html += f'<li style="margin-bottom: 4px;">{r}</li>'
        html += '</ul>'
    
    return html

def gen_layers_section(sector_flow, altcycle):
    """§5 — L1-L5 layer analysis."""
    if not sector_flow or not sector_flow.get('rows'):
        return '<p style="color: var(--text-muted);">Layer flow data not available.</p>'
    
    # Aggregate by sector
    sectors = {}
    for row in sector_flow.get('rows', []):
        s = row.get('sector')
        if not s: continue
        sectors.setdefault(s, 0)
        sectors[s] += row.get('net_flow_m_usd', 0)
    
    # Map sectors to layers
    layer_map = {
        'L1': ['BTC', 'ETH'],
        'L2': ['L2'],
        'L3 · DeFi': ['DeFi', 'LST'],
        'L4 · Infra': ['INFRA', 'DEPIN', 'RWA'],
        'L5 · Applications': ['GAMING', 'AI_AGENTS', 'MEME'],
    }
    
    html = '<div class="layer-grid">'
    for layer_name, layer_sectors in layer_map.items():
        total = sum(sectors.get(s, 0) for s in layer_sectors)
        sign = '+' if total >= 0 else ''
        color = 'var(--green)' if total > 0.5 else 'var(--red)' if total < -0.5 else 'var(--text-muted)'
        html += f'''<div class="layer-row">
            <div class="layer-tag">{layer_name}</div>
            <div style="color: var(--text-secondary); font-size: 12px;">{', '.join(layer_sectors)}</div>
            <div style="text-align: right; color: {color}; font-family: monospace; font-weight: 700;">{sign}${total:.2f}M</div>
        </div>'''
    html += '</div>'
    return html

def gen_catalysts_section():
    """§6 — upcoming catalysts. Manual for now, later auto from news."""
    html = '<ul class="catalyst-list">'
    catalysts = [
        {'when': '~2026 Q4', 'event': 'Starknet v0.15 mainnet upgrade', 'impact': 'high', 'desc': 'Ключевой catalyst для STRK thesis'},
        {'when': '~2027', 'event': 'ETH L2 fee compression cycle', 'impact': 'high', 'desc': 'STRK positioning как cheaper alternative'},
        {'when': 'ongoing', 'event': 'RWA sector expansion', 'impact': 'med', 'desc': 'LINK/ONDO benefit'},
        {'when': 'ongoing', 'event': 'ETH staking yield compression', 'impact': 'med', 'desc': 'ETHFI/LDO leverage yield play'},
    ]
    for c in catalysts:
        html += f'''<li class="catalyst-item {c["impact"]}">
            <strong>{c["when"]}</strong> · {c["event"]} — <span style="color: var(--text-muted);">{c["desc"]}</span>
        </li>'''
    html += '</ul>'
    html += '<p style="font-size: 11px; color: var(--text-muted); margin-top: 12px;">⚠ Catalyst timeline пока manual · auto-extraction из news feed на следующей итерации.</p>'
    return html

def gen_confidence_section(lab, altcycle, stables, confluence, sector_flow):
    """§7 — weighted confidence scores."""
    scores = []
    
    strk = lab.get('strk_status', {}) if lab else {}
    triggers_pct = (strk.get('triggers_hit', 0) / max(strk.get('triggers_total', 4), 1)) * 100
    scores.append(('STRK Entry Readiness', triggers_pct, 0.30))
    
    alt_conf = altcycle.get('phase', {}).get('confidence', 'MEDIUM') if altcycle else 'MEDIUM'
    conf_val = {'HIGH': 85, 'MEDIUM': 55, 'LOW': 30}.get(alt_conf, 50)
    scores.append(('Alt-Cycle Certainty', conf_val, 0.20))
    
    if confluence:
        rally = confluence.get('rally_score', 0)
        scores.append(('Rally Confluence', (rally / 9) * 100, 0.20))
    
    if stables:
        dp_score = 75 if stables.get('signal') == 'HIGH_DRY_POWDER' else 25
        scores.append(('Dry Powder Available', dp_score, 0.15))
    
    if sector_flow and sector_flow.get('rows'):
        utility_flow = sum(r.get('net_flow_m_usd', 0) for r in sector_flow['rows'] if r.get('sector') in ('INFRA', 'RWA', 'LST'))
        utility_score = min(100, max(0, 50 + utility_flow * 10))
        scores.append(('Utility Sector Health', utility_score, 0.15))
    
    html = ''
    weighted_total = 0
    weight_sum = 0
    for name, score, weight in scores:
        color = 'var(--green)' if score >= 65 else 'var(--yellow)' if score >= 40 else 'var(--red)'
        html += f'''<div class="confidence-bar">
            <div class="conf-name">{name}</div>
            <div class="conf-bar-track"><div class="conf-bar-fill" style="width: {score:.0f}%; background: {color};"></div></div>
            <div class="conf-value">{score:.0f}%</div>
        </div>'''
        weighted_total += score * weight
        weight_sum += weight
    
    overall = weighted_total / max(weight_sum, 0.01)
    color = 'var(--green)' if overall >= 65 else 'var(--yellow)' if overall >= 40 else 'var(--red)'
    html += f'''<div class="alert-box ax-info" style="margin-top: 16px;">
        <strong>Weighted Overall:</strong> <span style="color: {color}; font-size: 18px; font-weight: 700;">{overall:.0f}%</span>
    </div>'''
    
    return html

def gen_dominance_section(altcycle, sector_flow):
    """§8 — systemic dominance map."""
    html = '<div class="dominance-map">'
    
    if altcycle:
        metrics = altcycle.get('metrics', {})
        html += f'''<div class="dom-cell">
            <div class="dom-name">BTC Dominance</div>
            <div style="font-size: 20px; font-weight: 700;">{metrics.get('btc_dominance_pct', 0):.1f}%</div>
            <div class="dom-desc">Market share of BTC</div>
        </div>'''
        html += f'''<div class="dom-cell">
            <div class="dom-name">ETH Dominance</div>
            <div style="font-size: 20px; font-weight: 700;">{metrics.get('eth_dominance_pct', 0):.1f}%</div>
            <div class="dom-desc">Market share of ETH</div>
        </div>'''
    
    if sector_flow:
        sectors = {}
        for row in sector_flow.get('rows', []):
            s = row.get('sector')
            if s:
                sectors.setdefault(s, 0)
                sectors[s] += row.get('net_flow_m_usd', 0)
        sorted_secs = sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:3]
        for sec, flow in sorted_secs:
            html += f'''<div class="dom-cell">
                <div class="dom-name">{sec} Sector</div>
                <div style="font-size: 20px; font-weight: 700; color: var(--green);">+${flow:.1f}M</div>
                <div class="dom-desc">Top inflow sector</div>
            </div>'''
    
    html += '</div>'
    return html

def gen_beneficiaries_section(lab, sector_flow):
    """§9 — beneficiaries & weakest links."""
    if not lab:
        return '<p style="color: var(--text-muted);">Data not available.</p>'
    
    strong_buy = lab.get('strong_buy', [])
    sell = lab.get('sell', [])
    
    html = '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">'
    
    html += '<div><div class="stat-label" style="margin-bottom: 8px;">🟢 Beneficiaries</div>'
    for item in strong_buy[:6]:
        html += f'''<div class="layer-row" style="margin-bottom: 4px;">
            <div class="layer-tag">{item.get('token', '?')}</div>
            <div style="font-size: 11px; color: var(--text-secondary);">{item.get('sector', '')} · +{item.get('price_change_7d_pct', 0):.1f}%</div>
            <div style="text-align: right; color: var(--green); font-family: monospace; font-weight: 700;">+${item.get('net_flow_m_usd', 0):.1f}M</div>
        </div>'''
    html += '</div>'
    
    html += '<div><div class="stat-label" style="margin-bottom: 8px;">🔴 Weakest Links</div>'
    for item in sell[:6]:
        html += f'''<div class="layer-row" style="margin-bottom: 4px;">
            <div class="layer-tag">{item.get('token', '?')}</div>
            <div style="font-size: 11px; color: var(--text-secondary);">{item.get('sector', '')} · {item.get('price_change_7d_pct', 0):.1f}%</div>
            <div style="text-align: right; color: var(--red); font-family: monospace; font-weight: 700;">${item.get('net_flow_m_usd', 0):.2f}M</div>
        </div>'''
    html += '</div>'
    
    html += '</div>'
    return html

def gen_inevitability_section():
    """§10 — structural inevitability matrix. Manual thesis grid."""
    theses = [
        ('L2 fee compression', 'ETH мейн стал expensive · rollups need throughput', 'HIGH', 'Ecosystem shifts to modular scaling'),
        ('RWA tokenization', 'Traditional finance rails need on-chain', 'HIGH', 'Trillion+ market opportunity'),
        ('LST fragmentation', 'ETH staking too concentrated', 'MED', 'Diversification demand'),
        ('AI × crypto agents', 'AI economy needs crypto rails', 'MED', 'Speculative near-term, real long-term'),
    ]
    html = '<div class="layer-grid">'
    for name, why, conf, impact in theses:
        color = 'var(--green)' if conf == 'HIGH' else 'var(--yellow)'
        html += f'''<div class="layer-row">
            <div class="layer-tag" style="color: {color};">{conf}</div>
            <div>
                <strong>{name}</strong><br>
                <span style="font-size: 11px; color: var(--text-muted);">{why} → {impact}</span>
            </div>
            <div></div>
        </div>'''
    html += '</div>'
    return html

def gen_risk_section(lab, altcycle, confluence):
    """§11 — active risks & invalidation."""
    risks = []
    
    strk = lab.get('strk_status', {}) if lab else {}
    if strk.get('bearish_30d', 0) >= 25:
        risks.append(f'<strong>STRK bearish trend:</strong> {strk.get("bearish_30d", 0)}/30d bearish · watching for reversal signal')
    
    if strk.get('dune_monthly_signal') == 'BEARISH_BREAKDOWN':
        risks.append(f'<strong>Dune monthly BEARISH_BREAKDOWN:</strong> может углубиться в STRONG_BEARISH_BREAKDOWN · thesis breach if paired with STRONG_DISTRIBUTION on CEX')
    
    if confluence and confluence.get('crash_score', 0) >= 3:
        risks.append(f'<strong>Crash confluence:</strong> {confluence.get("crash_score", 0)}/9 · monitor если растёт')
    
    alt_phase = altcycle.get('phase', {}).get('phase', '') if altcycle else ''
    if 'MEME' in alt_phase:
        risks.append('<strong>Meme season:</strong> utility rotation теряет relevance · watch for capital return')
    
    if not risks:
        risks.append('Нет активных structural risks на этой итерации.')
    
    html = ''
    for r in risks:
        html += f'<div class="risk-row"><div>{r}</div></div>'
    
    html += '''<div class="alert-box ax-info" style="margin-top: 12px;">
        <strong>Thesis invalidation trigger:</strong> Одновременное появление 3+ из следующих →
        STRONG_BEARISH_BREAKDOWN (29+/30d) + STRONG_DISTRIBUTION on CEX + utility sectors в net outflow + MEME season >4w
    </div>'''
    return html

def gen_human_context():
    """§12 — human context, macro overlay."""
    return '''<div class="alert-box ax-info">
        <strong>Xenia thesis:</strong> STRK как замена ETH-функции (ZK L2 bottleneck). Асимметричная премия за bottleneck к 2027-28.
        Стратегия "1+3": long-term STRK + tactical rotation в utility STRONG_BUY активы (LINK/ETHFI/MORPHO).
    </div>
    <p style="font-size: 13px; color: var(--text-secondary); margin-top: 12px;">
        Horizon: 2-5 years · Hold period: months, not weeks · Trigger frame: phase transitions (accumulation → markup → distribution)
    </p>
    <p style="font-size: 12px; color: var(--text-muted); margin-top: 8px;">
        FULL RUN cadence: weekly Mondays 09:00 MSK · on-demand при significant market events.
    </p>'''

# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', help='Explicit run ID (e.g. R10)')
    parser.add_argument('--output', help='Custom output path')
    args = parser.parse_args()
    
    # Load all data
    lab = load_json('strk_lab_report.json')
    momentum = load_json('dune_sector_momentum.json')
    netflow = load_json('dune_sector_netflow.json')
    altcycle = load_json('alt_cycle.json')
    stables = load_json('stables_signal.json')
    funding = load_json('funding_signals.json')
    confluence = load_json('confluence_gate.json')
    
    # Determine run ID
    run_id = args.run_id or previous_run_id()
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    generated_at = datetime.now(timezone.utc).isoformat()[:19] + 'Z'
    
    # Previous run data for §2 delta
    prev = previous_run_data()
    
    # Generate all sections
    exec_summary = gen_executive_summary(lab, altcycle, stables, confluence)
    
    replacements = {
        'RUN_ID': run_id,
        'DATE': date_str,
        'GENERATED_AT': generated_at,
        'RUN_TITLE': f'Capital Intelligence · {run_id} · {exec_summary["THESIS_VERDICT"]}',
        'RUN_SUBTITLE': exec_summary['THESIS_SUMMARY'],
        **exec_summary,
        'MEMORY_ENGINE_CONTENT': gen_memory_engine(lab, altcycle, prev),
        'DASHBOARD_SNAPSHOT_CONTENT': gen_dashboard_snapshot(lab, altcycle, confluence, netflow),
        'STABLES_CONTENT': gen_stables_section(stables),
        'LAYERS_CONTENT': gen_layers_section(netflow, altcycle),
        'CATALYSTS_CONTENT': gen_catalysts_section(),
        'CONFIDENCE_CONTENT': gen_confidence_section(lab, altcycle, stables, confluence, netflow),
        'DOMINANCE_CONTENT': gen_dominance_section(altcycle, netflow),
        'BENEFICIARIES_CONTENT': gen_beneficiaries_section(lab, netflow),
        'INEVITABILITY_CONTENT': gen_inevitability_section(),
        'RISK_CONTENT': gen_risk_section(lab, altcycle, confluence),
        'HUMAN_CONTEXT_CONTENT': gen_human_context(),
    }
    
    # Load template & render
    with open(TEMPLATE_PATH) as f:
        template = f.read()
    
    output = template
    for key, val in replacements.items():
        output = output.replace('{{' + key + '}}', str(val))
    
    # Save
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = REPORTS_DIR / f'FULL_RUN_{run_id}_{date_str}.html'
    
    with open(output_path, 'w') as f:
        f.write(output)
    
    # Update latest
    latest_path = REPORTS_DIR / 'latest.html'
    with open(latest_path, 'w') as f:
        f.write(output)
    
    # Save memory for next run's §2
    strk = lab.get('strk_status', {}) if lab else {}
    memory = {
        'run_id': run_id,
        'generated_at': generated_at,
        'triggers_hit': strk.get('triggers_hit', 0),
        'wyckoff_phase': strk.get('wyckoff_phase', '?'),
        'alt_cycle_phase': altcycle.get('phase', {}).get('phase', '?') if altcycle else '?',
        'strong_buy_tokens': [x['token'] for x in lab.get('strong_buy', [])] if lab else [],
    }
    save_run_memory(memory)
    
    print(f'✓ FULL RUN generated: {output_path.name}')
    print(f'  ↪ Latest: {latest_path.name}')
    print(f'  ↪ Memory saved for {run_id} → next run will show delta')

if __name__ == '__main__':
    main()
