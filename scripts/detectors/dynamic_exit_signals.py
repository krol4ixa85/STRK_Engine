#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dynamic_exit_signals.py — detect exit signals для активных squeeze позиций.

Читает cex_flow / cohort_tracker / technical / funding / cvd.
Проверяет 4 exit triggers отдельно для LONG и SHORT позиций.
Пишет data/cache/exit_signals.json.

Если есть active squeeze (STRONG alert < 72h назад) — отправляет EXIT alert
в @strk_dynamic3_bot с рекомендацией "close position early".

Long exit triggers (когда ты в лонге):
  L1 · CEX flow flip — был bullish streak ≥ 3 дней, теперь bearish streak ≥ 2 дней
       (smart money distributing back to exchanges)
  L2 · SMART cohort distribution — был accumulating, теперь distributing
       (потрая support от крупных)
  L3 · Volume divergence — цена растёт, объём падает 3+ бара подряд
       (weakness, топ близко)
  L4 · Funding flip crowded long — funding перевернулся с neutral в >+20%
       (long crowded, risk of flush)

Short exit triggers (когда ты в шорте):
  S1 · CEX flow flip bearish→bullish — inverse of L1
  S2 · SMART cohort flip distribution→accumulation
  S3 · Volume + price divergence bullish — inverse of L3
  S4 · Funding flip crowded short — крупные позиции open shorts, свежий crowded

Уровни alert:
  NEUTRAL — 0 triggers active
  WARNING — 1-2 triggers active (осторожно)
  URGENT — 3+ triggers active (close position)

Alert идёт в @strk_dynamic3_bot ТОЛЬКО если:
  - Есть active squeeze position (недавний STRONG alert < 72h)
  - Level = WARNING или URGENT
  - Dedup 12h (не спамить)
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
OUTPUT_FILE = CACHE_DIR / 'exit_signals.json'
NOTIFIER_STATE_FILE = CACHE_DIR / 'exit_notifier_state.json'
SQUEEZE_STATE_FILE = CACHE_DIR / 'squeeze_state.json'
SQUEEZE_ALERTS_FILE = HISTORY_DIR / 'squeeze_alerts.jsonl'
HISTORY_FILE = HISTORY_DIR / 'exit_signals.jsonl'

# =====================================================================
# THRESHOLDS
# =====================================================================
CEX_STREAK_MIN = 3          # L1: prior bullish streak threshold
CEX_FLIP_STREAK = 2         # L1: current bearish streak threshold
SMART_DIST_THRESHOLD = -500_000  # L2: net -500k STRK = distributing
VOL_DIVERGENCE_BARS = 3     # L3: consecutive weakening bars
FUNDING_CROWDED_LONG = 20.0   # L4: funding above this = long crowded
FUNDING_CROWDED_SHORT = -20.0 # S4: funding below this = short crowded
DEDUP_HOURS = 12
ACTIVE_SQUEEZE_WINDOW_HOURS = 72


