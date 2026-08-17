#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_summary.py — генерирует weekly LAB summary + отправляет в @Lab_sector_bot.

Cron: воскресенье 21:00 UTC (00:00 MSK понедельника).
Также вызывается через /week команду в боте.

Читает:
  data/history/lab_signals.jsonl     — issued/closed backtest signals
  data/history/rotation_alerts.jsonl — rotation tracker history
  data/cache/strk_lab_report.json    — текущий STRK statuс
  data/cache/lab_signals_summary.json — precision summary

Сохраняет:
  data/cache/weekly_summary.json      — для Worker (команда /week)

Отправляет:
  → @Lab_sector_bot (при cron запуске)
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'

LAB_SIGNALS = HISTORY_DIR / 'lab_signals.jsonl'
ROTATION_ALERTS = HISTORY_DIR / 'rotation_alerts.jsonl'
LAB_SNAPSHOT = CACHE_DIR / 'strk_lab_report.json'
SIGNALS_SUMMARY = CACHE_DIR / 'lab_signals_summary.json'
WEEKLY_OUT = CACHE_DIR / 'weekly_summary.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def load_json(path, default=None):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def read_jsonl(path):
    if not path.exists():
        return []
    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def build_weekly_summary():
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    result = {
        'generated_at': now.isoformat(),
        'period_start': week_ago.isoformat(),
        'period_end': now.isoformat(),
    }

    # ============================================================
    # 1. STRK STATUS · текущий + сравнение
    # ============================================================
    snap = load_json(LAB_SNAPSHOT)
    strk_status = snap.get('strk_status', {}) if snap else {}
    result['strk_status'] = {
        'verdict': strk_status.get('verdict'),
        'triggers_hit': strk_status.get('triggers_hit'),
        'triggers_total': strk_status.get('triggers_total', 4),
        'wyckoff_phase': strk_status.get('wyckoff_phase'),
        'dune_monthly_signal': strk_status.get('dune_monthly_signal'),
        'cex_signal': strk_status.get('cex_signal'),
        'bearish_30d': strk_status.get('bearish_30d'),
        'strk_price': strk_status.get('strk_price'),
    }

    # ============================================================
    # 2. LAB SIGNALS · за 7 дней
    # ============================================================
    all_signals = read_jsonl(LAB_SIGNALS)

    signals_7d = []
    closed_7d = []
    for s in all_signals:
        issued = parse_ts(s.get('issued_at'))
        if issued and issued >= week_ago:
            signals_7d.append(s)
        closed_at = parse_ts(s.get('closed_at'))
        if closed_at and closed_at >= week_ago:
            closed_7d.append(s)

    # Group new signals by token
    new_tokens = Counter()
    sectors_seen = Counter()
    for s in signals_7d:
        tk = s.get('token')
        if tk:
            new_tokens[tk] += 1
            sectors_seen[s.get('sector', 'unknown')] += 1

    result['lab_signals_7d'] = {
        'total_issued': len(signals_7d),
        'total_closed': len(closed_7d),
        'unique_tokens': len(new_tokens),
        'top_active_tokens': new_tokens.most_common(5),
        'sectors_hit': dict(sectors_seen),
    }

    # Closed outcomes
    hits_7d = sum(1 for s in closed_7d if s.get('outcome') == 'HIT')
    misses_7d = sum(1 for s in closed_7d if s.get('outcome') == 'MISS')
    neutrals_7d = sum(1 for s in closed_7d if s.get('outcome') == 'NEUTRAL')
    actionable_7d = hits_7d + misses_7d + neutrals_7d
    result['lab_signals_7d']['hits'] = hits_7d
    result['lab_signals_7d']['misses'] = misses_7d
    result['lab_signals_7d']['neutrals'] = neutrals_7d
    result['lab_signals_7d']['precision_7d'] = (
        round(hits_7d / actionable_7d * 100, 1) if actionable_7d else None
    )

    # ============================================================
    # 3. STREAKS · какие tokens дольше всех держались
    # ============================================================
    token_first_seen = {}
    token_last_seen = {}
    for s in all_signals:
        tk = s.get('token')
        ts = parse_ts(s.get('issued_at'))
        if not tk or not ts:
            continue
        if tk not in token_first_seen or ts < token_first_seen[tk]:
            token_first_seen[tk] = ts
        if tk not in token_last_seen or ts > token_last_seen[tk]:
            token_last_seen[tk] = ts

    streaks = []
    for tk in new_tokens.keys():
        first = token_first_seen.get(tk)
        last = token_last_seen.get(tk)
        if first and last:
            duration_days = (last - first).total_seconds() / 86400 + 1  # +1 чтоб single-day = 1
            streaks.append({
                'token': tk,
                'duration_days': round(duration_days, 1),
                'issues_count': new_tokens[tk],
            })
    streaks.sort(key=lambda x: -x['duration_days'])
    result['top_streaks'] = streaks[:5]

    # ============================================================
    # 4. ROTATION TRACKER · alerts за 7 дней
    # ============================================================
    rotation = read_jsonl(ROTATION_ALERTS)
    rotation_7d = [r for r in rotation if parse_ts(r.get('ts')) and parse_ts(r.get('ts')) >= week_ago]

    events = defaultdict(list)
    for r in rotation_7d:
        for token in r.get('entered_strong_buy', []):
            events['new'].append(token)
        for token in r.get('exited_strong_buy_quiet', []):
            events['exit'].append(token)
        for token in r.get('strong_to_divergence', []):
            events['divergence'].append(token)
        for token in r.get('strong_to_sell', []):
            events['sell'].append(token)

    result['rotation_events_7d'] = {
        'new_strong_buy': events['new'],
        'exited': events['exit'],
        'divergence_warn': events['divergence'],
        'sell_signals': events['sell'],
        'total_alert_runs': len(rotation_7d),
    }

    # ============================================================
    # 5. OVERALL BACKTEST · lab_signals_summary
    # ============================================================
    signals_summary = load_json(SIGNALS_SUMMARY)
    if signals_summary:
        overall = signals_summary.get('overall', {})
        result['backtest_overall'] = {
            'n_closed': overall.get('n_closed'),
            'n_actionable': overall.get('n_actionable'),
            'precision_pct': overall.get('precision_pct'),
            'has_enough_data': overall.get('has_enough_data', False),
            'high_confidence': overall.get('high_confidence', False),
        }
        # Top tokens by precision (if have data)
        per_token = signals_summary.get('per_token', {})
        token_precision = []
        for tk, stats in per_token.items():
            if stats.get('has_enough_data') and stats.get('precision_pct') is not None:
                token_precision.append({
                    'token': tk,
                    'precision_pct': stats['precision_pct'],
                    'n_actionable': stats['n_actionable'],
                    'avg_return_pct': stats.get('avg_return_pct'),
                })
        token_precision.sort(key=lambda x: -x['precision_pct'])
        result['top_precision_tokens'] = token_precision[:5]
    else:
        result['backtest_overall'] = None
        result['top_precision_tokens'] = []

    return result


