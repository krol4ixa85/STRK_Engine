#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario_engine.py — Proactive scenario analysis (Bull/Base/Bear)

Второй режим STRK Engine — сценарии на 7-30 дней вперёд.

Логика:
  · Для каждого сценария: вероятность, катализатор, price target, risk
  · Базируется на текущей фазе + macro + unlock schedule
  · Сценарии обновляются раз в неделю (пятница) + при trigger events

Три сценария:
  · BASE case (60-70% вероятность): что вероятно случится
  · BULL case (10-20%): позитивный шок сценарий
  · BEAR case (15-25%): негативный шок сценарий

Каждый сценарий содержит:
  · Trigger event (что должно случиться)
  · Price target range
  · Timeframe (когда)
  · Key metrics to watch
  · Position sizing hint

Пример:
  BASE (65%): STRK держится $0.024-0.027, distribution продолжается, 
              переход в MARKDOWN через 2-3 недели после unlock 15-oct-2026.
              Watch: LARGE receivers > 100.
  BULL (15%): Short squeeze $0.030-0.035 если BTC пробьёт $70k.
              Trigger: BTC 7d slope > +5%.
  BEAR (20%): Cascade к $0.019-0.021 если BTC уйдёт под $60k
              или unlock 200M вызовет sell pressure.
