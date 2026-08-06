#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
interpretation_layer.py — Narrative synthesis из всех сигналов

Читает все сигналы (wyckoff, cex_flow, cvd, effort, funding, event_layer,
cross_token, calendar, macro) и распознаёт КАНОНИЧЕСКИЕ ПАТТЕРНЫ.

Для каждого паттерна:
  · Conditions (что должно совпасть)
  · Weight (насколько сильный сигнал)
  · Hypothesis (что происходит)
  · Narrative (объяснение)
  · Position hint (что делать / не делать)
  · Triggers (за чем следить)
  · Invalidation (когда гипотеза сломана)

15 канонических паттернов:
  1.  PHASE_E_TOP        — Wyckoff Phase E (top formation)
  2.  ACCUMULATION_BASE  — Phase B building base
  3.  SHORT_SQUEEZE_FUEL — deep drop + extreme shorts
  4.  DISTRIBUTION_TO_RETAIL — smart selling to retail
  5.  GENUINE_BREAKOUT   — real move up
  6.  PRE_UNLOCK_WEAKNESS — supply shock ahead
  7.  POST_UNLOCK_RECOVERY — oversold post-unlock
  8.  SECTOR_ROTATION_OUT — L2 underperform
  9.  SECTOR_ROTATION_IN — L2 outperform  
  10. STEALTH_ACCUMULATION — quiet smart buying
  11. DISTRIBUTION_PEAK   — topping structure
  12. CAPITULATION       — panic bottom
  13. BULL_TRAP          — false rally
  14. BEAR_TRAP          — false breakdown
  15. RANGE_BOUND        — sideways
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'interpretation.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('interp')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def gather_signals():
    """Собирает все входные сигналы в единый context."""
    wyckoff = load_json('wyckoff_phase.json') or {}
    tech = load_json('technical_momentum.json') or {}
    funding = load_json('funding_signal.json') or {}
    cex_flow = load_json('cex_flow.json') or {}
    concentration = load_json('concentration_metrics.json') or {}
    effort = load_json('effort_result.json') or {}
    cvd = load_json('cvd_analysis.json') or {}
    event_layer = load_json('event_layer.json') or {}
    calendar = load_json('event_calendar.json') or {}
    cross_token = load_json('cross_token_correlation.json') or {}
    agent_input = load_json('agent_input.json') or {}
    
    tf = tech.get('features', {})
    fm = funding.get('funding_metrics', {})
    btc = agent_input.get('btc', {}) or (load_json('composite_signal_v2.json') or {}).get('inputs', {}).get('btc_context', {})
    
    ctx = {
        # Wyckoff
        'phase': wyckoff.get('phase', 'UNKNOWN'),
        'sub_phase': wyckoff.get('sub_phase', ''),
        'wyckoff_conf': wyckoff.get('confidence', 'LOW'),
        
        # Price / Technical
        'price': tf.get('price_now', tf.get('price', 0)),
        'high_14d': tf.get('high_14d', 0),
        'low_14d': tf.get('low_14d', 0),
        'slope_3d': tf.get('slope_3d_pct', 0),
        'slope_accel': tf.get('slope_accel_pct', 0),
        'vol_ratio_3d': tf.get('vol_ratio_3d_vs_30d', 1),
        'rsi': tf.get('rsi', 50),
        'pct_from_high': tf.get('pct_from_high', 0),
        'pct_from_low': tf.get('pct_from_low', 0),
        
        # Funding / Shorts
        'funding_apr': fm.get('funding_apr_pct', 0),
        'funding_min_7d': fm.get('funding_min_7d_pct', fm.get('min_ann_7d', 0)),
        'short_crowded': fm.get('short_crowded', False),
        'long_crowded': fm.get('long_crowded', False),
        
        # On-chain
        'hhi': concentration.get('hhi', 0.05),
        'entropy': concentration.get('shannon_entropy_bits', 5),
        'large_receivers_14d': concentration.get('large_receivers_14d', 0),
        'top5_share': concentration.get('top_5_share_pct', 0),
        
        # CEX Flow
        'cex_signal': cex_flow.get('classification', {}).get('signal', 'NEUTRAL'),
        'cex_net_7d': cex_flow.get('net_flow_strk', 0),
        'cex_consecutive': cex_flow.get('consecutive_inflow_days', 0),
        
        # Effort/Result + CVD
        'effort_signal': effort.get('signal', 'NEUTRAL'),
        'cvd_signal': cvd.get('signal', 'NEUTRAL'),
        
        # Event Layer
        'event_signal': event_layer.get('signal', 'CALM'),
        'event_bull': event_layer.get('bullish_score', 0),
        'event_bear': event_layer.get('bearish_score', 0),
        
        # Cross-token
        'sector_signal': cross_token.get('signal', 'NEUTRAL'),
        'strk_alpha_7d': cross_token.get('strk_alpha', {}).get('alpha_7d_pct', 0),
        
        # Calendar
        'days_to_unlock': calendar.get('days_to_next_unlock', 999),
        'unlock_amount': (calendar.get('next_unlock') or {}).get('amount', 0),
        'supply_30d': calendar.get('supply_added_30d', 0),
        
        # BTC / Macro
        'btc_price': btc.get('price', btc.get('btc_price', 0)),
        'btc_cycle': btc.get('cycle', btc.get('btc_cycle', 'UNKNOWN')),
        'btc_dist200': btc.get('dist200_pct', 0),
    }
    return ctx


