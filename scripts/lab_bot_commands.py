#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lab_bot_commands.py — command handler for @Lab_sector_bot.

По аналогии с telegram_bot_commands.py (STRK_GUARDIAN), но для LAB бота.

Команды:
  /help              — список всех команд
  /status            — текущий STRK statuс + rotation compass
  /check <TOKEN>     — детальный анализ токена из последнего snapshot
  /list [<sector>]   — все STRONG_BUY (optionally filtered by sector)
  /refresh           — принудительный запуск LAB pipeline (dispatch)

DEPLOY:
  Одноразовый прогон (для GitHub Actions cron каждые 5 мин):
    python3 scripts/lab_bot_commands.py --once
  Loop (для VPS/Railway/Fly, не GitHub Actions):
    python3 scripts/lab_bot_commands.py

ENV:
  TELEGRAM_LAB_SECTOR_BOT — token @Lab_sector_bot
  TELEGRAM_CHAT_ID        — chat_id (тот же что и для основного bot)

DATA (чтение snapshots — не запускает Dune queries):
  data/cache/strk_lab_report.json     — from strk_lab.py
  data/cache/dune_sector_momentum.json — from dune_sector_collector.py
  data/cache/dune_sector_netflow.json  — same
  data/cache/rotation_tracker_state.json — streak info

STATE:
  data/cache/lab_bot_state.json — last processed update_id
