"""
STRK Scenario Module v1 · 4 forward-looking scenarios with probability + targets
Output: data/cache/strk_scenarios.json
"""
import json
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache'

STRK_ATH_USD = 4.42
STRK_ATL_USD = 0.019

def load(name):
    p = CACHE_DIR / name
    if not p.exists(): return None
    try:
        with open(p) as f: return json.load(f)
    except: return None

def compute_scenarios(lab, altcycle, confluence, funding, stables, netflow):
    scenarios = []
    strk = lab.get('strk_status', {}) if lab else {}
    triggers_hit = (strk.get('triggers_hit') or 0)
    triggers_total = strk.get('triggers_total', 4)
    phase = strk.get('wyckoff_phase', '?')
    current_price = strk.get('strk_price') or 0.023
    
    rally = (confluence.get('rally_score') or 0) if confluence else 0
    crash = (confluence.get('crash_score') or 0) if confluence else 0
    
    alt_phase = altcycle.get('phase', {}).get('phase', '') if altcycle else ''
    alt_conf = altcycle.get('phase', {}).get('confidence', 'LOW') if altcycle else 'LOW'
    stables_signal = stables.get('signal', '') if stables else ''
    
    utility_flow_m = 0
    if netflow and netflow.get('rows'):
        for r in netflow['rows']:
            if r.get('sector') in ('INFRA', 'RWA', 'LST'):
                utility_flow_m += (r.get('net_flow_m_usd') or 0)
    
    # BULL MARKUP
    bull_prob = 0
    if triggers_hit >= 2: bull_prob += 30
    if rally >= 5: bull_prob += 25
    if 'ALT' in alt_phase or 'EUPHORIA' in alt_phase: bull_prob += 20
    if stables_signal == 'HIGH_DRY_POWDER': bull_prob += 15
    if utility_flow_m > 10: bull_prob += 10
    bull_prob = min(bull_prob, 90)
    
    scenarios.append({
        'id': 'bull_markup',
        'name': '🚀 BULL MARKUP',
        'name_ru': 'Импульс вверх',
        'probability_pct': bull_prob,
        'targets': {
            'target_1': round(current_price * 1.8, 4),
            'target_2': round(current_price * 2.8, 4),
            'target_3': round(current_price * 4.2, 4),
            'ath_run_pct': round((STRK_ATH_USD / current_price - 1) * 100),
        },
        'timeframe': '3-9 месяцев',
        'trigger_to_confirm': f'Triggers {triggers_total}/4 + Rally ≥ 6/9 + weekly close > $0.030',
        'invalidation': 'Крах ниже $0.019 (ATL) или Rally < 3',
        'layman': 'Wyckoff Spring + confluence 5+/9 запускают markup. Крупные закончили накопление, толкают цену вверх. Первая цель $0.041, дальше $0.064.',
        'action': 'Держать позицию + добавлять на подтверждении. Не FOMO.',
    })
    
    # RE-ACCUMULATION
    re_accum_prob = 100 - bull_prob
    if crash >= 3: re_accum_prob -= 20
    if alt_conf == 'LOW': re_accum_prob += 15
    re_accum_prob = max(20, min(re_accum_prob, 70))
    
    scenarios.append({
        'id': 're_accumulation',
        'name': '🟡 RE-ACCUMULATION',
        'name_ru': 'Продолжение накопления',
        'probability_pct': re_accum_prob,
        'targets': {
            'range_low': 0.018,
            'range_high': 0.030,
        },
        'timeframe': '2-4 месяца',
        'trigger_to_confirm': 'Цена держится $0.018-0.030 + triggers 0-1/4',
        'invalidation': 'Breakout > $0.032 (markup) или < $0.017 (markdown)',
        'layman': 'Актив в фазе накопления — крупные тихо покупают на слабости, но ещё не толкают вверх. Range-bound. Здоровый setup, ждём импульса.',
        'action': 'Держать позицию если есть. Не докупать агрессивно. Watch weekly triggers.',
    })
    
    # DISTRIBUTION
    dist_prob = 0
    if crash >= 4: dist_prob += 30
    if utility_flow_m < -5: dist_prob += 20
    if (strk.get('bearish_30d') or 0) >= 27: dist_prob += 20
    if 'BEARISH' in strk.get('dune_monthly_signal', ''): dist_prob += 15
    dist_prob = min(dist_prob, 60)
    
    scenarios.append({
        'id': 'distribution',
        'name': '🔴 DISTRIBUTION',
        'name_ru': 'Распределение (bearish)',
        'probability_pct': dist_prob,
        'targets': {
            'target_1_down': round(current_price * 0.75, 4),
            'target_2_down': round(current_price * 0.55, 4),
            'floor_estimate': 0.014,
        },
        'timeframe': '2-6 месяцев',
        'trigger_to_confirm': 'Crash ≥ 5/9 + monthly STRONG_BEARISH + utility outflow',
        'invalidation': 'Rally ≥ 4 + weekly close > $0.028',
        'layman': 'Крупные тихо продают, retail покупает слухи. Может начаться markdown с целью $0.014. Пересмотреть thesis.',
        'action': 'Trim позицию 50-75%. Не докупать. Crash ≥ 5 → full exit.',
    })
    
    # MACRO SHOCK
    shock_prob = 15
    if crash >= 3: shock_prob += 10
    if stables_signal == 'LOW_DRY_POWDER': shock_prob += 5
    shock_prob = min(shock_prob, 35)
    
    scenarios.append({
        'id': 'macro_shock',
        'name': '⚡ MACRO SHOCK',
        'name_ru': 'Макро-шок (внешний)',
        'probability_pct': shock_prob,
        'targets': {
            'flash_low': round(current_price * 0.55, 4),
            'recovery_target': round(current_price * 0.85, 4),
        },
        'timeframe': 'Внезапно (дни)',
        'trigger_to_confirm': 'BTC -15%+ за неделю + risk-off + funding negative',
        'invalidation': 'BTC консолидирует + stables stable + no forced deleveraging',
        'layman': 'Внешний шок (Fed, geopolitics, exchange crash) может обрушить рынок. STRK как small-cap упадёт сильнее ($0.014 flash low). Обычно быстро восстанавливается 60-70% за месяц.',
        'action': 'Не паниковать. Если > 5% портфеля — уменьшить сейчас. Держать stables buffer для buy-the-dip.',
    })
    
    # Normalize
    total = sum(s['probability_pct'] for s in scenarios)
    if total > 0:
        f = 100 / total
        for s in scenarios:
            s['probability_pct'] = round(s['probability_pct'] * f, 1)
    return scenarios