# ============================================================
# КАНОНИЧЕСКИЕ ПАТТЕРНЫ
# Каждый паттерн: match(ctx) → confidence 0-100 или None
# ============================================================

def pattern_phase_e_top(ctx):
    """PHASE E TOP FORMATION — распределение крупными на локальном топе.
    
    Wyckoff Phase E = markup terminates, crypto distributes to eager retail.
    Классические признаки: 
      · MARKUP phase на дневке
      · CEX distribution 3+ дня подряд (крупные шлют на биржи)
      · CVD stealth distribution (розница покупает лимитками, крупные — рынком)
      · Short crowded (все ждут падения — контра-индикатор в краткосроке)
      · Volume дряблый
    """
    if ctx['phase'] not in ('MARKUP',):
        return None
    if 'DISTRIBUTION' not in ctx['cex_signal']:
        return None
    if ctx['cex_consecutive'] < 3:
        return None
    
    score = 60
    if 'STEALTH_DISTRIBUTION' in ctx['cvd_signal'] or 'BEARISH' in ctx['cvd_signal']:
        score += 15
    if ctx['short_crowded']:
        score += 10  # counter-intuitive but historically bearish for topping
    if ctx['vol_ratio_3d'] < 0.7:
        score += 5   # dry volume at top = weakness
    if ctx['days_to_unlock'] <= 14:
        score += 10  # supply pressure ahead
    
    return {
        'name': 'PHASE_E_TOP',
        'label': 'Phase E · top formation',
        'confidence': min(score, 90),
        'direction': 'BEARISH_MID',  # bearish medium-term, may squeeze first
        'hypothesis': f'Крупные распределяют на локальном пике. CEX inflow {ctx["cex_consecutive"]} дней подряд ({ctx["cex_net_7d"]/1e6:+.1f}M net). Wyckoff MARKUP переходит в distribution.',
        'narrative': (
            f'Классическая Phase E: Wyckoff показывает MARKUP, но on-chain говорит противоположное — '
            f'крупные {ctx["cex_consecutive"]} дней подряд шлют STRK на биржи ({ctx["cex_signal"]}). '
            f'CVD говорит retail покупает лимитками ({ctx["cvd_signal"]}), а крупные продают market-orders. '
            f'Shorts crowded ({ctx["funding_apr"]:+.1f}% APR) — все ждут падения, что даёт топливо для короткого squeeze, '
            f'но структурно это разгрузка на розницу.'
            + (f' Через {ctx["days_to_unlock"]}д unlock ({ctx["unlock_amount"]/1e6:.0f}M STRK) — supply shock приближается.' if ctx['days_to_unlock'] <= 14 else '')
        ),
        'position_hint': 'НЕ входить в longs здесь. Возможен squeeze +5-10% в ближайшие дни, но rally scarcity. Ждать либо (a) сквиз выше сопротивления и его пробой ВНИЗ = short signal, либо (b) reset после unlock и re-entry в accumulation.',
        'triggers_watch': [
            f'Пробой выше ${ctx["high_14d"]:.4f} на volume 1.5×+ = squeeze активирован',
            f'Пробой ниже ${ctx["low_14d"]:.4f} = distribution подтверждён',
            f'CEX flow flip to OUTFLOW = крупные перестали разгружаться',
        ],
        'invalidation': f'Если CEX flow развернётся в OUTFLOW 3+ дня подряд И пробой $${ctx["high_14d"]:.4f} на объёме — паттерн отменён, вероятен genuine breakout.',
    }


