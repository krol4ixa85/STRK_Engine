#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strk_lab.py — LAB режим · Portfolio Rotation Compass.

ЦЕЛЬ: помочь ручному трейдеру не сидеть в кэше пока STRK в accumulation.
      Показать (a) где сейчас реальный momentum по DEX-ликвидным токенам
                (b) конкретные STRK re-entry triggers
                (c) opportunity cost — насколько STRK отстаёт от лидеров

BOT: @Lab_sector_bot (отдельно от @STRK_GUARDIAN_BOT).
     Чтобы sector/rotation сигналы не смешивались с STRK-specific.

Читает:
  - dune_sector_netflow.json  (query 8317444)
  - dune_sector_momentum.json (query 8317478)
  - Existing STRK cache (phase, wyckoff, dune monthly)

Отправляет LAB отчёт в @Lab_sector_bot (mode=lab или cron 08:30 UTC).

ENV:
  TELEGRAM_LAB_SECTOR_BOT — токен @Lab_sector_bot
  TELEGRAM_CHAT_ID — тот же chat_id что для основного бота

Фильтры:
  - Only DEX-liquid tokens: tx_count > 5000 за 7d
  - Убирает шумовые sectors (AI_AGENTS, GAMING если < 1000 tx)
  - STRONG_BUY appears only с price momentum > +5%
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'

NETFLOW = CACHE_DIR / 'dune_sector_netflow.json'
MOMENTUM = CACHE_DIR / 'dune_sector_momentum.json'

LIQUIDITY_FLOOR_TX = 5000  # min tx_count 7d для считать токен ликвидным


def load_json(path, default=None):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def _get(row, name, default=None):
    if isinstance(row, dict):
        return row.get(name, default)
    return default


def _safe(s):
    return str(s).replace('<', '&lt;').replace('>', '&gt;') if s else ''


def get_strk_context():
    """Собираем STRK phase state из existing cache."""
    wyckoff = load_json(CACHE_DIR / 'wyckoff_phase.json', {})
    monthly = load_json(CACHE_DIR / 'dune_starknet_monthly.json', {})
    daily = load_json(CACHE_DIR / 'dune_starknet.json', {})
    cex = load_json(CACHE_DIR / 'cex_flow.json', {})
    composite = load_json(CACHE_DIR / 'composite_signal_v2.json', {})

    # Wyckoff
    ctx = {
        'wyckoff_phase': wyckoff.get('phase'),
        'wyckoff_confidence': wyckoff.get('confidence'),
    }

    # Dune monthly signal + streak
    m_rows = monthly.get('rows') or []
    if m_rows:
        latest = m_rows[0]
        ctx['dune_monthly_signal'] = _get(latest, 'phase_signal') or _get(latest, 'signal')
        ctx['dune_trend_pct'] = _get(latest, 'w_m_pct') or _get(latest, 'pct_from_30d_max')
        # Bearish streak
        streak = 0
        for r in m_rows[:30]:
            sig = _get(r, 'phase_signal') or _get(r, 'signal')
            if sig == 'BEARISH_BREAKDOWN':
                streak += 1
            else:
                break
        ctx['bearish_streak'] = streak
        ctx['bearish_30d'] = sum(1 for r in m_rows[:30]
                                  if (_get(r, 'phase_signal') or _get(r, 'signal')) == 'BEARISH_BREAKDOWN')

    # Daily activity WoW
    d_rows = daily.get('rows') or []
    if len(d_rows) >= 7:
        latest_txs = _get(d_rows[0], 'total_txs', 0) or 0
        wow_txs = _get(d_rows[6], 'total_txs', 1) or 1
        ctx['activity_wow_pct'] = (latest_txs / wow_txs - 1) * 100 if wow_txs else 0
        latest_new = _get(d_rows[0], 'new_accounts', 0) or 0
        wow_new = _get(d_rows[6], 'new_accounts', 1) or 1
        ctx['adoption_wow_pct'] = (latest_new / wow_new - 1) * 100 if wow_new else 0

    # CEX signal
    classification = (cex.get('classification') or {})
    ctx['cex_signal'] = classification.get('signal')
    stats = classification.get('stats') or {}
    ctx['cex_consecutive_bullish'] = stats.get('consecutive_bullish', 0)

    # STRK price
    for src in (composite, load_json(CACHE_DIR / 'technical_momentum.json', {})):
        v = src.get('price') or src.get('current_price')
        if isinstance(v, (int, float)) and 0.001 < v < 100:
            ctx['strk_price'] = float(v)
            break

    return ctx


