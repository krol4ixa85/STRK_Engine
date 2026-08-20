"""
Market Regime Calculator v1
============================
Enhanced version of interpretation layer с weighted scoring across ВСЕХ модулей.

Reads:
- macro_narratives.json (Fed, US10Y, DXY, VIX, Gold)
- total_phase.json (BTC/ETH phase, BTC.D, TOTAL3)
- stables_signal.json (dry powder)
- funding_signals.json (perp funding)
- surf_events.json (news narrative)
- confluence_gate.json (STRK signal)
- strk_lab_report.json (STRK verdict)

Computes:
1. Weighted market regime score
2. Layman-friendly interpretation combining all signals + news narrative
3. Recommended action pattern for current regime
4. Confidence level

Weights (total 100%):
- Macro (Fed/US10Y/DXY/VIX): 30%
- Crypto Phase (BTC/ETH/BTC.D): 25%
- News Narrative (recent HOME events): 20%
- Stables (dry powder): 15%
- Funding (perp market): 10%

Output: data/cache/market_regime.json
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).parent.parent
CACHE_DIR = REPO_ROOT / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# LOADERS
# ============================================================
def load(name):
    p = CACHE_DIR / name
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except:
        return None

# ============================================================
# COMPONENT SCORERS (each returns -100 to +100)
# -100 = strongly bearish, 0 = neutral, +100 = strongly bullish
# ============================================================
def score_macro(macro_data):
    """Score TradFi macro regime (Fed/US10Y/DXY/VIX/Gold)."""
    if not macro_data:
        return 0, 'No data', {}
    
    score = 0
    factors = {}
    metrics = macro_data.get('metrics', {})
    
    # Fed rate direction (weight: 30% of macro)
    fed = metrics.get('fed_rate', {})
    if fed:
        current = fed.get('current')
        direction = fed.get('direction')
        if direction == 'falling':
            score += 30  # cuts = bullish
            factors['fed'] = 'Fed cuts (bullish)'
        elif direction == 'rising':
            score -= 30
            factors['fed'] = 'Fed hikes (bearish)'
        elif current and current > 5:
            score -= 15
            factors['fed'] = f'High rates {current}% (restrictive)'
        elif current and current < 3:
            score += 15
            factors['fed'] = f'Low rates {current}% (stimulative)'
    
    # US10Y direction (weight: 20% of macro)
    us10y = metrics.get('us10y', {})
    if us10y:
        direction = us10y.get('direction')
        if direction == 'falling':
            score += 15
            factors['us10y'] = 'Yields falling (bullish risk)'
        elif direction == 'rising':
            score -= 15
            factors['us10y'] = 'Yields rising (bearish risk)'
    
    # DXY direction (weight: 25% of macro)
    dxy = metrics.get('dxy', {})
    if dxy:
        direction = dxy.get('direction')
        change = dxy.get('change_pct', 0)
        if direction == 'falling' or change < -1:
            score += 20
            factors['dxy'] = f'Dollar weakens {change}% (bullish crypto)'
        elif direction == 'rising' or change > 1:
            score -= 20
            factors['dxy'] = f'Dollar strengthens {change}% (bearish crypto)'
    
    # VIX level (weight: 15% of macro)
    vix = metrics.get('vix', {})
    if vix:
        current = vix.get('current')
        if current and current < 15:
            score += 10
            factors['vix'] = f'VIX {current} low (complacency = bullish)'
        elif current and current > 25:
            score -= 15
            factors['vix'] = f'VIX {current} high (fear = bearish)'
    
    # Gold (safe haven flag)
    gold = metrics.get('gold', {})
    if gold:
        change = gold.get('change_pct', 0)
        if change > 5:
            score -= 5  # gold rising = flight to safety
            factors['gold'] = f'Gold +{change}% (safe haven bid)'
    
    # Normalize to -100 to +100
    score = max(-100, min(100, score))
    
    summary = ' · '.join(factors.values())[:200] if factors else 'Macro neutral'
    return score, summary, factors

def score_crypto_phase(total_data):
    """Score crypto market phase (BTC/ETH/BTC.D)."""
    if not total_data:
        return 0, 'No data', {}
    
    score = 0
    factors = {}
    
    # Market signal (weight: 50% of phase)
    signal = total_data.get('market_signal', 'UNKNOWN')
    signal_scores = {
        'BULL_MARKET': 50,
        'PRE_BULL_ACCUMULATION': 25,
        'MIXED': 0,
        'PRE_BEAR_DISTRIBUTION': -25,
        'BEAR_MARKET': -50,
    }
    score += signal_scores.get(signal, 0)
    factors['market_signal'] = f'Global: {signal}'
    
    # BTC phase (weight: 25%)
    btc_phase = total_data.get('btc_phase', {}).get('phase')
    if btc_phase == 'MARKUP':
        score += 20
        factors['btc'] = 'BTC MARKUP (+9%/30d)'
    elif btc_phase == 'MARKDOWN':
        score -= 20
        factors['btc'] = 'BTC MARKDOWN'
    elif btc_phase == 'ACCUMULATION':
        score += 10
        factors['btc'] = 'BTC accumulating'
    elif btc_phase == 'DISTRIBUTION':
        score -= 10
        factors['btc'] = 'BTC distributing'
    
    # BTC.D (weight: 25%)
    btc_d = total_data.get('btc_dominance', 0)
    if btc_d > 55:
        # High BTC.D = alts suppressed
        score -= 10  # bearish for alts (which is main focus)
        factors['btc_d'] = f'BTC.D {btc_d:.1f}% (alts suppressed)'
    elif btc_d < 45:
        score += 15  # alt season potential
        factors['btc_d'] = f'BTC.D {btc_d:.1f}% (alt season potential)'
    
    score = max(-100, min(100, score))
    summary = ' · '.join(factors.values())[:200]
    return score, summary, factors

def score_news_narrative(news_data):
    """Score based on recent HOME + MACRO news events."""
    if not news_data:
        return 0, 'No news data', {}
    
    score = 0
    factors = {}
    
    home_events = news_data.get('home_events', [])
    macro_events = news_data.get('macro_events', [])
    
    bullish_count = 0
    bearish_count = 0
    
    # Score HOME events (higher weight)
    for e in home_events:
        etype = e.get('type', '')
        severity = e.get('severity', 'medium')
        mult = 3 if severity == 'high' else 2 if severity == 'medium' else 1
        
        if etype in ('upgrade', 'partnership', 'funding', 'roadmap'):
            score += mult * 5
            bullish_count += 1
        elif etype in ('security', 'unlock'):
            score -= mult * 8  # bearish weighted higher
            bearish_count += 1
        elif etype == 'regulation':
            # Regulation depends on content
            summary = e.get('summary', '').lower()
            if 'approv' in summary or 'etf inflow' in summary:
                score += mult * 5
                bullish_count += 1
            else:
                score -= mult * 5
                bearish_count += 1
    
    # Score MACRO events (lower weight)
    for e in macro_events[:5]:
        etype = e.get('type', '')
        if etype in ('upgrade', 'partnership'):
            score += 2
        elif etype in ('security', 'unlock'):
            score -= 3
    
    factors['home_events'] = f'{len(home_events)} HOME · {bullish_count} bull · {bearish_count} bear'
    factors['macro_events'] = f'{len(macro_events)} macro events'
    
    score = max(-100, min(100, score))
    summary = f'{bullish_count} bullish + {bearish_count} bearish news events'
    return score, summary, factors

def score_stables(stables_data):
    """Score dry powder (stables market cap trend)."""
    if not stables_data:
        return 0, 'No data', {}
    
    score = 0
    factors = {}
    
    # Get stables signal
    signal = stables_data.get('signal', '')
    total_mcap = stables_data.get('total_mcap_billions', 0) or stables_data.get('total_market_cap_usd', 0) / 1e9
    change_pct = stables_data.get('change_30d_pct', 0)
    
    if 'GROWING' in signal or 'ACCUMULATION' in signal:
        score += 30  # accumulating dry powder = bullish setup
        factors['stables'] = f'Stables growing (dry powder building)'
    elif 'DEPLOYING' in signal or 'DEPLOYED' in signal or 'FALLING' in signal:
        score -= 20  # deploying = already spent
        factors['stables'] = f'Stables deploying (capital already in market)'
    elif change_pct > 3:
        score += 20
        factors['stables'] = f'Stables +{change_pct}% (accumulating)'
    elif change_pct < -3:
        score -= 15
        factors['stables'] = f'Stables {change_pct}% (deploying)'
    
    summary = factors.get('stables', 'Stables neutral')
    return score, summary, factors

def score_funding(funding_data):
    """Score perp funding rates."""
    if not funding_data:
        return 0, 'No data', {}
    
    score = 0
    factors = {}
    
    # Get overall funding
    signal = funding_data.get('signal', '')
    avg_funding = funding_data.get('avg_funding_pct', 0)
    
    if 'EXTREME_LONG' in signal or avg_funding > 0.05:
        score -= 25  # extreme longs = crowded, bearish
        factors['funding'] = f'Extreme LONG funding {avg_funding:.3f}% (crowded)'
    elif 'HIGH_LONG' in signal or avg_funding > 0.02:
        score -= 10
        factors['funding'] = f'High LONG funding (bulls paying)'
    elif 'EXTREME_SHORT' in signal or avg_funding < -0.05:
        score += 25  # extreme shorts = squeeze potential
        factors['funding'] = f'Extreme SHORT funding {avg_funding:.3f}% (squeeze potential)'
    elif avg_funding < -0.02:
        score += 10
        factors['funding'] = f'Negative funding (bears paying, bullish)'
    
    summary = factors.get('funding', 'Funding neutral')
    return score, summary, factors

# ============================================================
# WEIGHTED REGIME CALCULATION
# ============================================================
WEIGHTS = {
    'macro': 0.30,
    'phase': 0.25,
    'news': 0.20,
    'stables': 0.15,
    'funding': 0.10,
}

def calculate_regime(scores):
    """Calculate weighted regime score."""
    weighted = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS if k in scores)
    return round(weighted, 1)

def classify_regime(weighted_score):
    """Classify regime based on weighted score."""
    if weighted_score >= 40:
        return {
            'regime': 'STRONG_BULL',
            'label_ru': 'Сильный bull market',
            'action_ru': 'Держать позиции, добавлять на weakness. Не FOMO на pumps.',
            'confidence': 'high',
            'color': 'green',
        }
    elif weighted_score >= 20:
        return {
            'regime': 'BULL_EARLY',
            'label_ru': 'Ранний bull · накопление',
            'action_ru': 'Позиционироваться в качественные assets. Ждать breakouts.',
            'confidence': 'medium',
            'color': 'green',
        }
    elif weighted_score >= 10:
        return {
            'regime': 'BULL_BIAS',
            'label_ru': 'Bullish bias',
            'action_ru': 'Медленно накапливать. Watch confirmation.',
            'confidence': 'medium',
            'color': 'green',
        }
    elif weighted_score >= -10:
        return {
            'regime': 'NEUTRAL_MIXED',
            'label_ru': 'Смешанные сигналы',
            'action_ru': 'Wait mode. Не увеличивать risk. Watch confluence.',
            'confidence': 'low',
            'color': 'yellow',
        }
    elif weighted_score >= -20:
        return {
            'regime': 'BEAR_BIAS',
            'label_ru': 'Bearish bias',
            'action_ru': 'Reduce risk. Trim пpozitions на bounces.',
            'confidence': 'medium',
            'color': 'orange',
        }
    elif weighted_score >= -40:
        return {
            'regime': 'BEAR_DEVELOPING',
            'label_ru': 'Развивающийся bear',
            'action_ru': 'Значительное reducing. Stables buffer 50%.',
            'confidence': 'medium',
            'color': 'red',
        }
    else:
        return {
            'regime': 'STRONG_BEAR',
            'label_ru': 'Сильный bear market',
            'action_ru': 'Cash / stables приоритет. Ждать capitulation для entries.',
            'confidence': 'high',
            'color': 'red',
        }

# ============================================================
# LAYMAN-FRIENDLY NARRATIVE
# ============================================================
def build_layman_narrative(regime_class, scores, summaries, weighted_score, news_data):
    """Build layman-friendly explanation combining all signals + news."""
    parts = []
    
    # 1. Overall regime
    parts.append(f'**{regime_class["label_ru"]}** (score {weighted_score:+.0f}/100)')
    
    # 2. Best drivers (top 2 positive)
    positive = sorted([(k, v) for k, v in scores.items() if v > 10], key=lambda x: -x[1])
    if positive:
        top_pos = positive[0]
        parts.append(f'✓ Что supportive: {summaries.get(top_pos[0], "")}')
    
    # 3. Concerns (top 2 negative)
    negative = sorted([(k, v) for k, v in scores.items() if v < -10], key=lambda x: x[1])
    if negative:
        top_neg = negative[0]
        parts.append(f'⚠ Что concerns: {summaries.get(top_neg[0], "")}')
    
    # 4. News narrative
    if news_data:
        home_events = news_data.get('home_events', [])
        if home_events:
            critical = home_events[0]
            parts.append(f'📰 Critical news: {critical.get("summary", "")[:100]}...')
    
    # 5. What it means для торговли
    parts.append(f'💡 Что делать: {regime_class["action_ru"]}')
    
    return ' · '.join(parts)

# ============================================================
# BOTTLENECK CHECK (STRK-specific overlay)
# ============================================================
def analyze_strk_context(lab_data, confluence_data, weighted_score):
    """
    Analyze STRK-specific context relative to overall regime.
    """
    lab = (lab_data or {}).get('strk_status', {})
    conf = confluence_data or {}
    
    triggers = lab.get('triggers_hit', 0)
    verdict = lab.get('verdict', '')
    conf_rally = conf.get('rally_score', 0)
    cex_signal = lab.get('cex_signal', '')
    
    if weighted_score > 20 and triggers >= 3:
        return 'STRK: aligned с bullish regime + fundamentals готовы. Consider entry.'
    elif weighted_score > 20 and triggers < 2:
        return f'STRK: bullish regime но fundamentals not confirmed (triggers {triggers}/4). Wait for Spring.'
    elif weighted_score < -20 and conf_rally >= 5:
        return f'STRK: confluence rally но bearish regime — likely false signal. НЕ входить.'
    elif 'DISTRIBUTION' in cex_signal and weighted_score > 0:
        return f'STRK: CEX distribution vs bullish regime — thesis breach. Wait.'
    elif triggers == 0:
        return f'STRK: still in accumulation (0/4 triggers). Follow regime for direction.'
    else:
        return f'STRK: {verdict} · watch triggers и regime alignment.'

# ============================================================
# MAIN
# ============================================================
def main():
    print('=== Market Regime Calculator v1 ===\n')
    
    # Load all data
    data = {
        'macro': load('macro_narratives.json'),
        'phase': load('total_phase.json'),
        'news': load('surf_events.json'),
        'stables': load('stables_signal.json'),
        'funding': load('funding_signals.json'),
        'lab': load('strk_lab_report.json'),
        'confluence': load('confluence_gate.json'),
    }
    
    print('Data sources:')
    for k, v in data.items():
        print(f'  {k}: {"✓" if v else "✗ MISSING"}')
    
    # Compute component scores
    print('\n=== Component Scores ===')
    scores = {}
    summaries = {}
    factors_all = {}
    
    scores['macro'], summaries['macro'], factors_all['macro'] = score_macro(data['macro'])
    scores['phase'], summaries['phase'], factors_all['phase'] = score_crypto_phase(data['phase'])
    scores['news'], summaries['news'], factors_all['news'] = score_news_narrative(data['news'])
    scores['stables'], summaries['stables'], factors_all['stables'] = score_stables(data['stables'])
    scores['funding'], summaries['funding'], factors_all['funding'] = score_funding(data['funding'])
    
    for k, v in scores.items():
        weight = WEIGHTS[k] * 100
        contrib = v * WEIGHTS[k]
        print(f'  {k} ({weight:.0f}% weight): {v:+.0f} → contributes {contrib:+.1f}')
        print(f'    {summaries[k][:100]}')
    
    # Calculate weighted regime
    weighted = calculate_regime(scores)
    regime_class = classify_regime(weighted)
    
    print(f'\n=== WEIGHTED REGIME: {weighted:+.1f} ===')
    print(f'  Class: {regime_class["regime"]} ({regime_class["confidence"]})')
    print(f'  Label: {regime_class["label_ru"]}')
    print(f'  Action: {regime_class["action_ru"]}')
    
    # Layman narrative
    narrative = build_layman_narrative(regime_class, scores, summaries, weighted, data['news'])
    print(f'\n=== LAYMAN NARRATIVE ===')
    print(narrative)
    
    # STRK-specific overlay
    strk_context = analyze_strk_context(data['lab'], data['confluence'], weighted)
    print(f'\n=== STRK CONTEXT ===')
    print(f'  {strk_context}')
    
    # Save
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'weighted_score': weighted,
        'regime': regime_class['regime'],
        'regime_class': regime_class,
        'component_scores': scores,
        'component_summaries': summaries,
        'component_factors': factors_all,
        'weights': WEIGHTS,
        'layman_narrative_ru': narrative,
        'strk_context_ru': strk_context,
        'sources_loaded': [k for k, v in data.items() if v],
    }
    
    output_path = CACHE_DIR / 'market_regime.json'
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)
    print(f'\n✓ Written: {output_path}')

if __name__ == '__main__':
    main()