def pattern_accumulation_base(ctx):
    """ACCUMULATION BASE BUILDING — Phase B тихое накопление."""
    if ctx['phase'] != 'ACCUMULATION':
        return None
    if ctx['sub_phase'] and 'Phase B' not in ctx['sub_phase']:
        return None
    
    score = 55
    if abs(ctx['funding_apr']) < 5:
        score += 10  # neutral funding = no crowd
    if 'DISTRIBUTION' not in ctx['cex_signal']:
        score += 10
    if ctx['vol_ratio_3d'] < 1.2:
        score += 5   # low volume typical for Phase B
    if ctx['event_signal'] not in ('NEGATIVE_CATALYST',):
        score += 5
    
    return {
        'name': 'ACCUMULATION_BASE',
        'label': 'Accumulation · Phase B · base building',
        'confidence': min(score, 85),
        'direction': 'NEUTRAL_TO_BULLISH',
        'hypothesis': 'Крупные тихо аккумулируют. Розница пассивна. Volatility сжимается.',
        'narrative': (
            f'Phase B — building phase. Цена в узком range {ctx["low_14d"]:.4f}-{ctx["high_14d"]:.4f}. '
            f'Volume {ctx["vol_ratio_3d"]:.1f}× среднего — сухо. Funding {ctx["funding_apr"]:+.1f}% почти нейтральный. '
            f'CEX flow: {ctx["cex_signal"]}. Обычно фаза длится 2-8 недель до подтверждения markup.'
        ),
        'position_hint': f'Wait. Не входить пока не увидим Sign of Strength (SOS): пробой выше ${ctx["high_14d"]:.4f} на volume expansion 1.5×+.',
        'triggers_watch': [
            f'Break ABOVE ${ctx["high_14d"]:.4f} on volume 1.5×+ = SOS, markup starts',
            f'Break BELOW ${ctx["low_14d"]:.4f} on volume = Spring failure, deeper accumulation',
            'CEX flow flip to OUTFLOW 3+ days = whales accumulating too',
        ],
        'invalidation': f'Если цена уйдёт ниже ${ctx["low_14d"]:.4f} без быстрого возврата — фаза продлевается или переходит в markdown.',
    }


def pattern_genuine_breakout(ctx):
    """GENUINE BREAKOUT — real markup, Sign of Strength подтверждён.
    
    Признаки настоящего breakout из accumulation в markup:
      · Wyckoff Phase D (Sign of Strength)
      · Slope 3d > +3% + accelerating
      · Volume 1.5× среднего или выше
      · CVD подтверждает (BULLISH)
      · CEX flow не distribution (или accumulation)
      · Event layer positive
      · BTC UP cycle (или в первой фазе UP)
    """
    if ctx['slope_3d'] < 3:
        return None
    if ctx['vol_ratio_3d'] < 1.4:
        return None
    if 'BEARISH' in ctx.get('cvd_signal', ''):
        return None
    
    score = 55
    if ctx['phase'] == 'MARKUP':
        score += 10
    if 'Phase D' in ctx.get('sub_phase', '') or 'sign of strength' in ctx.get('sub_phase', '').lower():
        score += 15
    if 'DISTRIBUTION' not in ctx['cex_signal']:
        score += 10
    if ctx['event_signal'] in ('POSITIVE_CATALYST', 'SLIGHT_BULLISH'):
        score += 10
    if ctx['btc_cycle'] == 'UP':
        score += 5
    if ctx['funding_apr'] > 0 and ctx['funding_apr'] < 15:
        score += 5  # positive but not extreme = healthy
    
    return {
        'name': 'GENUINE_BREAKOUT',
        'label': 'Genuine breakout · Sign of Strength',
        'confidence': min(score, 85),
        'direction': 'BULLISH_MID',
        'hypothesis': f'Настоящий markup: slope +{ctx["slope_3d"]:.1f}% на volume {ctx["vol_ratio_3d"]:.1f}× среднего, CVD confirms.',
        'narrative': (
            f'Cлайп +{ctx["slope_3d"]:.1f}% за 3 дня с acceleration +{ctx["slope_accel"]:.1f}%, '
            f'volume {ctx["vol_ratio_3d"]:.1f}× среднего. CVD: {ctx["cvd_signal"]}. '
            f'CEX flow: {ctx["cex_signal"]}. Event layer: {ctx["event_signal"]}. '
            f'Всё сходится — это не bull trap, а real move.'
        ),
        'position_hint': f'Long entries valid. Buy pullbacks к $${ctx["price"]*0.97:.4f} area с stop под ${ctx["low_14d"]:.4f}. Trail higher lows.',
        'triggers_watch': [
            f'Continued vol expansion + higher highs = trend intact',
            'CEX flow flip to distribution = exhaustion warning',
            'Funding > +30% ann = long crowded, take partial profit',
        ],
        'invalidation': f'Break BELOW recent HL с closing на volume = breakout failed.',
    }


