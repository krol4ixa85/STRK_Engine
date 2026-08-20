"""
Interpretation Layer v1
========================
Synthesizes ALL signals into ONE unified reading.

Reads all cache files:
- macro_narratives.json (Fed/US10Y/DXY/VIX/Gold)
- total_phase.json (BTC/ETH phase, BTC.D)
- strk_lab_report.json (STRK verdict, HOLD tokens)
- confluence_gate.json (STRK confluence signals)
- surf_events.json (news + per-asset)
- strk_scenarios.json (4 scenarios)
- dune_sector_netflow.json (sector rotation)

Outputs unified reading:
- context_summary (2-3 lines · big picture)
- coherence_score (0-100 · насколько signals consistent)
- conflict_alerts (где signals конфликтуют)
- per_asset_verdict (STRK, LINK, ETHFI, ETH, BTC)
- unified_action (что делать СЕГОДНЯ)
- what_to_watch (следующий trigger)

Output: data/cache/interpretation.json
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
# COHERENCE ANALYZER
# ============================================================
def analyze_coherence(signals):
    """
    Determines how consistent are signals with each other.
    Returns coherence_score (0-100) + conflict_alerts list.
    """
    conflicts = []
    consistency_points = 0
    total_checks = 0
    
    # Check 1: Global market phase vs Fed macro regime
    market_signal = signals.get('total_phase', {}).get('market_signal')
    macro_regime = signals.get('macro', {}).get('regime', {}).get('regime')
    
    total_checks += 1
    if market_signal == 'BULL_MARKET' and macro_regime == 'RISK_ON':
        consistency_points += 1  # aligned
    elif market_signal == 'BEAR_MARKET' and macro_regime == 'RISK_OFF':
        consistency_points += 1
    elif macro_regime == 'MIXED':
        consistency_points += 0.5  # partial credit
    else:
        conflicts.append({
            'level': 'medium',
            'type': 'macro_market_divergence',
            'text_ru': f'Crypto market показывает {market_signal}, но macro regime {macro_regime}. Возможен разворот на смене macro тренда.',
        })
    
    # Check 2: STRK confluence vs LAB verdict
    conf_action = signals.get('confluence', {}).get('action', '')
    lab_verdict = signals.get('lab', {}).get('strk_status', {}).get('verdict', '')
    triggers_hit = signals.get('lab', {}).get('strk_status', {}).get('triggers_hit', 0)
    triggers_total = signals.get('lab', {}).get('strk_status', {}).get('triggers_total', 4)
    
    total_checks += 1
    conf_bullish = 'LONG' in conf_action or 'BUY' in conf_action or 'RALLY' in conf_action.upper()
    lab_bullish = 'STRONG_BUY' in lab_verdict or 'CONFIRMED' in lab_verdict
    
    if conf_bullish and lab_bullish:
        consistency_points += 1
    elif conf_bullish and not lab_bullish:
        # Confluence rally but LAB not confirmed → likely fake signal
        conflicts.append({
            'level': 'high',
            'type': 'strk_confluence_lab_conflict',
            'text_ru': f'Confluence Gate показывает "{conf_action}", но LAB verdict {lab_verdict} ({triggers_hit}/{triggers_total} triggers). Rally signal НЕ подтверждён fundamentals — вероятно false signal или pump на low liquidity.',
        })
    else:
        consistency_points += 0.5
    
    # Check 3: STRK CEX flow vs Confluence signal
    cex_flow = signals.get('lab', {}).get('strk_status', {}).get('cex_signal', '')
    total_checks += 1
    if 'DISTRIBUTION' in cex_flow and conf_bullish:
        conflicts.append({
            'level': 'high',
            'type': 'cex_flow_vs_rally',
            'text_ru': f'CEX flow показывает {cex_flow} (whales отправляют на биржу для продажи), но confluence rally. THESIS BREACH — не входить.',
        })
    elif 'ACCUMULATION' in cex_flow and conf_bullish:
        consistency_points += 1
    else:
        consistency_points += 0.5
    
    # Check 4: Dune monthly vs LAB
    dune_monthly = signals.get('lab', {}).get('strk_status', {}).get('dune_monthly_signal', '')
    total_checks += 1
    if 'BEARISH' in dune_monthly and conf_bullish:
        conflicts.append({
            'level': 'medium',
            'type': 'monthly_trend_vs_rally',
            'text_ru': f'Monthly Dune signal: {dune_monthly} (30d тренд bearish). Confluence rally может быть short-term counter-trend bounce.',
        })
    elif dune_monthly:
        consistency_points += 0.5
    
    # Check 5: BTC.D vs alt-signals
    btc_d = signals.get('total_phase', {}).get('btc_dominance', 50)
    total_checks += 1
    if btc_d > 55:
        conflicts.append({
            'level': 'medium',
            'type': 'btc_dominance_high',
            'text_ru': f'BTC.D {btc_d:.1f}% (высокая) — alts под давлением. Даже если STRK rally сигнал есть, movement будет короткий и слабый.',
        })
    else:
        consistency_points += 0.5
    
    # Check 6: News HOME events (critical for HOLD tokens)
    news_events = signals.get('news', {}).get('home_events', [])
    hold_tokens = signals.get('lab', {}).get('strong_buy', [])
    hold_symbols = [x.get('token') for x in hold_tokens]
    
    for event in news_events:
        assets = event.get('assets', [])
        # Check if event affects HOLD token bearishly
        if event.get('type') in ('security', 'unlock', 'regulation') and event.get('severity') in ('high', 'medium'):
            hit_hold = [a for a in assets if a in hold_symbols]
            if hit_hold:
                conflicts.append({
                    'level': 'high',
                    'type': 'hold_token_bearish_news',
                    'text_ru': f'⚠ Bearish news затрагивает {", ".join(hit_hold)} (в HOLD): {event.get("summary", "")[:100]}. Review SIZE.',
                })
    
    coherence_score = int(consistency_points / max(total_checks, 1) * 100)
    
    return coherence_score, conflicts

# ============================================================
# PER-ASSET VERDICT
# ============================================================
def get_asset_verdict(asset, signals):
    """
    Unified verdict per asset combining all sources.
    """
    # Get news per this asset
    news = signals.get('news', {})
    per_asset = news.get('per_asset_top', {}).get(asset, [])
    total_news = news.get('per_asset_counts', {}).get(asset, 0)
    
    # Count news sentiment
    home_news = [n for n in per_asset if n.get('route') == 'home']
    bearish_news = [n for n in per_asset if n.get('type') in ('security', 'unlock', 'regulation') and n.get('severity') in ('high', 'medium')]
    bullish_news = [n for n in per_asset if n.get('type') in ('upgrade', 'partnership', 'funding', 'roadmap')]
    
    # Asset-specific logic
    if asset == 'STRK':
        return get_strk_verdict(signals, home_news, bearish_news, bullish_news, total_news)
    
    # For HOLD tokens (LINK, ETHFI, MORPHO, etc)
    hold_tokens = [x.get('token') for x in signals.get('lab', {}).get('strong_buy', [])]
    if asset in hold_tokens:
        return get_hold_verdict(asset, signals, home_news, bearish_news, bullish_news, total_news)
    
    # For DIVERGENCE tokens
    div_tokens = [x.get('token') for x in signals.get('lab', {}).get('divergence', [])]
    if asset in div_tokens:
        return get_divergence_verdict(asset, signals, home_news, bearish_news, bullish_news)
    
    # For BTC/ETH (macro)
    if asset in ('BTC', 'ETH'):
        return get_macro_asset_verdict(asset, signals, home_news, bearish_news, bullish_news)
    
    return None

def get_strk_verdict(signals, home_news, bearish, bullish, total):
    """STRK unified verdict."""
    conf = signals.get('confluence', {})
    lab = signals.get('lab', {}).get('strk_status', {})
    total_phase = signals.get('total_phase', {})
    
    conf_rally = conf.get('rally_score', 0)
    triggers = lab.get('triggers_hit', 0)
    cex = lab.get('cex_signal', '')
    monthly = lab.get('dune_monthly_signal', '')
    market_signal = total_phase.get('market_signal', 'UNKNOWN')
    
    # Multi-signal decision
    thesis_intact = 'BEARISH' not in monthly and 'DISTRIBUTION' not in cex
    confluence_signals = conf_rally >= 5
    fundamentals_confirm = triggers >= 2
    macro_supportive = market_signal in ('BULL_MARKET', 'PRE_BULL_ACCUMULATION')
    
    if fundamentals_confirm and confluence_signals and thesis_intact and macro_supportive:
        verdict = 'ENTER'
        signal_color = 'green'
        rec_ru = 'Все сигналы aligned — можно входить размером 25-50%. Stop согласно confluence.'
    elif confluence_signals and not thesis_intact:
        verdict = 'FALSE_RALLY_WAIT'
        signal_color = 'red'
        rec_ru = f'⚠ Confluence rally {conf_rally}/9, НО thesis breach: {cex}, {monthly}. Это pump НЕ на fundamentals. НЕ входить.'
    elif fundamentals_confirm and not confluence_signals:
        verdict = 'FUNDAMENTALS_OK_WAIT_TRIGGER'
        signal_color = 'yellow'
        rec_ru = 'Fundamentals подтверждают, но нет technical signal. Ждать confluence rally 5+/9.'
    elif not macro_supportive:
        verdict = 'MACRO_HEADWIND'
        signal_color = 'orange'
        rec_ru = f'Macro не supportive ({market_signal}). STRK как small-cap follows BTC. Wait.'
    else:
        verdict = 'STILL_ACCUMULATION'
        signal_color = 'gray'
        rec_ru = f'STRK в accumulation phase. {triggers}/4 triggers hit. Ждать Spring signal + confluence.'
    
    # Add news context
    news_context = ''
    if bearish:
        news_context = f' | ⚠ {len(bearish)} bearish news событий (см News секцию)'
    elif bullish:
        news_context = f' | +{len(bullish)} bullish news событий'
    
    return {
        'asset': 'STRK',
        'verdict': verdict,
        'signal_color': signal_color,
        'recommendation_ru': rec_ru + news_context,
        'confluence_score': conf_rally,
        'triggers_hit': triggers,
        'thesis_intact': thesis_intact,
        'news_events_total': total,
        'news_bearish_count': len(bearish),
        'news_bullish_count': len(bullish),
    }

def get_hold_verdict(asset, signals, home_news, bearish, bullish, total):
    """HOLD token unified verdict."""
    total_phase = signals.get('total_phase', {})
    market_signal = total_phase.get('market_signal', 'UNKNOWN')
    
    # Simple logic: if bearish news + macro headwind → trim
    # If bullish news + macro supportive → hold aggressively
    
    if bearish and (market_signal == 'BEAR_MARKET' or market_signal == 'PRE_BEAR_DISTRIBUTION'):
        verdict = 'TRIM'
        signal_color = 'red'
        rec_ru = f'{asset}: bearish news + bearish macro → trim 50%'
    elif bearish:
        verdict = 'REVIEW'
        signal_color = 'orange'
        rec_ru = f'{asset}: bearish news ({len(bearish)} events). Review SIZE, tighten stops.'
    elif bullish and market_signal == 'BULL_MARKET':
        verdict = 'HOLD_STRONG'
        signal_color = 'green'
        rec_ru = f'{asset}: bullish news + bull market → продолжать HOLD. Не FOMO новые входы.'
    else:
        verdict = 'HOLD'
        signal_color = 'gray'
        rec_ru = f'{asset}: держать позицию. Watch mode.'
    
    return {
        'asset': asset,
        'verdict': verdict,
        'signal_color': signal_color,
        'recommendation_ru': rec_ru,
        'news_events_total': total,
        'news_bearish_count': len(bearish),
        'news_bullish_count': len(bullish),
    }

def get_divergence_verdict(asset, signals, home_news, bearish, bullish):
    """DIVERGENCE token verdict."""
    return {
        'asset': asset,
        'verdict': 'AVOID',
        'signal_color': 'red',
        'recommendation_ru': f'{asset}: divergence signal (цена up без capital flow). Не входить.',
        'news_bearish_count': len(bearish),
        'news_bullish_count': len(bullish),
    }

def get_macro_asset_verdict(asset, signals, home_news, bearish, bullish):
    """BTC/ETH macro verdict."""
    total_phase = signals.get('total_phase', {})
    phase = total_phase.get(f'{asset.lower()}_phase', {})
    
    if phase.get('phase') == 'MARKUP':
        verdict = 'MACRO_BULL'
        rec = f'{asset} в MARKUP phase (+{phase.get("change_30d_pct")}%/30d). Держать long-term core.'
    elif phase.get('phase') == 'MARKDOWN':
        verdict = 'MACRO_BEAR'
        rec = f'{asset} в MARKDOWN. Reduce exposure.'
    else:
        verdict = 'MACRO_HOLD'
        rec = f'{asset} в {phase.get("phase")}. Follow phase.'
    
    if bearish:
        rec += f' | {len(bearish)} bearish news'
    
    return {
        'asset': asset,
        'verdict': verdict,
        'signal_color': 'green' if phase.get('phase') == 'MARKUP' else 'red' if phase.get('phase') == 'MARKDOWN' else 'gray',
        'recommendation_ru': rec,
        'news_bearish_count': len(bearish),
        'news_bullish_count': len(bullish),
    }

# ============================================================
# UNIFIED CONTEXT SUMMARY
# ============================================================
def build_context_summary(signals, coherence_score, conflicts):
    """
    Build 2-3 line summary of what's happening in the market right now.
    """
    macro = signals.get('macro', {})
    total_phase = signals.get('total_phase', {})
    lab = signals.get('lab', {}).get('strk_status', {})
    news = signals.get('news', {})
    
    parts = []
    
    # 1. Global market phase
    market_signal = total_phase.get('market_signal', 'UNKNOWN')
    btc_d = total_phase.get('btc_dominance', 0)
    
    if market_signal == 'BULL_MARKET':
        if btc_d > 55:
            parts.append(f'🚀 BULL market · BTC.D {btc_d:.0f}% (high) → alts suppressed')
        else:
            parts.append(f'🚀 BULL market · BTC.D {btc_d:.0f}% (rotating to alts)')
    elif market_signal == 'BEAR_MARKET':
        parts.append(f'🐻 BEAR market · risk-off phase')
    elif market_signal == 'PRE_BULL_ACCUMULATION':
        parts.append(f'🟡 PRE-BULL accumulation · smart money позиционируется')
    else:
        parts.append(f'❓ {market_signal.replace("_", " ")}')
    
    # 2. Macro context
    regime = macro.get('regime', {}).get('regime', 'UNKNOWN')
    dxy = macro.get('metrics', {}).get('dxy', {})
    if regime == 'RISK_ON':
        parts.append('Macro: risk-ON · капитал в crypto/акциях')
    elif regime == 'RISK_OFF':
        parts.append('Macro: risk-OFF · капитал в Treasuries/cash')
    elif dxy.get('direction') == 'falling':
        parts.append(f'Macro: MIXED, но DXY {dxy.get("change_pct")}% (bullish crypto medium-term)')
    else:
        parts.append(f'Macro: {regime}')
    
    # 3. STRK current state
    triggers = lab.get('triggers_hit', 0)
    strk_verdict = lab.get('verdict', '')
    if strk_verdict:
        parts.append(f'STRK: {strk_verdict} · {triggers}/4 triggers · {news.get("route_counts", {}).get("home", 0)} HOME news')
    
    # 4. Coherence warning
    if coherence_score < 50:
        parts.append(f'⚠ COHERENCE {coherence_score}% · сигналы противоречат друг другу ({len(conflicts)} conflicts)')
    elif coherence_score < 70:
        parts.append(f'~ COHERENCE {coherence_score}% · сигналы частично aligned')
    else:
        parts.append(f'✓ COHERENCE {coherence_score}% · сигналы aligned')
    
    return ' · '.join(parts)

# ============================================================
# UNIFIED ACTION
# ============================================================
def build_unified_action(signals, per_asset_verdicts, coherence_score):
    """Build actionable summary for TODAY."""
    actions = []
    
    strk_v = next((v for v in per_asset_verdicts if v.get('asset') == 'STRK'), None)
    hold_verdicts = [v for v in per_asset_verdicts if v.get('verdict') in ('HOLD', 'HOLD_STRONG', 'TRIM', 'REVIEW')]
    
    # STRK action
    if strk_v:
        if strk_v['verdict'] == 'FALSE_RALLY_WAIT':
            actions.append('⛔ STRK: НЕ входить на pump (false rally, thesis breach)')
        elif strk_v['verdict'] == 'ENTER':
            actions.append('✅ STRK: ENTER 25-50% размером')
        elif strk_v['verdict'] == 'STILL_ACCUMULATION':
            actions.append('⏸ STRK: WAIT для Spring signal (accumulation phase продолжается)')
        else:
            actions.append(f'~ STRK: {strk_v["verdict"]}')
    
    # HOLD tokens
    for v in hold_verdicts:
        if v['verdict'] == 'TRIM':
            actions.append(f'⚠ {v["asset"]}: trim позиции (bearish news + macro)')
        elif v['verdict'] == 'REVIEW':
            actions.append(f'👀 {v["asset"]}: review SIZE ({v["news_bearish_count"]} bearish events)')
        elif v['verdict'] == 'HOLD_STRONG':
            actions.append(f'✅ {v["asset"]}: HOLD strong (thesis alive)')
        elif v['verdict'] == 'HOLD':
            actions.append(f'📊 {v["asset"]}: HOLD (watch mode)')
    
    # Coherence warning
    if coherence_score < 50:
        actions.append(f'⚠ Coherence LOW ({coherence_score}%) — сигналы конфликтуют. Не делать big moves.')
    
    return actions

def build_what_to_watch(signals):
    """What key trigger to watch next."""
    watches = []
    total = signals.get('total_phase', {})
    btc_d = total.get('btc_dominance', 0)
    lab = signals.get('lab', {}).get('strk_status', {})
    triggers = lab.get('triggers_hit', 0)
    conf = signals.get('confluence', {})
    
    # BTC.D watch
    if btc_d > 55:
        watches.append({
            'text_ru': f'BTC.D сейчас {btc_d:.1f}% — watch пробой ниже 55% как сигнал начала alt season',
            'importance': 'high',
        })
    
    # STRK triggers watch
    if triggers < 4:
        watches.append({
            'text_ru': f'STRK triggers {triggers}/4 — watch следующие weekly rollups для достижения 3/4 или 4/4',
            'importance': 'high',
        })
    
    # Confluence watch
    rally = conf.get('rally_score', 0)
    if rally >= 5:
        watches.append({
            'text_ru': f'STRK confluence {rally}/9 rally · watch weekly close выше $0.0262 для confirmation',
            'importance': 'medium',
        })
    
    # News watch
    news = signals.get('news', {})
    home_events = news.get('home_events', [])
    if home_events:
        for e in home_events[:2]:
            watches.append({
                'text_ru': f'News HOME: {", ".join(e.get("assets", []))} · {e.get("type")}/{e.get("severity")} · {e.get("summary", "")[:80]}',
                'importance': 'medium',
            })
    
    return watches

# ============================================================
# MAIN
# ============================================================
def main():
    print('=== Interpretation Layer v1 ===\n')
    
    signals = {
        'macro': load('macro_narratives.json'),
        'total_phase': load('total_phase.json'),
        'lab': load('strk_lab_report.json'),
        'confluence': load('confluence_gate.json'),
        'news': load('surf_events.json'),
        'scenarios': load('strk_scenarios.json'),
        'netflow': load('dune_sector_netflow.json'),
    }
    
    print('Loaded signals:')
    for k, v in signals.items():
        print(f'  {k}: {"✓" if v else "✗ MISSING"}')
    
    # Analyze coherence
    print('\n=== Coherence Analysis ===')
    coherence_score, conflicts = analyze_coherence(signals)
    print(f'Coherence score: {coherence_score}%')
    print(f'Conflicts detected: {len(conflicts)}')
    for c in conflicts:
        print(f'  [{c["level"]}] {c["type"]}: {c["text_ru"][:100]}...')
    
    # Per-asset verdicts
    print('\n=== Per-Asset Verdicts ===')
    assets_to_check = ['STRK', 'BTC', 'ETH']
    hold_tokens = [x.get('token') for x in signals.get('lab', {}).get('strong_buy', [])]
    div_tokens = [x.get('token') for x in signals.get('lab', {}).get('divergence', [])]
    assets_to_check.extend(hold_tokens)
    assets_to_check.extend(div_tokens[:3])  # top 3 divergence
    assets_to_check = list(dict.fromkeys(assets_to_check))  # dedupe
    
    per_asset_verdicts = []
    for asset in assets_to_check:
        v = get_asset_verdict(asset, signals)
        if v:
            per_asset_verdicts.append(v)
            print(f'  {v["asset"]}: {v["verdict"]} ({v["signal_color"]})')
            print(f'    {v["recommendation_ru"][:120]}')
    
    # Context summary
    context_summary = build_context_summary(signals, coherence_score, conflicts)
    print(f'\n=== Context Summary ===')
    print(context_summary)
    
    # Unified action
    actions = build_unified_action(signals, per_asset_verdicts, coherence_score)
    print(f'\n=== Unified Action for TODAY ===')
    for a in actions:
        print(f'  {a}')
    
    # What to watch
    watches = build_what_to_watch(signals)
    print(f'\n=== What to Watch ===')
    for w in watches:
        print(f'  [{w["importance"]}] {w["text_ru"]}')
    
    # SAVE
    output = {
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'coherence_score': coherence_score,
        'context_summary_ru': context_summary,
        'conflicts': conflicts,
        'per_asset_verdicts': per_asset_verdicts,
        'unified_action_today': actions,
        'what_to_watch': watches,
        'sources_loaded': [k for k, v in signals.items() if v],
    }
    
    # ВАЖНО · имя файла изменено с interpretation.json на unified_reading.json.
    #
    # Причина: в data/cache/interpretation.json писали ДВА разных скрипта:
    #   scripts/detectors/interpretation_layer.py — старый, 10 паттернов Wyckoff,
    #       его читает scripts/daily_digest.py (ключ interpretation.primary)
    #   interpretation/interpretation_layer.py — этот, unified reading,
    #       его читает дашборд (ключи context_summary_ru, coherence_score,
    #       per_asset_verdicts, unified_action_today, what_to_watch)
    #
    # Старый запускается внутри composite и отрабатывает позже, поэтому
    # затирал результат нового. Дашборд получал чужую схему и рисовал
    # пустой блок Unified Reading.
    #
    # Файлы разведены. Старый скрипт не тронут — daily_digest продолжает
    # работать как работал.
    output_path = CACHE_DIR / 'unified_reading.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)
    print(f'\n✓ Written: {output_path}')

if __name__ == '__main__':
    main()