def format_summary(summary):
    """Format for Telegram HTML."""
    def safe(s):
        return str(s).replace('<', '&lt;').replace('>', '&gt;') if s is not None else 'n/a'

    now = datetime.now(timezone.utc)
    text = f"<b>📅 STRK LAB · WEEKLY SUMMARY</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')} · период 7 дней</i>\n\n"

    # STRK STATUS
    strk = summary.get('strk_status', {})
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📍 STRK CURRENT STATE</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    verdict = strk.get('verdict', 'UNKNOWN')
    emoji = {'STILL_ACCUMULATION': '🔴', 'EARLY_INFLECTION': '🟡',
             'WATCH_CLOSELY': '🟡', 'RE_ENTRY_ZONE': '🟢'}.get(verdict, '⚪')
    text += f"{emoji} <b>{safe(verdict)}</b>\n"
    text += f"Triggers: <code>{strk.get('triggers_hit', 0)}/{strk.get('triggers_total', 4)}</code>\n"
    if strk.get('strk_price'):
        text += f"Price: <code>${strk['strk_price']:.4f}</code>\n"
    if strk.get('bearish_30d') is not None:
        text += f"Dune: {safe(strk.get('dune_monthly_signal'))} ({strk['bearish_30d']}/30d bearish)\n"
    text += "\n"

    # LAB SIGNALS 7d
    ls = summary.get('lab_signals_7d', {})
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🟢 LAB SIGNALS (7d)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"Issued:   <code>{ls.get('total_issued', 0)}</code>\n"
    text += f"Closed:   <code>{ls.get('total_closed', 0)}</code>"
    if ls.get('total_closed'):
        text += (f" · HIT <code>{ls.get('hits', 0)}</code>, "
                 f"MISS <code>{ls.get('misses', 0)}</code>, "
                 f"NEUTRAL <code>{ls.get('neutrals', 0)}</code>")
    text += "\n"
    if ls.get('precision_7d') is not None:
        text += f"Precision (7d): <code>{ls['precision_7d']}%</code>\n"

    top_tokens = ls.get('top_active_tokens') or []
    if top_tokens:
        text += "\n<b>Top active:</b>\n"
        for tk, count in top_tokens[:5]:
            text += f"  <code>{safe(tk)}</code>: {count} issues\n"

    sectors = ls.get('sectors_hit') or {}
    if sectors:
        text += "\n<b>Sectors hit:</b> "
        sorted_sect = sorted(sectors.items(), key=lambda x: -x[1])
        text += ", ".join(f"<code>{safe(s)}</code>×{c}" for s, c in sorted_sect[:6])
        text += "\n"
    text += "\n"

    # TOP STREAKS
    streaks = summary.get('top_streaks') or []
    if streaks:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🏆 TOP STREAKS (7d)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for st in streaks[:5]:
            text += (f"  <code>{safe(st['token']):<7}</code> · "
                     f"<code>{st['duration_days']}d</code> streak · "
                     f"{st['issues_count']} issues\n")
        text += "\n"

    # ROTATION EVENTS
    rot = summary.get('rotation_events_7d', {})
    total_events = (len(rot.get('new_strong_buy') or [])
                    + len(rot.get('exited') or [])
                    + len(rot.get('divergence_warn') or [])
                    + len(rot.get('sell_signals') or []))
    if total_events > 0:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🔄 ROTATION EVENTS (7d)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        if rot.get('new_strong_buy'):
            text += f"📈 NEW STRONG_BUY: <code>{safe(', '.join(rot['new_strong_buy']))}</code>\n"
        if rot.get('exited'):
            text += f"⚪ Exited: <code>{safe(', '.join(rot['exited']))}</code>\n"
        if rot.get('divergence_warn'):
            text += f"⚠ Divergence: <code>{safe(', '.join(rot['divergence_warn']))}</code>\n"
        if rot.get('sell_signals'):
            text += f"🚪 Sell: <code>{safe(', '.join(rot['sell_signals']))}</code>\n"
        text += "\n"

    # BACKTEST OVERALL
    bt = summary.get('backtest_overall')
    if bt and bt.get('n_actionable'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📊 BACKTEST (all-time)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"N closed: <code>{bt['n_closed']}</code> (actionable <code>{bt['n_actionable']}</code>)\n"
        if bt.get('precision_pct') is not None and bt.get('has_enough_data'):
            conf = 'HIGH' if bt.get('high_confidence') else 'early (wide CI)'
            text += f"Precision: <code>{bt['precision_pct']}%</code> <i>({conf})</i>\n"
        text += "\n"

    # TOP PRECISION
    top_prec = summary.get('top_precision_tokens') or []
    if top_prec:
        text += "<b>🎯 Top precision tokens:</b>\n"
        for t in top_prec[:5]:
            avg = t.get('avg_return_pct')
            avg_str = f" · avg return <code>{avg:+.1f}%</code>" if avg is not None else ''
            text += (f"  <code>{safe(t['token']):<7}</code> "
                     f"<code>{t['precision_pct']}%</code> "
                     f"(N={t['n_actionable']}){avg_str}\n")
        text += "\n"

    # FOOTER
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>💡 Weekly cron: воскресенье 00:00 MSK · ручной запрос /week</i>\n"
    text += "<i>💡 Backtest precision появится через 7+ дней после первых issues</i>"

    return text


def split_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    while len(text) > max_len:
        cut = text.rfind('\n', 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip()
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
                if not result.get('ok'):
                    logger.error(f"Telegram error: {result}")
                    return False
        except Exception as e:
            logger.error(f"Send failed: {e}")
            return False
    return True


def main():
    logger.info("=" * 60)
    logger.info("WEEKLY SUMMARY · LAB")
    logger.info("=" * 60)

    summary = build_weekly_summary()

    # Save для Worker команды /week
    save_json(WEEKLY_OUT, summary)
    logger.info(f"Summary saved to {WEEKLY_OUT.name}")

    # Log key numbers
    ls = summary.get('lab_signals_7d', {})
    rot = summary.get('rotation_events_7d', {})
    logger.info(f"  Signals issued 7d: {ls.get('total_issued', 0)}")
    logger.info(f"  Signals closed 7d: {ls.get('total_closed', 0)}")
    logger.info(f"  Rotation runs 7d: {rot.get('total_alert_runs', 0)}")

    # Send to Telegram
    text = format_summary(summary)
    token = os.environ.get('TELEGRAM_LAB_SECTOR_BOT', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

    if not token:
        logger.warning("TELEGRAM_LAB_SECTOR_BOT not set — summary saved but not sent")
    else:
        sent = send_telegram(text, token, chat_id)
        logger.info(f"Telegram send: {'✓' if sent else '✗'}")

    logger.info("=" * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())