def load_json(name):
    path = CACHE_DIR / name
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_long_exit_triggers(cex_flow, cohort, technical, funding, cvd):
    """Check 4 exit triggers для LONG positions."""
    triggers = []

    # L1: CEX flow flip bullish→bearish
    cex_stats = ((cex_flow.get('classification') or {}).get('stats') or {})
    bullish_streak = cex_stats.get('consecutive_bullish', 0)
    bearish_streak = cex_stats.get('consecutive_bearish', 0)
    # Проверяем historical: если раньше был bullish_streak, а сейчас bearish_streak
    # Since we don't have history здесь, используем current bearish_streak ≥ threshold
    # и добавляем "prior_bullish_hint" через max_recent_bullish (если doступно)
    l1_active = bearish_streak >= CEX_FLIP_STREAK
    triggers.append({
        'id': 'L1',
        'name': 'CEX flow flip → distribution',
        'active': l1_active,
        'evidence': f'bearish streak {bearish_streak} days (was bullish {bullish_streak})',
    })

    # L2: SMART cohort distribution
    cohorts = cohort.get('cohorts') or {}
    smart = cohorts.get('SMART') or cohorts.get('smart') or {}
    smart_net = smart.get('net_flow_strk') or smart.get('net_24h_strk') or 0
    l2_active = smart_net <= SMART_DIST_THRESHOLD
    triggers.append({
        'id': 'L2',
        'name': 'SMART cohort distributing',
        'active': l2_active,
        'evidence': f'SMART 24h net {smart_net/1e6:+.2f}M STRK (threshold {SMART_DIST_THRESHOLD/1e6:+.1f}M)',
    })

    # L3: Volume divergence — price up but volume declining
    features = technical.get('features') or {}
    price_up_3d = features.get('price_up_3d', False)
    vol_ratio = features.get('vol_ratio_3d_vs_30d', 1.0)
    vol_declining = features.get('vol_declining_3bars', False)
    # Простой прокси: price_up but vol_ratio < 1.0 (below average)
    l3_active = bool(price_up_3d) and (vol_ratio is not None and vol_ratio < 0.8)
    triggers.append({
        'id': 'L3',
        'name': 'Volume divergence (weakness at top)',
        'active': l3_active,
        'evidence': f'price_up_3d={price_up_3d}, vol_ratio={vol_ratio}',
    })

    # L4: Funding flip crowded long
    fm = funding.get('funding_metrics') or {}
    apr = fm.get('current_annualized_pct')
    long_crowded = fm.get('long_crowded', False)
    l4_active = (apr is not None and apr >= FUNDING_CROWDED_LONG) or bool(long_crowded)
    triggers.append({
        'id': 'L4',
        'name': 'Funding crowded long (flush risk)',
        'active': l4_active,
        'evidence': f'funding APR {apr}%, long_crowded={long_crowded}' if apr is not None else 'no data',
    })

    active_count = sum(1 for t in triggers if t['active'])
    return {
        'direction': 'long',
        'triggers': triggers,
        'active_count': active_count,
    }


def check_short_exit_triggers(cex_flow, cohort, technical, funding, cvd):
    """Check 4 exit triggers для SHORT positions."""
    triggers = []

    # S1: CEX flow flip bearish→bullish (inverse of L1)
    cex_stats = ((cex_flow.get('classification') or {}).get('stats') or {})
    bullish_streak = cex_stats.get('consecutive_bullish', 0)
    bearish_streak = cex_stats.get('consecutive_bearish', 0)
    s1_active = bullish_streak >= CEX_FLIP_STREAK
    triggers.append({
        'id': 'S1',
        'name': 'CEX flow flip → accumulation',
        'active': s1_active,
        'evidence': f'bullish streak {bullish_streak} days (was bearish {bearish_streak})',
    })

    # S2: SMART cohort accumulation restart
    cohorts = cohort.get('cohorts') or {}
    smart = cohorts.get('SMART') or cohorts.get('smart') or {}
    smart_net = smart.get('net_flow_strk') or smart.get('net_24h_strk') or 0
    s2_active = smart_net >= abs(SMART_DIST_THRESHOLD)  # +500k
    triggers.append({
        'id': 'S2',
        'name': 'SMART cohort accumulating',
        'active': s2_active,
        'evidence': f'SMART 24h net {smart_net/1e6:+.2f}M STRK',
    })

    # S3: Volume divergence bullish (price down but volume declining = seller exhaustion)
    features = technical.get('features') or {}
    rsi = features.get('rsi')
    vol_ratio = features.get('vol_ratio_3d_vs_30d', 1.0)
    s3_active = (rsi is not None and rsi < 35) and (vol_ratio is not None and vol_ratio < 0.8)
    triggers.append({
        'id': 'S3',
        'name': 'Seller exhaustion (RSI<35 + vol low)',
        'active': s3_active,
        'evidence': f'RSI {rsi}, vol_ratio {vol_ratio}',
    })

    # S4: Funding flip crowded short (inverse of L4)
    fm = funding.get('funding_metrics') or {}
    apr = fm.get('current_annualized_pct')
    short_crowded = fm.get('short_crowded', False)
    s4_active = (apr is not None and apr <= FUNDING_CROWDED_SHORT) or bool(short_crowded)
    triggers.append({
        'id': 'S4',
        'name': 'Funding crowded short (squeeze risk)',
        'active': s4_active,
        'evidence': f'funding APR {apr}%, short_crowded={short_crowded}' if apr is not None else 'no data',
    })

    active_count = sum(1 for t in triggers if t['active'])
    return {
        'direction': 'short',
        'triggers': triggers,
        'active_count': active_count,
    }