def pattern_short_squeeze_fuel(ctx):
    """SHORT SQUEEZE SETUP — deep drop + extreme shorts + support."""
    if not ctx['short_crowded']:
        return None
    if ctx['pct_from_high'] > -15:  # not deep enough
        return None
    if ctx['funding_min_7d'] > -8:  # shorts не extreme
        return None
    
    score = 55
    if ctx['pct_from_high'] < -25:
        score += 15
    if ctx['funding_min_7d'] < -15:
        score += 10
    if ctx['pct_from_low'] > 3:  # started bouncing
        score += 10
    if ctx['btc_cycle'] != 'DOWN':
        score += 10
    
    return {
        'name': 'SHORT_SQUEEZE_FUEL',
        'label': 'Short squeeze setup',
        'confidence': min(score, 80),
        'direction': 'BULLISH_SHORT',
        'hypothesis': f'Extreme short crowding после падения {ctx["pct_from_high"]:+.1f}% от 14d high. Топливо для squeeze +5-15%.',
        'narrative': (
            f'Funding min за 7 дней: {ctx["funding_min_7d"]:+.1f}% ann — шорты платят премиум чтобы удержать позиции. '
            f'Цена {ctx["pct_from_high"]:+.1f}% от high, {ctx["pct_from_low"]:+.1f}% от low. '
            f'Классический setup для short squeeze — любой positive trigger может выбить шортов из позиций и создать rally 5-15%. '
            f'BTC cycle: {ctx["btc_cycle"]} — {"попутный ветер" if ctx["btc_cycle"] == "UP" else "против ветра"}.'
        ),
        'position_hint': 'Squeeze возможен но risky (нужен trigger). Если long — small size, стоп ниже недавнего low. Основная позиция — wait.',
        'triggers_watch': [
            f'Break ABOVE $${ctx["high_14d"]*0.98:.4f} on volume = squeeze активирован',
            'Funding flip to positive = shorts capitulate',
            'BTC bounces >3% в день = risk-on tailwind',
        ],
        'invalidation': f'Break BELOW ${ctx["low_14d"]:.4f} = shorts правы, продолжение markdown.',
    }