def main():
    lab = load('strk_lab_report.json')
    altcycle = load('alt_cycle.json')
    confluence = load('confluence_gate.json')
    funding = load('funding_signals.json')
    stables = load('stables_signal.json')
    netflow = load('dune_sector_netflow.json')
    
    scenarios = compute_scenarios(lab, altcycle, confluence, funding, stables, netflow)
    
    current_price = (lab.get('strk_status', {}).get('strk_price') if lab else None) or 0.023
    
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'strk_current_price': current_price,
        'strk_ath_usd': STRK_ATH_USD,
        'strk_atl_usd': STRK_ATL_USD,
        'ath_run_from_current_pct': round((STRK_ATH_USD / current_price - 1) * 100),
        'scenarios': scenarios,
        'most_likely': max(scenarios, key=lambda s: s['probability_pct'])['id'],
        'summary': {
            'primary_thesis': 'Актив в Wyckoff accumulation после markdown с ATH $4.42. Ждём Spring + confluence rally для markup.',
            'horizon': '2-5 лет',
            'strategy': 'HOLD + докупать на weakness. Trim в distribution.',
        }
    }
    
    output_path = CACHE_DIR / 'strk_scenarios.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f'✓ Scenarios: {output_path}')
    for s in scenarios:
        print(f'\n  {s["name"]}: {s["probability_pct"]}%')

if __name__ == '__main__':
    main()