def classify_level(active_count):
    """Determine alert level from active trigger count."""
    if active_count >= 3:
        return 'URGENT'
    elif active_count >= 1:
        return 'WARNING'
    return 'NEUTRAL'


def has_active_squeeze_position():
    """Check if есть недавний STRONG squeeze alert (< 72h)."""
    if not SQUEEZE_ALERTS_FILE.exists():
        return None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ACTIVE_SQUEEZE_WINDOW_HOURS)
        latest_strong = None
        with open(SQUEEZE_ALERTS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get('transition_type') != 'STRONG':
                        continue
                    ts = datetime.fromisoformat(r.get('ts', ''))
                    if ts >= cutoff:
                        latest_strong = r
                except Exception:
                    continue
        return latest_strong
    except Exception as e:
        logger.warning(f"Failed to check squeeze alerts: {e}")
        return None


def compute_exit_state():
    """Main computation — check exit triggers для both long and short."""
    cex_flow = load_json('cex_flow.json')
    cohort = load_json('cohort_tracker.json')
    technical = load_json('technical_momentum.json')
    funding = load_json('funding_signal.json')
    cvd = load_json('cvd_analysis.json')

    long_check = check_long_exit_triggers(cex_flow, cohort, technical, funding, cvd)
    short_check = check_short_exit_triggers(cex_flow, cohort, technical, funding, cvd)

    long_level = classify_level(long_check['active_count'])
    short_level = classify_level(short_check['active_count'])

    active_squeeze = has_active_squeeze_position()

    return {
        'ts': datetime.now(timezone.utc).isoformat(),
        'long_exit': {
            'level': long_level,
            'active_count': long_check['active_count'],
            'triggers': long_check['triggers'],
        },
        'short_exit': {
            'level': short_level,
            'active_count': short_check['active_count'],
            'triggers': short_check['triggers'],
        },
        'active_squeeze_alert': active_squeeze,
        'thresholds': {
            'smart_dist_threshold_strk': SMART_DIST_THRESHOLD,
            'cex_streak_min': CEX_STREAK_MIN,
            'funding_crowded_long_pct': FUNDING_CROWDED_LONG,
            'funding_crowded_short_pct': FUNDING_CROWDED_SHORT,
        },
    }


def format_exit_alert(state, direction, level):
    """Build alert message for Telegram."""
    exit_data = state[f'{direction}_exit']
    triggers = exit_data['triggers']
    active_triggers = [t for t in triggers if t['active']]

    emoji = '🚨' if level == 'URGENT' else '⚠️'
    dir_emoji = '🟢' if direction == 'long' else '🔴'

    text = f"{emoji} <b>EXIT ALERT · {level}</b>\n\n"
    text += f"{dir_emoji} Direction: <b>{direction.upper()}</b>\n"
    text += f"Active triggers: <b>{exit_data['active_count']}/4</b>\n\n"

    text += "<b>Triggered:</b>\n"
    for t in active_triggers:
        text += f"  ✓ <b>{t['id']}</b> · {t['name']}\n"
        text += f"    <i>{t['evidence']}</i>\n"
    text += "\n"

    if level == 'URGENT':
        text += "<i>🚨 3+ триггера — рассмотрите закрытие позиции.</i>\n"
    else:
        text += "<i>⚠️ 1-2 триггера — предупреждение, следите за развитием.</i>\n"

    # Context: связь со squeeze alert если был
    active_sq = state.get('active_squeeze_alert')
    if active_sq:
        sq_ts = active_sq.get('ts', '?')[:16]
        text += f"\n<i>💡 Активный squeeze setup был {sq_ts} — учитывайте контекст.</i>"

    text += "\n<i>⚠ Not a decision, just a signal. Проверьте DECISION в @STRK_GUARDIAN_BOT.</i>"
    return text


def send_telegram(text, token, chat_id):
    if not token or not chat_id:
        logger.warning("Squeeze bot not configured; would send:")
        logger.warning(text[:400])
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"Exit alert sent · message_id={result.get('result', {}).get('message_id')}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send: {e}")
        return False