def compute_strk_status(ctx):
    """Определяем в какой мы фазе holding period."""
    wyckoff = str(ctx.get('wyckoff_phase') or '').upper()
    dune_sig = ctx.get('dune_monthly_signal') or 'UNKNOWN'
    bearish_30d = ctx.get('bearish_30d', 0)
    cex_sig = ctx.get('cex_signal') or ''

    triggers_hit = 0
    trigger_list = []

    # Trigger 1: Wyckoff not in bear phases
    if wyckoff in ('MARKUP', 'ACCUMULATION_LATE', 'ACCUMULATION_BASE'):
        triggers_hit += 1
        trigger_list.append(('✓', 'Wyckoff', f'{wyckoff}'))
    else:
        trigger_list.append(('✗', 'Wyckoff', f'{wyckoff or "?"} (need MARKUP/ACC)'))

    # Trigger 2: Dune monthly not bearish
    if dune_sig in ('BULLISH_MOMENTUM', 'NEUTRAL_CONSOLIDATION'):
        # And bearish 30d should be dropping
        if bearish_30d < 15:
            triggers_hit += 1
            trigger_list.append(('✓', 'Dune monthly', f'{dune_sig} ({bearish_30d}/30d bearish)'))
        else:
            trigger_list.append(('⏳', 'Dune monthly',
                                  f'{dune_sig} but still {bearish_30d}/30d bearish'))
    else:
        trigger_list.append(('✗', 'Dune monthly',
                              f'{dune_sig} ({bearish_30d}/30d bearish)'))

    # Trigger 3: CEX signal accumulation
    if 'ACCUMULATION' in cex_sig:
        triggers_hit += 1
        trigger_list.append(('✓', 'CEX flow', cex_sig))
    else:
        trigger_list.append(('✗', 'CEX flow', f'{cex_sig or "?"} (need ACCUMULATION)'))

    # Trigger 4: Activity WoW positive
    activity = ctx.get('activity_wow_pct', 0)
    if activity > 10:
        triggers_hit += 1
        trigger_list.append(('✓', 'Activity', f'WoW {activity:+.0f}%'))
    else:
        trigger_list.append(('✗', 'Activity', f'WoW {activity:+.0f}% (need >+10%)'))

    # Verdict
    if triggers_hit >= 3:
        verdict = 'RE_ENTRY_ZONE'
        emoji = '🟢'
        recommendation = 'Начать возврат в STRK. Скалить position с ниже.'
    elif triggers_hit == 2:
        verdict = 'WATCH_CLOSELY'
        emoji = '🟡'
        recommendation = 'Наблюдение. 2 из 4 triggers. Ждать 3-й.'
    elif triggers_hit == 1:
        verdict = 'EARLY_INFLECTION'
        emoji = '🟡'
        recommendation = 'Один сигнал шума. Продолжать rotation.'
    else:
        verdict = 'STILL_ACCUMULATION'
        emoji = '🔴'
        recommendation = 'STRK в accumulation. Ротировать капитал в active sectors.'

    return {
        'verdict': verdict,
        'emoji': emoji,
        'triggers_hit': triggers_hit,
        'triggers_total': 4,
        'trigger_list': trigger_list,
        'recommendation': recommendation,
    }