"""

import os
import sys
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OUTPUT_FILE = CACHE_DIR / 'scenario_analysis.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('scenario')


def load_json(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def build_scenarios():
    """Build Base/Bull/Bear scenarios from current state."""
    now = datetime.now(timezone.utc)
    
    # Load all inputs
    wyckoff = load_json('wyckoff_phase.json')
    composite = load_json('composite_signal_v2.json')
    funding = load_json('funding_signal.json')
    unlock = load_json('unlock_signal.json')
    cex_flow = load_json('cex_flow.json')
    concentration = load_json('concentration_metrics.json')
    # NEW: Fallback data sources when composite/wyckoff not ready yet
    tech_mom = load_json('technical_momentum.json')
    agent_input = load_json('agent_input.json')
    event_cal = load_json('event_calendar.json')
    
    # Extract current state (with fallback chain)
    phase = wyckoff.get('phase', 'UNKNOWN') if wyckoff else 'UNKNOWN'
    sub_phase = wyckoff.get('sub_phase', '') if wyckoff else ''
    confidence = wyckoff.get('confidence', 'LOW') if wyckoff else 'LOW'
    
    # Price: try wyckoff.technical, then technical_momentum.features
    tech = wyckoff.get('technical', {}) if wyckoff else {}
    price = tech.get('price_now', 0)
    high_14d = tech.get('high_14d', 0)
    low_14d = tech.get('low_14d', 0)
    
    # Fallback to technical_momentum.json (always fresh, no dependency)
    if price == 0 and tech_mom:
        tm_features = tech_mom.get('features', {})
        price = tm_features.get('price_now', 0)  # key is price_now, not price
        high_14d = tm_features.get('high_14d', 0)
        low_14d = tm_features.get('low_14d', 0)
    
    # If STILL no price, we cannot build meaningful scenarios
    if price == 0:
        logger.error("No price data available (wyckoff, technical_momentum both empty)")
        logger.error("Scenario engine skipped — will retry next cycle")
        return None
    
    # BTC context: try composite, then agent_input, then event_calendar
    btc_data = {}
    if composite and composite.get('inputs'):
        btc_data = composite['inputs'].get('btc_context', {}) or {}
    if not btc_data and agent_input:
        btc_data = agent_input.get('btc', {}) or {}
    
    btc_cycle = btc_data.get('cycle', btc_data.get('btc_cycle', 'UNKNOWN'))
    btc_price = btc_data.get('btc_price', btc_data.get('price', 0))
    btc_dist200 = btc_data.get('dist200_pct', 0)
    
    # Funding
    fm = funding.get('funding_metrics', {}) if funding else {}
    short_crowded = fm.get('short_crowded', False)
    long_crowded = fm.get('long_crowded', False)
    funding_min_7d = fm.get('min_ann_7d', 0)
    
    # Unlock (try unlock_signal first, then event_calendar)
    unlock_info = unlock.get('unlock_info', {}) if unlock else {}
    days_to_unlock = unlock_info.get('days_to_next_unlock', 999)
    unlock_amount = unlock_info.get('next_unlock_amount_strk', 0)
    
    # Fallback: event_calendar always has this
    if days_to_unlock == 999 and event_cal:
        days_to_unlock = event_cal.get('days_to_next_unlock', 999)
        next_unlock = event_cal.get('next_unlock', {})
        if next_unlock:
            unlock_amount = next_unlock.get('amount', 0)
    
    # CEX flow
    cex_signal = 'NEUTRAL'
    if cex_flow:
        cex_signal = cex_flow.get('classification', {}).get('signal', 'NEUTRAL')
    
    # === BUILD SCENARIOS ===
    scenarios = []
    
    # =============== BASE CASE ===============
    base = build_base_scenario(phase, price, high_14d, low_14d, btc_cycle,
                                short_crowded, days_to_unlock, cex_signal)
    scenarios.append(base)
    
    # =============== BULL CASE ===============
    bull = build_bull_scenario(phase, price, high_14d, low_14d, btc_cycle,
                                btc_dist200, short_crowded, funding_min_7d)
    scenarios.append(bull)
    
    # =============== BEAR CASE ===============
    bear = build_bear_scenario(phase, price, high_14d, low_14d, btc_cycle,
                                btc_dist200, days_to_unlock, unlock_amount, cex_signal)
    scenarios.append(bear)
    
    # Normalize probabilities to 100
    total_prob = sum(s['probability_pct'] for s in scenarios)
    if total_prob != 100 and total_prob > 0:
        for s in scenarios:
            s['probability_pct'] = round(s['probability_pct'] / total_prob * 100)
    
    return {
        'as_of': now.isoformat(),
        'valid_until': (now + timedelta(days=7)).isoformat(),
        'current_state': {
            'phase': phase,
            'sub_phase': sub_phase,
            'confidence': confidence,
            'price': price,
            'btc_cycle': btc_cycle,
            'btc_price': btc_price,
            'short_crowded': short_crowded,
            'long_crowded': long_crowded,
            'days_to_next_unlock': days_to_unlock,
            'next_unlock_strk': unlock_amount,
            'cex_flow_signal': cex_signal,
        },
        'scenarios': scenarios,
    }


def build_base_scenario(phase, price, high_14d, low_14d, btc_cycle, short_crowded, days_to_unlock, cex_signal):
    """Most likely path forward."""
    # Range prediction based on phase
    if phase == 'ACCUMULATION':
        low = round(low_14d * 0.98, 4)
        high = round(price * 1.10, 4)
        narrative = 'Продолжение аккумуляции в диапазоне. Крупные покупают на dip, розница пассивна.'
        catalyst = 'Постепенное building base 2-6 недель до подтверждения markup.'
        prob = 60
    elif phase == 'DISTRIBUTION':
        low = round(price * 0.92, 4)
        high = round(high_14d * 1.02, 4)
        narrative = 'Дистрибьюция продолжается. Whales сбрасывают в rally, ждём слома поддержки.'
        catalyst = 'Sign of Weakness при неудаче удержать support. Затем markdown.'
        prob = 65
    elif phase == 'MARKUP':
        low = round(price * 0.95, 4)
        high = round(high_14d * 1.15, 4)
        narrative = 'Тренд вверх продолжается. HH/HL структура сохраняется.'
        catalyst = 'Trail stops по higher lows. Выход когда объём растёт при плоской цене.'
        prob = 55
    elif phase == 'MARKDOWN':
        low = round(low_14d * 0.85, 4)
        high = round(price * 1.03, 4)
        narrative = 'Падение продолжается. Lower highs, lower lows.'
        catalyst = 'Ждать капитуляции: extreme neg funding + volume spike + 7 дней без новых лоу.'
        prob = 60
    else:
        low = round(low_14d, 4)
        high = round(high_14d, 4)
        narrative = 'Sideways. Ждать подтверждения фазы.'
        catalyst = 'Watch for phase determination.'
        prob = 50
    
    # Adjust prob based on unlock proximity
    if days_to_unlock < 30 and phase in ('DISTRIBUTION', 'MARKDOWN'):
        prob += 5
        narrative += f' Unlock через {days_to_unlock} дней добавит давления.'
    
    return {
        'type': 'BASE',
        'label': f'Продолжение {phase.lower()}',
        'probability_pct': prob,
        'price_range': [low, high],
        'timeframe_days': 14,
        'narrative': narrative,
        'catalyst': catalyst,
        'position_hint': 'Stay flat. Не открывать directional trade без confirmation.',
    }


def build_bull_scenario(phase, price, high_14d, low_14d, btc_cycle, btc_dist200, short_crowded, funding_min_7d):
    """Positive shock scenario."""
    # Bull upside based on setup
    if short_crowded and funding_min_7d < -10:
        # Short squeeze setup
        bull_low = round(price * 1.05, 4)
        bull_high = round(high_14d * 1.25, 4)
        narrative = f'Short squeeze rally. Все шорты в панике закрываются, funding {funding_min_7d:+.0f}% signals fuel.'
        catalyst = 'Trigger: BTC breaks +3% в 24ч, или positive STRK news (partnership, upgrade).'
        prob = 18
    elif phase == 'ACCUMULATION' and btc_cycle in ('UP', 'DOWN_REVERSING'):
        bull_low = round(high_14d * 1.02, 4)
        bull_high = round(high_14d * 1.35, 4)
        narrative = 'Прорыв range высот и переход в markup. Accumulation завершилась.'
        catalyst = 'Trigger: break выше $' + f'{high_14d:.4f}' + ' на 2× volume.'
        prob = 20
    else:
        bull_low = round(price * 1.03, 4)
        bull_high = round(high_14d * 1.10, 4)
        narrative = 'Ограниченный отскок в диапазоне.'
        catalyst = 'Trigger: BTC breaks +5% в неделю.'
        prob = 12
    
    if btc_cycle == 'DOWN':
        prob = max(prob - 5, 5)
        narrative += ' BTC в down-cycle снижает вероятность.'
    
    return {
        'type': 'BULL',
        'label': 'Positive shock / squeeze',
        'probability_pct': prob,
        'price_range': [bull_low, bull_high],
        'timeframe_days': 7,
        'narrative': narrative,
        'catalyst': catalyst,
        'position_hint': f'Long only above ${bull_low:.4f} with stop ${round(price * 0.95, 4):.4f}. Size small.',
    }


def build_bear_scenario(phase, price, high_14d, low_14d, btc_cycle, btc_dist200, days_to_unlock, unlock_amount, cex_signal):
    """Negative shock scenario."""
    # Bear downside
    if 'DISTRIBUTION' in cex_signal and days_to_unlock < 60:
        bear_low = round(low_14d * 0.80, 4)
        bear_high = round(low_14d * 0.98, 4)
        narrative = f'Cascade breakdown. CEX inflows + unlock {unlock_amount/1e6:.0f}M через {days_to_unlock}d вызывают sell pressure.'
        catalyst = f'Trigger: break BELOW ${low_14d:.4f} + volume spike. Или BTC уходит под $60k.'
        prob = 25
    elif btc_cycle == 'DOWN' and btc_dist200 < -10:
        bear_low = round(low_14d * 0.85, 4)
        bear_high = round(low_14d, 4)
        narrative = 'STRK следует за BTC deeper в down-cycle. Correlation работает против.'
        catalyst = 'Trigger: BTC breaks $60k support with force.'
        prob = 22
    elif phase == 'DISTRIBUTION':
        bear_low = round(low_14d * 0.90, 4)
        bear_high = round(low_14d, 4)
        narrative = 'Markdown начинается после distribution phase D. Ожидаемо.'
        catalyst = 'Trigger: любой негатив (news, macro, unlock).'
        prob = 20
    else:
        bear_low = round(price * 0.90, 4)
        bear_high = round(price * 0.97, 4)
        narrative = 'Ограниченная коррекция в тренде.'
        catalyst = 'Trigger: broader crypto sell-off.'
        prob = 15
    
    if 'ACCUMULATION' in cex_signal:
        prob = max(prob - 5, 8)
        narrative += ' CEX outflows снижают вероятность bear scenario.'
    
    return {
        'type': 'BEAR',
        'label': 'Negative shock / markdown',
        'probability_pct': prob,
        'price_range': [bear_low, bear_high],
        'timeframe_days': 14,
        'narrative': narrative,
        'catalyst': catalyst,
        'position_hint': f'Short only below ${bear_high:.4f} with stop ${round(price * 1.05, 4):.4f}. Or reduce longs.',
    }


def format_scenario_digest(scenarios_data):
    """Format scenarios for Telegram."""
    now = datetime.now(timezone.utc)
    state = scenarios_data['current_state']
    
    text = f"<b>🎯 STRK-GUARD · SCENARIOS · {now.strftime('%Y-%m-%d')}</b>\n"
    text += f"<i>Valid until {scenarios_data['valid_until'][:10]}</i>\n\n"
    
    # Current state summary
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📍 CURRENT STATE</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"Phase: <b>{state['phase']}</b> · {state.get('confidence', '?')}\n"
    text += f"Sub: {state.get('sub_phase', '')}\n"
    text += f"Price: <b>${state.get('price', 0):.4f}</b>\n"
    text += f"BTC: ${state.get('btc_price', 0):,.0f} · {state.get('btc_cycle', '?')}\n"
    text += f"Unlock in: <b>{state.get('days_to_next_unlock', '?')}d</b> ({state.get('next_unlock_strk', 0)/1e6:.0f}M STRK)\n"
    text += f"CEX flow: {state.get('cex_flow_signal', '?')}\n\n"
    
    # Scenarios
    for s in scenarios_data['scenarios']:
        emoji = {'BASE': '📊', 'BULL': '🟢', 'BEAR': '🔴'}.get(s['type'], '❓')
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"<b>{emoji} {s['type']} · {s['probability_pct']}%</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"<b>{s['label']}</b>\n"
        text += f"Target: ${s['price_range'][0]:.4f} - ${s['price_range'][1]:.4f}\n"
        text += f"Timeframe: {s['timeframe_days']} days\n\n"
        text += f"<b>Narrative:</b>\n<i>{s['narrative']}</i>\n\n"
        text += f"<b>Catalyst:</b>\n<i>{s['catalyst']}</i>\n\n"
        text += f"<b>Position:</b>\n<i>{s['position_hint']}</i>\n\n"
    
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Сценарии — оценка вероятностей на основе текущего состояния. "
    text += "Не советы. Обновляются раз в неделю или при trigger event.</i>"
    
    return text


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        # Split if long
        if len(text) > 4000:
            parts, current = [], ""
            for line in text.split('\n'):
                if len(current) + len(line) > 3800:
                    parts.append(current)
                    current = line + '\n'
                else:
                    current += line + '\n'
            if current: parts.append(current)
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
    logger.info("SCENARIO ENGINE · Bull/Base/Bear")
    logger.info("=" * 60)
    
    scenarios = build_scenarios()
    
    if scenarios is None:
        logger.warning("No scenarios generated (missing price data). Exiting cleanly.")
        return 0
    
    logger.info(f"\nCurrent state: {scenarios['current_state']['phase']}")
    for s in scenarios['scenarios']:
        logger.info(f"  {s['type']} ({s['probability_pct']}%): {s['label']}")
        logger.info(f"    Range: ${s['price_range'][0]}-${s['price_range'][1]}")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(scenarios, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved: {OUTPUT_FILE}")
    
    text = format_scenario_digest(scenarios)
    logger.info(f"Digest length: {len(text)}")
    
    # Only send to Telegram if explicitly requested (weekly Friday job).
    # Default behavior: silent (daily_digest will include scenarios).
    if os.environ.get('SCENARIO_SEND_TELEGRAM', '').lower() in ('1', 'true', 'yes'):
        sent = send_telegram(text)
        if sent:
            logger.info("Scenarios sent to Telegram (opt-in)")
    else:
        logger.info("Silent mode (daily_digest will include scenarios)")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