def pattern_pre_unlock_weakness(ctx):
    """PRE-UNLOCK WEAKNESS — supply shock ahead."""
    if ctx['days_to_unlock'] > 14:
        return None
    if ctx['unlock_amount'] < 50_000_000:
        return None
    
    score = 60
    if 'DISTRIBUTION' in ctx['cex_signal']:
        score += 15
    if ctx['days_to_unlock'] <= 7:
        score += 10
    if ctx['unlock_amount'] > 150_000_000:
        score += 10
    
    return {
        'name': 'PRE_UNLOCK_WEAKNESS',
        'label': f'Pre-unlock weakness ({ctx["days_to_unlock"]}d)',
        'confidence': min(score, 85),
        'direction': 'BEARISH_SHORT',
        'hypothesis': f'Через {ctx["days_to_unlock"]} дней unlock {ctx["unlock_amount"]/1e6:.0f}M STRK. Крупные разгружаются заранее.',
        'narrative': (
            f'Supply shock приближается: {ctx["unlock_amount"]/1e6:.0f}M STRK unlock через {ctx["days_to_unlock"]}д. '
            f'Крупные обычно разгружаются за 3-7 дней до события чтобы получить лучшую цену. '
            f'CEX flow: {ctx["cex_signal"]}. '
            f'Historically STRK показывает slower price для 5-10 дней до unlock, затем relief bounce.'
        ),
        'position_hint': 'Not the time for longs. Если уже long — trail stop плотно. Consider hedge/reduce перед unlock.',
        'triggers_watch': [
            f'Day-of-unlock volume spike = capitulation zone (bounce entry)',
            f'CEX flow flip to OUTFLOW = unlock уже отработан',
            f'Price recovery выше pre-announcement level = supply absorbed',
        ],
        'invalidation': 'Если CEX flow flip to OUTFLOW за 3+ дня до unlock — supply уже poured out, паттерн отменён.',
    }


def pattern_sector_rotation_out(ctx):
    """SECTOR ROTATION OUT — STRK worse than L2 peers."""
    if ctx['sector_signal'] != 'STRK_UNDERPERFORMING':
        return None
    if ctx['strk_alpha_7d'] > -3:
        return None
    
    score = 60
    if ctx['strk_alpha_7d'] < -8:
        score += 15
    if 'DISTRIBUTION' in ctx['cex_signal']:
        score += 10
    if ctx['days_to_unlock'] <= 21:
        score += 10
    
    return {
        'name': 'SECTOR_ROTATION_OUT',
        'label': 'L2 sector rotation OUT of STRK',
        'confidence': min(score, 80),
        'direction': 'BEARISH_MID',
        'hypothesis': f'STRK alpha {ctx["strk_alpha_7d"]:+.1f}% vs L2 sector — деньги уходят из STRK специфически, не broad L2 weakness.',
        'narrative': (
            f'STRK 7d показывает {ctx["strk_alpha_7d"]:+.1f}% относительно L2 сектора. '
            f'Это не macro — это STRK-специфический sell-off. ARB, OP, ZK ведут себя иначе. '
            f'Причины обычно: (a) upcoming supply pressure, (b) narrative shift, (c) whale distribution.'
            + (f' Unlock через {ctx["days_to_unlock"]}д может быть ключевым фактором.' if ctx['days_to_unlock'] <= 21 else '')
        ),
        'position_hint': 'Long STRK здесь — против тренда сектора. Wait либо для sector rotation IN, либо для capitulation setup.',
        'triggers_watch': [
            'STRK alpha 7d > +2% = rotation reversal',
            'CEX flow flip OUTFLOW',
            'Positive catalyst (upgrade, partnership, listing)',
        ],
        'invalidation': 'Alpha 7d flip to positive + BTC UP cycle = rotation IN, паттерн сброшен.',
    }


def pattern_bull_trap(ctx):
    """BULL TRAP — false rally признаки."""
    if ctx['slope_3d'] < 2:
        return None
    if ctx['cvd_signal'] not in ('BEARISH_LEAN', 'STEALTH_DISTRIBUTION', 'BEARISH_DIVERGENCE'):
        return None
    
    score = 55
    if 'DISTRIBUTION' in ctx['cex_signal']:
        score += 15
    if ctx['funding_apr'] > 5:  # turning positive = longs piling in
        score += 10
    if ctx['event_signal'] in ('NEGATIVE_CATALYST', 'SLIGHT_BEARISH'):
        score += 10
    
    return {
        'name': 'BULL_TRAP',
        'label': 'Potential bull trap',
        'confidence': min(score, 75),
        'direction': 'BEARISH_SHORT',
        'hypothesis': 'Цена растёт но структурные сигналы против. Retail покупает эйфорию, крупные разгружаются.',
        'narrative': (
            f'Slope 3d {ctx["slope_3d"]:+.1f}% выглядит как rally. Но CVD показывает {ctx["cvd_signal"]}: '
            f'настоящие покупатели отсутствуют, cover buying шортов и retail FOMO двигают цену. '
            f'CEX flow: {ctx["cex_signal"]}, funding {ctx["funding_apr"]:+.1f}% ann — longs набегают на топ. '
            f'Event layer: {ctx["event_signal"]}.'
        ),
        'position_hint': 'НЕ гнаться за pump здесь. Если long — trail stop плотно. Возможна fade выше resistance.',
        'triggers_watch': [
            f'Failure ниже ${ctx["high_14d"]*0.97:.4f} после rejection = trap подтверждён',
            'CVD flip to positive = real buyers пришли',
            'CEX flow OUTFLOW 3+ days = smart money заканчивает разгрузку',
        ],
        'invalidation': 'CVD flip to bullish + CEX outflow 3+ дней = real breakout, trap отменён.',
    }