def get_top_movers():
    """Из sector netflow + momentum — топ actionable candidates."""
    netflow_data = load_json(NETFLOW, {})
    momentum_data = load_json(MOMENTUM, {})

    rows_nf = netflow_data.get('rows') or []
    rows_mo = momentum_data.get('rows') or []

    # Dedup by token — same token в multiple sectors
    seen = set()

    # STRONG_BUY candidates (from momentum: net flow + price up)
    strong_buys = []
    divergences = []
    for r in rows_mo:
        token = _get(r, 'token')
        if not token or token in seen:
            continue
        tx_count = _get(r, 'tx_count', 0) or 0
        if tx_count < LIQUIDITY_FLOOR_TX:
            continue
        signal = _get(r, 'signal', '')
        if signal == 'STRONG_BUY':
            strong_buys.append({
                'sector': _get(r, 'sector'),
                'token': token,
                'net_flow_m_usd': _get(r, 'net_flow_m_usd', 0) or 0,
                'price_change_7d_pct': _get(r, 'price_change_7d_pct', 0) or 0,
                'tx_count': tx_count,
                'signal': signal,
            })
            seen.add(token)
        elif signal == 'DIVERGENCE':
            divergences.append({
                'sector': _get(r, 'sector'),
                'token': token,
                'net_flow_m_usd': _get(r, 'net_flow_m_usd', 0) or 0,
                'price_change_7d_pct': _get(r, 'price_change_7d_pct', 0) or 0,
                'tx_count': tx_count,
                'signal': 'DIVERGENCE',
            })
            seen.add(token)
        elif signal in ('STRONG_SELL', 'SELL_PRESSURE'):
            # для полноты — sell tokens тоже в snapshot
            seen.add(token)

    # Sort by net flow desc
    strong_buys.sort(key=lambda x: x['net_flow_m_usd'], reverse=True)
    divergences.sort(key=lambda x: x['net_flow_m_usd'], reverse=True)

    # BUY_PRESSURE (без price up) from netflow — kandidati bez momentum yet
    buy_pressure_no_price = []
    sell_tokens = []
    for r in rows_nf:
        token = _get(r, 'token')
        if not token or token in seen:
            continue
        tx_count = _get(r, 'tx_count', 0) or 0
        if tx_count < LIQUIDITY_FLOOR_TX:
            continue
        direction = _get(r, 'direction')
        nf = _get(r, 'net_flow_m_usd', 0) or 0
        if direction == 'BUY_PRESSURE' and nf > 0.5:
            buy_pressure_no_price.append({
                'sector': _get(r, 'sector'),
                'token': token,
                'net_flow_m_usd': nf,
                'net_flow_pct': _get(r, 'net_flow_pct', 0) or 0,
                'tx_count': tx_count,
                'signal': 'BUY_PRESSURE',
            })
            seen.add(token)
        elif direction == 'SELL_PRESSURE' and nf < -0.5:
            sell_tokens.append({
                'sector': _get(r, 'sector'),
                'token': token,
                'net_flow_m_usd': nf,
                'net_flow_pct': _get(r, 'net_flow_pct', 0) or 0,
                'tx_count': tx_count,
                'signal': 'SELL_PRESSURE',
            })
            seen.add(token)
    buy_pressure_no_price.sort(key=lambda x: x['net_flow_m_usd'], reverse=True)
    sell_tokens.sort(key=lambda x: x['net_flow_m_usd'])

    return strong_buys, divergences, buy_pressure_no_price, sell_tokens


def save_snapshot(ctx, status, strong_buys, divergences, buy_pressure, sell_tokens):
    """Сохраняет structured snapshot для rotation_tracker и других consumers."""
    snapshot = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'strk_status': {
            'verdict': status['verdict'],
            'triggers_hit': status['triggers_hit'],
            'triggers_total': status['triggers_total'],
            'recommendation': status['recommendation'],
            'wyckoff_phase': ctx.get('wyckoff_phase'),
            'dune_monthly_signal': ctx.get('dune_monthly_signal'),
            'cex_signal': ctx.get('cex_signal'),
            'strk_price': ctx.get('strk_price'),
            'bearish_30d': ctx.get('bearish_30d'),
        },
        're_entry_triggers': {
            'trigger_list': status['trigger_list'],
        },
        'strong_buy': strong_buys,
        'divergence': divergences,
        'buy_pressure': buy_pressure,
        'sell': sell_tokens,
    }
    snapshot_path = CACHE_DIR / 'strk_lab_report.json'
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Snapshot saved: {snapshot_path.name}")