"""
import os
import sys
import json
import time
import argparse
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = CACHE_DIR / 'lab_bot_state.json'

# @Lab_sector_bot — отдельный от @STRK_GUARDIAN_BOT
LAB_BOT_TOKEN = os.environ.get('TELEGRAM_LAB_SECTOR_BOT', '').strip()
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('lab_bot')

# ============================================================
# STATE
# ============================================================
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'last_update_id': 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_offset_from_telegram():
    """Fallback: get last processed update_id from Telegram itself.
    Same trick as telegram_bot_commands.py — avoids re-processing on cache miss."""
    url = f"https://api.telegram.org/bot{LAB_BOT_TOKEN}/getUpdates?offset=-1&limit=1&timeout=0"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        if data.get('ok') and data.get('result'):
            last = data['result'][0]
            return last['update_id'] + 1
    except Exception as e:
        logger.warning(f"Telegram offset fallback failed: {e}")
    return 0


# ============================================================
# TELEGRAM API
# ============================================================
def send_message(chat_id, text, disable_preview=True):
    if not LAB_BOT_TOKEN:
        logger.error("TELEGRAM_LAB_SECTOR_BOT not set")
        return False
    url = f"https://api.telegram.org/bot{LAB_BOT_TOKEN}/sendMessage"
    # Split long messages
    max_len = 4000
    parts = []
    while len(text) > max_len:
        cut = text.rfind('\n', 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)

    ok = True
    for part in parts:
        data = urllib.parse.urlencode({
            'chat_id': chat_id,
            'text': part,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true' if disable_preview else 'false',
        }).encode()
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read())
                if not result.get('ok'):
                    logger.error(f"Telegram error: {result}")
                    ok = False
        except Exception as e:
            logger.error(f"Send failed: {e}")
            ok = False
    return ok


def get_updates(offset=0):
    url = f"https://api.telegram.org/bot{LAB_BOT_TOKEN}/getUpdates?offset={offset}&timeout=0"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read())
        if data.get('ok'):
            return data.get('result', [])
    except Exception as e:
        logger.error(f"getUpdates failed: {e}")
    return []


# ============================================================
# DATA READING (from snapshots — no Dune calls)
# ============================================================
def load_json(path):
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load {path.name}: {e}")
        return None


def get_lab_snapshot():
    return load_json(CACHE_DIR / 'strk_lab_report.json')


def get_momentum_data():
    return load_json(CACHE_DIR / 'dune_sector_momentum.json')


def get_netflow_data():
    return load_json(CACHE_DIR / 'dune_sector_netflow.json')


def get_rotation_state():
    return load_json(CACHE_DIR / 'rotation_tracker_state.json')


def _safe_html(s):
    if s is None:
        return 'n/a'
    return str(s).replace('<', '&lt;').replace('>', '&gt;')


def _snapshot_age_str(snapshot):
    ts = snapshot.get('generated_at') if snapshot else None
    if not ts:
        return 'unknown'
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if age < 1:
            return f'{int(age*60)}m ago'
        if age < 24:
            return f'{age:.1f}h ago'
        return f'{age/24:.1f}d ago'
    except Exception:
        return 'unknown'


# ============================================================
# COMMANDS
# ============================================================
def cmd_help(chat_id):
    text = "<b>🧪 LAB Bot Commands</b>\n\n"
    text += "<b>/status</b>\n"
    text += "  Текущий статус STRK · triggers · рекомендация\n\n"
    text += "<b>/check &lt;TOKEN&gt;</b>\n"
    text += "  Детальный анализ токена из последнего LAB snapshot\n"
    text += "  Пример: <code>/check LINK</code>\n\n"
    text += "<b>/list</b> [<code>&lt;sector&gt;</code>]\n"
    text += "  Все STRONG_BUY tokens. Optionally по сектору.\n"
    text += "  Примеры: <code>/list</code> · <code>/list DeFi</code> · <code>/list RWA</code>\n\n"
    text += "<b>/refresh</b>\n"
    text += "  Триггерит новый LAB запуск (Dune queries)\n"
    text += "  ⚠ Тратит Dune credits — используй разумно\n\n"
    text += "<b>/help</b>\n"
    text += "  Эта справка\n\n"
    text += "<i>💡 LAB = data-only. Все numbers из DEX volume (Dune).</i>\n"
    text += "<i>💡 CEX-only tokens (BTC, TAO, DOGE) не видны в этом source.</i>\n"
    send_message(chat_id, text)


def cmd_status(chat_id):
    snap = get_lab_snapshot()
    if not snap:
        send_message(chat_id, "⚠ Нет LAB snapshot. Запусти <code>/refresh</code> или подожди утренний cron (08:30 UTC).")
        return

    strk = snap.get('strk_status', {})
    triggers = snap.get('re_entry_triggers', {}).get('trigger_list', [])

    text = "<b>📍 STRK STATUS</b>\n"
    text += f"<i>Snapshot: {_snapshot_age_str(snap)}</i>\n\n"

    verdict = strk.get('verdict', 'UNKNOWN')
    emoji = {'STILL_ACCUMULATION': '🔴', 'EARLY_INFLECTION': '🟡',
             'WATCH_CLOSELY': '🟡', 'RE_ENTRY_ZONE': '🟢'}.get(verdict, '⚪')
    text += f"<b>{emoji} {_safe_html(verdict)}</b>\n"

    hit = strk.get('triggers_hit', 0)
    total = strk.get('triggers_total', 4)
    text += f"Triggers hit: <code>{hit}/{total}</code>\n\n"

    price = strk.get('strk_price')
    if price:
        text += f"Price: <code>${price:.4f}</code>\n"
    text += f"Wyckoff: <code>{_safe_html(strk.get('wyckoff_phase'))}</code>\n"
    text += f"Dune monthly: <code>{_safe_html(strk.get('dune_monthly_signal'))}</code>"
    if strk.get('bearish_30d') is not None:
        text += f" ({strk['bearish_30d']}/30d bearish)"
    text += "\n"
    text += f"CEX: <code>{_safe_html(strk.get('cex_signal'))}</code>\n\n"

    text += "<b>Triggers:</b>\n"
    for trig in triggers:
        if isinstance(trig, list) and len(trig) >= 3:
            mark, name, detail = trig[0], trig[1], trig[2]
            text += f"{mark} <b>{_safe_html(name)}</b>: {_safe_html(detail)}\n"
    text += "\n"

    rec = strk.get('recommendation', '')
    if rec:
        text += f"<b>💡 {_safe_html(rec)}</b>\n\n"

    # Quick STRONG_BUY summary
    sb = snap.get('strong_buy', [])
    if sb:
        text += f"<b>🟢 STRONG_BUY ({len(sb)} tokens):</b> " + ", ".join(
            f"<code>{t.get('token')}</code>" for t in sb[:8]
        ) + "\n"

    send_message(chat_id, text)


def cmd_check(chat_id, token_query):
    if not token_query:
        send_message(chat_id, "⚠ Укажи токен. Пример: <code>/check LINK</code>")
        return

    token_upper = token_query.strip().upper()
    snap = get_lab_snapshot()
    momentum = get_momentum_data()
    netflow = get_netflow_data()
    rotation = get_rotation_state()

    if not snap and not momentum and not netflow:
        send_message(chat_id, "⚠ Нет данных. Запусти <code>/refresh</code>.")
        return

    # Find token in momentum data (has price signal)
    mom_row = None
    if momentum:
        for r in momentum.get('rows', []):
            if isinstance(r, dict) and str(r.get('token', '')).upper() == token_upper:
                mom_row = r
                break

    # Find token in netflow data (has direction)
    nf_row = None
    if netflow:
        for r in netflow.get('rows', []):
            if isinstance(r, dict) and str(r.get('token', '')).upper() == token_upper:
                nf_row = r
                break

    if not mom_row and not nf_row:
        # Fallback: check LAB snapshot lists
        found_in = None
        for section in ('strong_buy', 'divergence', 'buy_pressure', 'sell'):
            for t in (snap or {}).get(section, []):
                if str(t.get('token', '')).upper() == token_upper:
                    found_in = (section, t)
                    break
            if found_in:
                break
        if not found_in:
            send_message(chat_id,
                f"⚠ Токен <code>{_safe_html(token_upper)}</code> не найден в universe.\n\n"
                f"<i>Проверь список tracked токенов через /list.</i>")
            return
        section, t = found_in
        text = f"<b>📊 {_safe_html(token_upper)}</b> ({_safe_html(t.get('sector'))})\n\n"
        text += f"Signal: <code>{_safe_html(t.get('signal') or section.upper())}</code>\n"
        nf = t.get('net_flow_m_usd', 0)
        text += f"Net flow 7d: <code>{nf:+.2f}M USD</code>\n"
        send_message(chat_id, text)
        return

    # Build detailed response
    sector = (mom_row or nf_row).get('sector', 'unknown')
    text = f"<b>📊 {_safe_html(token_upper)}</b> · <i>{_safe_html(sector)}</i>\n"
    text += f"<i>Snapshot: {_snapshot_age_str(snap)}</i>\n\n"

    # From momentum (price + signal)
    if mom_row:
        signal = mom_row.get('signal', 'NEUTRAL')
        sig_emoji = {
            'STRONG_BUY': '🟢', 'STRONG_SELL': '🔴',
            'DIVERGENCE': '⚠', 'NEUTRAL_FLOW_UP': '⚪', 'NEUTRAL': '⚪'
        }.get(signal, '⚪')
        text += f"<b>{sig_emoji} Signal: <code>{_safe_html(signal)}</code></b>\n\n"

        text += "<b>Flow:</b>\n"
        buy = mom_row.get('buy_volume_m_usd', 0) or 0
        sell = mom_row.get('sell_volume_m_usd', 0) or 0
        net = mom_row.get('net_flow_m_usd', 0) or 0
        text += f"  Buy vol 7d:   <code>${buy:.2f}M</code>\n"
        text += f"  Sell vol 7d:  <code>${sell:.2f}M</code>\n"
        text += f"  Net flow:     <code>{net:+.2f}M USD</code>"
        pct = mom_row.get('net_flow_pct')
        if pct is not None:
            text += f" ({pct:+.1f}%)"
        text += "\n\n"

        text += "<b>Price:</b>\n"
        pn = mom_row.get('price_now')
        p7 = mom_row.get('price_7d_ago')
        pc = mom_row.get('price_change_7d_pct', 0) or 0
        if pn:
            text += f"  Now:     <code>${pn:.4f}</code>\n"
        if p7:
            text += f"  7d ago:  <code>${p7:.4f}</code>\n"
        text += f"  Change:  <code>{pc:+.1f}%</code>\n\n"

        tx = mom_row.get('tx_count', 0) or 0
        text += f"<b>Liquidity:</b> <code>{tx:,} tx</code> за 7d "
        text += "(HIGH)" if tx > 50000 else "(MEDIUM)" if tx > 10000 else "(LOW)"
        text += "\n\n"

    # From rotation tracker: streak
    if rotation:
        streaks = rotation.get('signal_streaks', {})
        streak = streaks.get(token_upper) or streaks.get(token_query.strip())
        if streak:
            text += f"<b>📅 STRONG_BUY streak:</b> <code>{streak}d</code>"
            if streak >= 3:
                text += " ✅ CONFIRMED"
            text += "\n\n"

    # Assessment (только базовое — на основе data, без выдуманной precision)
    if mom_row:
        signal = mom_row.get('signal', 'NEUTRAL')
        net = mom_row.get('net_flow_m_usd', 0) or 0
        pc = mom_row.get('price_change_7d_pct', 0) or 0
        tx = mom_row.get('tx_count', 0) or 0

        text += "<b>Assessment:</b>\n"
        if signal == 'STRONG_BUY':
            if net > 10 and pc > 10 and tx > 50000:
                text += "  🟢 <b>Strong confluence</b> — significant flow + price + liquidity.\n"
            elif net > 1 and pc > 5:
                text += "  🟢 Moderate STRONG_BUY.\n"
            else:
                text += "  🟡 STRONG_BUY signal, но numbers скромные.\n"
        elif signal == 'DIVERGENCE':
            text += "  ⚠ Price rally без buy flow — часто fake breakout.\n"
        elif signal in ('STRONG_SELL', 'SELL_PRESSURE'):
            text += "  🔴 Distribution — избегать входа.\n"
        else:
            text += f"  ⚪ {signal} — no clear edge.\n"

    text += "\n<i>⚠ Historical precision этих signals ещё measuring (backtest module в разработке).</i>\n"
    text += "<i>💡 Not advice. Проверяй технику отдельно.</i>"

    send_message(chat_id, text)


def cmd_list(chat_id, sector_filter=None):
    momentum = get_momentum_data()
    if not momentum:
        send_message(chat_id, "⚠ Нет momentum data. Запусти <code>/refresh</code>.")
        return

    rows = momentum.get('rows', [])
    if not rows:
        send_message(chat_id, "⚠ Momentum data пустая.")
        return

    LIQUIDITY_FLOOR = 5000
    sector_upper = sector_filter.strip().upper() if sector_filter else None

    # Group by signal
    buckets = {'STRONG_BUY': [], 'DIVERGENCE': [], 'STRONG_SELL': [], 'NEUTRAL': []}
    for r in rows:
        if not isinstance(r, dict):
            continue
        tx = r.get('tx_count', 0) or 0
        if tx < LIQUIDITY_FLOOR:
            continue
        if sector_upper and str(r.get('sector', '')).upper() != sector_upper:
            continue
        sig = r.get('signal', 'NEUTRAL')
        if sig in buckets:
            buckets[sig].append(r)

    for bucket in buckets.values():
        bucket.sort(key=lambda x: (x.get('net_flow_m_usd', 0) or 0), reverse=True)

    header = f"<b>📋 LAB Tokens</b>"
    if sector_upper:
        header += f" · sector: <code>{_safe_html(sector_upper)}</code>"
    header += f"\n<i>Snapshot: {_snapshot_age_str(momentum)}</i>\n\n"

    text = header
    total_shown = 0

    if buckets['STRONG_BUY']:
        text += f"<b>🟢 STRONG_BUY ({len(buckets['STRONG_BUY'])})</b>\n"
        for r in buckets['STRONG_BUY'][:8]:
            net = r.get('net_flow_m_usd', 0) or 0
            pc = r.get('price_change_7d_pct', 0) or 0
            text += (f"  <code>{r.get('token'):<7}</code> "
                     f"({_safe_html(r.get('sector'))}) "
                     f"net <code>{net:+.2f}M</code> · <code>{pc:+.1f}%</code>\n")
        total_shown += len(buckets['STRONG_BUY'])
        text += "\n"

    if buckets['DIVERGENCE']:
        text += f"<b>⚠ DIVERGENCE ({len(buckets['DIVERGENCE'])})</b>\n"
        for r in buckets['DIVERGENCE'][:5]:
            net = r.get('net_flow_m_usd', 0) or 0
            pc = r.get('price_change_7d_pct', 0) or 0
            text += (f"  <code>{r.get('token'):<7}</code> "
                     f"net <code>{net:+.2f}M</code> · price <code>{pc:+.1f}%</code>\n")
        total_shown += len(buckets['DIVERGENCE'])
        text += "\n"

    if buckets['STRONG_SELL']:
        text += f"<b>🔴 STRONG_SELL ({len(buckets['STRONG_SELL'])})</b>\n"
        for r in buckets['STRONG_SELL'][:5]:
            net = r.get('net_flow_m_usd', 0) or 0
            pc = r.get('price_change_7d_pct', 0) or 0
            text += (f"  <code>{r.get('token'):<7}</code> "
                     f"net <code>{net:+.2f}M</code> · <code>{pc:+.1f}%</code>\n")
        total_shown += len(buckets['STRONG_SELL'])
        text += "\n"

    if total_shown == 0:
        text += "<i>Нет tokens подходящих под фильтр (или все с tx_count &lt; 5000).</i>\n\n"
        # Show available sectors
        all_sectors = sorted({r.get('sector') for r in rows if isinstance(r, dict) and r.get('sector')})
        if sector_filter and all_sectors:
            text += f"<b>Available sectors:</b> " + ", ".join(f"<code>{s}</code>" for s in all_sectors)

    send_message(chat_id, text)


def cmd_refresh(chat_id):
    """Trigger LAB pipeline via GitHub Actions workflow_dispatch."""
    # Мы не можем dispatch напрямую без PAT (GITHUB_TOKEN у нас в secrets).
    # Просто сообщаем что делать.
    text = "<b>🔄 Refresh LAB</b>\n\n"
    text += "Automatic dispatch пока не настроен.\n\n"
    text += "<b>Ручной запуск:</b>\n"
    text += "1. Открой GitHub Actions в repo\n"
    text += "2. Выбери workflow <b>STRK Engine</b>\n"
    text += "3. Run workflow → mode = <code>lab</code>\n\n"
    text += "<b>Автоматически:</b> каждый день в 08:30 UTC (11:30 MSK)\n\n"
    text += "<i>💡 Если хочешь auto-dispatch по /refresh — нужен PAT (GitHub Personal Access Token) в secrets. Скажу как настроить если нужно.</i>"
    send_message(chat_id, text)


# ============================================================
# COMMAND DISPATCH
# ============================================================
def process_command(chat_id, text):
    text = (text or '').strip()
    if not text.startswith('/'):
        return

    parts = text.split(None, 1)
    cmd = parts[0].lower()
    # Handle @botname suffix (e.g. /status@Lab_sector_bot)
    if '@' in cmd:
        cmd = cmd.split('@', 1)[0]
    arg = parts[1] if len(parts) > 1 else ''

    logger.info(f"CMD from chat {chat_id}: {cmd} arg={arg[:50]}")

    if cmd in ('/help', '/start'):
        cmd_help(chat_id)
    elif cmd == '/status':
        cmd_status(chat_id)
    elif cmd == '/check':
        cmd_check(chat_id, arg)
    elif cmd == '/list':
        cmd_list(chat_id, arg if arg else None)
    elif cmd == '/refresh':
        cmd_refresh(chat_id)
    else:
        send_message(chat_id, f"Unknown command: {_safe_html(cmd)}\nSend <code>/help</code> for list.")


def process_updates(updates, state):
    max_id = state.get('last_update_id', 0)
    for u in updates:
        uid = u.get('update_id', 0)
        max_id = max(max_id, uid)

        msg = u.get('message') or u.get('edited_message')
        if not msg:
            continue

        chat_id = msg.get('chat', {}).get('id')
        text = msg.get('text', '') or ''

        # Only respond to authorized chat
        if CHAT_ID and str(chat_id) != CHAT_ID:
            logger.warning(f"❌ IGNORING message from chat_id={chat_id} (authorized CHAT_ID env={CHAT_ID})")
            logger.warning(f"   ↑ Если это ТВОЙ чат — обнови secret TELEGRAM_CHAT_ID на {chat_id}")
            continue

        logger.info(f"✓ Authorized chat_id={chat_id} · processing text: {text[:80]!r}")

        if text.startswith('/'):
            try:
                process_command(chat_id, text)
            except Exception as e:
                logger.exception(f"Error processing command: {e}")
                send_message(chat_id, f"⚠ Internal error: {_safe_html(str(e)[:100])}")

    state['last_update_id'] = max_id
    return state


# ============================================================
# MAIN
# ============================================================
def run_once():
    logger.info("=" * 60)
    logger.info("LAB BOT POLLING · handling one iteration")
    logger.info("=" * 60)

    if not LAB_BOT_TOKEN:
        logger.error("❌ TELEGRAM_LAB_SECTOR_BOT env NOT SET — cannot run")
        print("::error title=LAB bot token missing::Add TELEGRAM_LAB_SECTOR_BOT secret in repo settings")
        return 1
    logger.info(f"✓ Bot token present (len={len(LAB_BOT_TOKEN)})")

    if not CHAT_ID:
        logger.warning("⚠ TELEGRAM_CHAT_ID env NOT SET — bot will respond to ANY chat (unsafe)")
    else:
        logger.info(f"✓ CHAT_ID={CHAT_ID} (only this chat authorized)")

    # Verify bot is alive
    try:
        me_url = f"https://api.telegram.org/bot{LAB_BOT_TOKEN}/getMe"
        with urllib.request.urlopen(me_url, timeout=10) as r:
            me = json.loads(r.read())
        if me.get('ok'):
            bot_info = me.get('result', {})
            logger.info(f"✓ Bot alive: @{bot_info.get('username')} (id={bot_info.get('id')})")
        else:
            logger.error(f"❌ getMe failed: {me}")
            return 1
    except Exception as e:
        logger.error(f"❌ Cannot reach Telegram API: {e}")
        return 1

    state = load_state()
    offset = state.get('last_update_id', 0) + 1
    logger.info(f"State: last_update_id={state.get('last_update_id', 0)} → asking offset={offset}")

    # If state is fresh (offset == 1), get real offset from Telegram
    if offset <= 1:
        logger.info("Fresh state — fetching offset from Telegram")
        offset = get_offset_from_telegram()
        logger.info(f"Telegram says offset should be {offset}")

    updates = get_updates(offset=offset)
    logger.info(f"📥 Received {len(updates)} updates from getUpdates(offset={offset})")

    if not updates:
        logger.info("No new updates. If you sent /help but bot didn't receive it:")
        logger.info("  1. Check @Lab_sector_bot in Telegram — did you press START?")
        logger.info("  2. Try: delete data/cache/lab_bot_state.json + rerun this workflow")
        logger.info("  3. Check https://api.telegram.org/bot<TOKEN>/getUpdates to see raw updates")
        return 0

    # Log what we got
    for u in updates[:5]:
        msg = u.get('message') or u.get('edited_message') or {}
        chat = msg.get('chat', {})
        text = msg.get('text', '')[:80]
        logger.info(f"  update_id={u.get('update_id')} chat_id={chat.get('id')} text={text!r}")

    state = process_updates(updates, state)
    save_state(state)
    logger.info(f"✓ Processed {len(updates)} updates · new last_update_id={state.get('last_update_id')}")
    return 0


def run_loop(interval=5):
    logger.info(f"LAB bot loop started · interval={interval}s")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception as e:
            logger.exception(f"Loop error: {e}")
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description='LAB bot command handler')
    parser.add_argument('--once', action='store_true',
                        help='Run one iteration and exit (for GitHub Actions cron)')
    parser.add_argument('--interval', type=int, default=5,
                        help='Poll interval seconds (for loop mode)')
    args = parser.parse_args()

    if args.once:
        return run_once()
    else:
        run_loop(args.interval)
        return 0


if __name__ == '__main__':
    sys.exit(main())