def pattern_stealth_accumulation(ctx):
    """STEALTH ACCUMULATION — quiet smart buying."""
    if abs(ctx['slope_3d']) > 3:
        return None
    if ctx['vol_ratio_3d'] > 1.3:
        return None
    if ctx['effort_signal'] not in ('QUIET_ACCUMULATION', 'ABSORPTION_ACCUMULATION'):
        return None
    
    score = 55
    if 'DISTRIBUTION' not in ctx['cex_signal']:
        score += 15
    if ctx['event_signal'] in ('POSITIVE_CATALYST', 'SLIGHT_BULLISH'):
        score += 10
    if ctx['days_to_unlock'] > 30:
        score += 10
    
    return {
        'name': 'STEALTH_ACCUMULATION',
        'label': 'Stealth accumulation',
        'confidence': min(score, 75),
        'direction': 'BULLISH_MID',
        'hypothesis': 'Крупные тихо покупают. Розница не замечает. Цена flat, volume low.',
        'narrative': (
            f'Effort/Result: {ctx["effort_signal"]} — низкое effort с положительным result. '
            f'Volume {ctx["vol_ratio_3d"]:.1f}× среднего, цена {ctx["slope_3d"]:+.1f}% за 3 дня — тишина. '
            f'Классические признаки тихого накопления перед markup.'
        ),
        'position_hint': 'Ok начать scale-in малым размером. Full position — после подтверждения через breakout.',
        'triggers_watch': [
            f'Volume spike + break ABOVE ${ctx["high_14d"]:.4f}',
            'Positive catalyst news',
            'CEX outflow acceleration',
        ],
        'invalidation': f'Break BELOW ${ctx["low_14d"]:.4f} без recovery = accumulation invalidated.',
    }


def pattern_capitulation(ctx):
    """CAPITULATION — panic bottom setup."""
    if ctx['pct_from_high'] > -30:
        return None
    if ctx['vol_ratio_3d'] < 1.5:  # need volume for capitulation
        return None
    
    score = 55
    if ctx['funding_min_7d'] < -20:
        score += 15
    if ctx['rsi'] < 30:
        score += 10
    if ctx['pct_from_low'] > 2:  # bouncing from low
        score += 15
    
    return {
        'name': 'CAPITULATION',
        'label': 'Capitulation / bottom fishing',
        'confidence': min(score, 80),
        'direction': 'BULLISH_MID',
        'hypothesis': f'Panic sell exhaustion после {ctx["pct_from_high"]:+.1f}% drop с 14d high.',
        'narrative': (
            f'Дно ищется: цена {ctx["pct_from_high"]:+.1f}% от high, RSI {ctx["rsi"]:.0f}, volume {ctx["vol_ratio_3d"]:.1f}× — capitulation признаки. '
            f'Funding min: {ctx["funding_min_7d"]:+.1f}% — шорты overloaded. Bounce setup.'
        ),
        'position_hint': 'Scale-in longs малыми частями. НЕ all-in — capitulation может продолжиться day-two.',
        'triggers_watch': [
            f'Bounce > 5% с текущих уровней = bottom confirmed',
            'Funding flip positive = shorts covered',
            'BTC bounces = macro tailwind',
        ],
        'invalidation': f'Break BELOW {ctx["low_14d"]:.4f} on new high volume = capitulation ещё не закончилась.',
    }