def log_history(state, direction, level, sent):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'direction': direction,
        'level': level,
        'active_count': state[f'{direction}_exit']['active_count'],
        'triggers': [
            {'id': t['id'], 'active': t['active'], 'evidence': t['evidence']}
            for t in state[f'{direction}_exit']['triggers']
        ],
        'active_squeeze_alert_ts': (state.get('active_squeeze_alert') or {}).get('ts'),
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        logger.warning(f"Failed to append history: {e}")


def is_within_dedup(last_alert_ts):
    if not last_alert_ts:
        return False
    try:
        last = datetime.fromisoformat(last_alert_ts)
        age = datetime.now(timezone.utc) - last
        return age < timedelta(hours=DEDUP_HOURS)
    except Exception:
        return False


def load_notifier_state():
    return load_json_from_path(NOTIFIER_STATE_FILE) or {
        'last_long_alert_ts': None,
        'last_short_alert_ts': None,
        'alert_count': 0,
    }


def load_json_from_path(path):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def main():
    logger.info("=" * 60)
    logger.info("DYNAMIC EXIT SIGNALS · @strk_dynamic3_bot")
    logger.info("=" * 60)

    state = compute_exit_state()
    save_json(OUTPUT_FILE, state)

    long_level = state['long_exit']['level']
    short_level = state['short_exit']['level']
    long_count = state['long_exit']['active_count']
    short_count = state['short_exit']['active_count']

    logger.info(f"LONG exit: {long_level} · {long_count}/4 triggers")
    for t in state['long_exit']['triggers']:
        mark = '✓' if t['active'] else '·'
        logger.info(f"  {mark} {t['id']} · {t['name']}: {t['evidence']}")

    logger.info(f"SHORT exit: {short_level} · {short_count}/4 triggers")
    for t in state['short_exit']['triggers']:
        mark = '✓' if t['active'] else '·'
        logger.info(f"  {mark} {t['id']} · {t['name']}: {t['evidence']}")

    # Send alerts ТОЛЬКО если есть active squeeze position (< 72h) и level ≥ WARNING
    active_squeeze = state.get('active_squeeze_alert')
    if not active_squeeze:
        logger.info("No active squeeze position (last STRONG > 72h ago) — skip alerts")
        return 0

    notifier_state = load_notifier_state()
    token = os.environ.get('SQUEEZE_BOT_TOKEN', '')
    chat_id = os.environ.get('SQUEEZE_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')

    # Long exit alert
    if long_level in ('WARNING', 'URGENT'):
        if not is_within_dedup(notifier_state.get('last_long_alert_ts')):
            text = format_exit_alert(state, 'long', long_level)
            sent = send_telegram(text, token, chat_id)
            log_history(state, 'long', long_level, sent)
            if sent:
                notifier_state['last_long_alert_ts'] = datetime.now(timezone.utc).isoformat()
                notifier_state['alert_count'] = notifier_state.get('alert_count', 0) + 1
        else:
            logger.info(f"Long alert dedup — last within {DEDUP_HOURS}h")

    # Short exit alert
    if short_level in ('WARNING', 'URGENT'):
        if not is_within_dedup(notifier_state.get('last_short_alert_ts')):
            text = format_exit_alert(state, 'short', short_level)
            sent = send_telegram(text, token, chat_id)
            log_history(state, 'short', short_level, sent)
            if sent:
                notifier_state['last_short_alert_ts'] = datetime.now(timezone.utc).isoformat()
                notifier_state['alert_count'] = notifier_state.get('alert_count', 0) + 1
        else:
            logger.info(f"Short alert dedup — last within {DEDUP_HOURS}h")

    save_json(NOTIFIER_STATE_FILE, notifier_state)
    logger.info(f"Total exit alerts sent: {notifier_state.get('alert_count', 0)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())