def format_lab_report():
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    ctx = get_strk_context()
    status = compute_strk_status(ctx)
    strong_buys, divergences, buy_pressure, sell_tokens = get_top_movers()

    # Save structured snapshot для rotation_tracker и других consumers
    save_snapshot(ctx, status, strong_buys, divergences, buy_pressure, sell_tokens)

    text = f"<b>🧪 STRK LAB · Portfolio Rotation</b>\n"
    text += f"<i>{ts}</i>\n\n"

    # === STRK STATUS ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>📍 STRK STATUS: {status['emoji']} {status['verdict']}</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    price_str = f"${ctx['strk_price']:.4f}" if ctx.get('strk_price') else 'n/a'
    text += f"Price: <code>{price_str}</code>\n"
    if ctx.get('wyckoff_phase'):
        text += f"Wyckoff: <code>{_safe(ctx['wyckoff_phase'])}</code>"
        if ctx.get('wyckoff_confidence'):
            text += f" ({_safe(ctx['wyckoff_confidence'])})"
        text += "\n"
    if ctx.get('dune_monthly_signal'):
        text += f"Dune: <code>{_safe(ctx['dune_monthly_signal'])}</code>"
        if ctx.get('bearish_30d') is not None:
            text += f" · {ctx['bearish_30d']}/30d bearish"
        text += "\n"
    if ctx.get('cex_signal'):
        text += f"CEX: <code>{_safe(ctx['cex_signal'])}</code>\n"
    text += "\n"

    # === RE-ENTRY TRIGGERS ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>🎯 RE-ENTRY TRIGGERS ({status['triggers_hit']}/{status['triggers_total']})</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    for mark, name, detail in status['trigger_list']:
        text += f"{mark} {name}: <code>{_safe(detail)}</code>\n"
    text += f"\n<b>{status['emoji']} {_safe(status['recommendation'])}</b>\n\n"

    # === WHAT'S PUMPING ===
    if strong_buys:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🟢 STRONG_BUY · Confirmed Momentum (7d)</b>\n"
        text += "<i>Net flow ↑ + Price ↑ + Liquid (>5K tx)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for sb in strong_buys[:8]:
            text += (f"  <code>{sb['token']:<6}</code> "
                     f"({_safe(sb['sector']):<8}) "
                     f"net <code>{sb['net_flow_m_usd']:+.1f}M</code> · "
                     f"price <code>{sb['price_change_7d_pct']:+.1f}%</code>\n")
        text += "\n"
    else:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🟢 STRONG_BUY (0 tokens)</b>\n"
        text += "<i>No confirmed momentum + net flow signals today.</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n\n"

    # === DIVERGENCES (early opportunity) ===
    if divergences:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚡ DIVERGENCE · Price ↑ but Flow ↓ (be careful)</b>\n"
        text += "<i>Price rallying without buy volume — часто fake breakout</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for dv in divergences[:5]:
            text += (f"  <code>{dv['token']:<6}</code> "
                     f"({_safe(dv['sector']):<8}) "
                     f"net <code>{dv['net_flow_m_usd']:+.1f}M</code> · "
                     f"price <code>{dv['price_change_7d_pct']:+.1f}%</code>\n")
        text += "\n"

    # === BUY PRESSURE (без price confirmation) ===
    if buy_pressure:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>💰 BUY_PRESSURE · Flow accumulation (price ещё не отразила)</b>\n"
        text += "<i>Net flow > $500K, ждать price confirmation</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for bp in buy_pressure[:5]:
            text += (f"  <code>{bp['token']:<6}</code> "
                     f"({_safe(bp['sector']):<8}) "
                     f"net <code>{bp['net_flow_m_usd']:+.1f}M</code> "
                     f"({bp['net_flow_pct']:+.0f}%)\n")
        text += "\n"

    # === FOOTER ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>💡 LAB = data-only. Не advice. Проверяй технику каждого токена.</i>\n"
    text += "<i>💡 Все numbers — из DEX volume (Dune). Крупные CEX-only tokens не видны.</i>\n"
    text += "<i>💡 Update: 1x/сутки утром (08:30 UTC). Ручной запуск: mode=lab</i>\n"

    return text


def split_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    while len(text) > max_len:
        split_at = text.rfind('\n', 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts


def send_telegram(text, token, chat_id):
    if not token or not chat_id:
        logger.warning("Telegram not configured — printing:")
        logger.warning(text[:500])
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for i, part in enumerate(split_message(text)):
        data = urllib.parse.urlencode({
            'chat_id': chat_id,
            'text': part,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        }).encode()
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read())
                if result.get('ok'):
                    logger.info(f"LAB part {i+1} sent")
                else:
                    logger.error(f"Telegram error: {result}")
                    return False
        except Exception as e:
            logger.error(f"Failed: {e}")
            return False
    return True


def main():
    logger.info("=" * 60)
    logger.info("STRK LAB · Portfolio Rotation Compass")
    logger.info("=" * 60)

    text = format_lab_report()

    # LAB отправляется в @Lab_sector_bot (отдельно от @STRK_GUARDIAN_BOT)
    # Chat_id тот же — идёт тебе.
    token = os.environ.get('TELEGRAM_LAB_SECTOR_BOT', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

    if not token:
        logger.error("TELEGRAM_LAB_SECTOR_BOT not set — cannot send LAB report")
        print("::error title=LAB bot token missing::Set TELEGRAM_LAB_SECTOR_BOT secret with @Lab_sector_bot token")
        return 0

    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID not set")
        return 0

    logger.info(f"Sending to @Lab_sector_bot · chat_id={chat_id[:8]}...")
    send_telegram(text, token, chat_id)

    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())