def pattern_range_bound(ctx):
    """RANGE BOUND — sideways."""
    if abs(ctx['slope_3d']) > 4:
        return None
    if ctx['pct_from_high'] < -8 or ctx['pct_from_low'] > 8:
        return None
    
    score = 40
    if abs(ctx['funding_apr']) < 5:
        score += 10
    if ctx['vol_ratio_3d'] < 1.2:
        score += 10
    if ctx['event_signal'] == 'CALM':
        score += 10
    
    return {
        'name': 'RANGE_BOUND',
        'label': 'Range-bound / sideways',
        'confidence': min(score, 70),
        'direction': 'NEUTRAL',
        'hypothesis': 'Consolidation. Ни быки ни медведи не берут инициативу.',
        'narrative': (
            f'Цена в узком range: {ctx["low_14d"]:.4f}-{ctx["high_14d"]:.4f}. Volatility сжата. '
            f'Ждём breakout в любую сторону.'
        ),
        'position_hint': 'Wait. Trade range trades (buy support, sell resistance) с малым размером, или подожди пробоя.',
        'triggers_watch': [
            f'Break ABOVE {ctx["high_14d"]:.4f} on volume = trend up',
            f'Break BELOW {ctx["low_14d"]:.4f} on volume = trend down',
        ],
        'invalidation': 'Direct trend break — паттерн заменяется на trend pattern.',
    }


ALL_PATTERNS = [
    pattern_phase_e_top,
    pattern_accumulation_base,
    pattern_short_squeeze_fuel,
    pattern_pre_unlock_weakness,
    pattern_sector_rotation_out,
    pattern_bull_trap,
    pattern_stealth_accumulation,
    pattern_capitulation,
    pattern_range_bound,
    pattern_genuine_breakout,  # NEW
]


def synthesize():
    """Main interpretation logic."""
    ctx = gather_signals()
    
    # Test all patterns
    matches = []
    for pattern_fn in ALL_PATTERNS:
        try:
            result = pattern_fn(ctx)
            if result:
                matches.append(result)
        except Exception as e:
            logger.debug(f"Pattern {pattern_fn.__name__} error: {e}")
    
    # Sort by confidence
    matches.sort(key=lambda x: -x['confidence'])
    
    # Primary hypothesis = highest confidence pattern
    primary = matches[0] if matches else None
    secondary = matches[1] if len(matches) > 1 else None
    tertiary = matches[2] if len(matches) > 2 else None
    
    # Build final interpretation
    if not primary:
        interpretation = {
            'primary': None,
            'summary': 'Нет чёткого паттерна. Данные противоречивы или недостаточны.',
            'position_hint': 'STAY FLAT. Wait for pattern to emerge.',
            'triggers': [],
        }
    else:
        interpretation = {
            'primary': primary,
            'secondary': secondary,
            'tertiary': tertiary,
            'summary': primary['narrative'],
            'position_hint': primary['position_hint'],
            'triggers': primary['triggers_watch'],
            'invalidation': primary.get('invalidation', ''),
        }
    
    return {
        'as_of': datetime.now(timezone.utc).isoformat(),
        'context_snapshot': ctx,
        'patterns_matched': len(matches),
        'interpretation': interpretation,
        'all_matches': matches,
    }


def main():
    logger.info("=" * 60)
    logger.info("INTERPRETATION LAYER · narrative synthesis")
    logger.info("=" * 60)
    
    result = synthesize()
    
    logger.info(f"\nPatterns matched: {result['patterns_matched']}")
    
    interp = result['interpretation']
    if interp['primary']:
        p = interp['primary']
        logger.info(f"\n=== PRIMARY: {p['label']} ===")
        logger.info(f"Confidence: {p['confidence']}%")
        logger.info(f"Direction: {p['direction']}")
        logger.info(f"Hypothesis: {p['hypothesis']}")
        logger.info(f"\nNarrative:")
        logger.info(f"  {p['narrative']}")
        logger.info(f"\nPosition: {p['position_hint']}")
        logger.info(f"\nWatch:")
        for t in p['triggers_watch']:
            logger.info(f"  · {t}")
        logger.info(f"\nInvalidation: {p.get('invalidation', '—')}")
    else:
        logger.info(interp['summary'])
    
    if interp.get('secondary'):
        logger.info(f"\n=== SECONDARY: {interp['secondary']['label']} ({interp['secondary']['confidence']}%) ===")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
