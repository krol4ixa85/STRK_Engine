#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_backtest.py — автоматическая недельная проверка точности.

Читает 2 источника:
  · data/history/shadow_votes.jsonl (2 записи per run — 72h + 7d windows)
  · data/history/all_history.jsonl (compact snapshot per RUN)

Вычисляет accuracy для CLOSED forecasts:
  1. Confluence Gate (real DECISION) — из shadow_votes CLOSED
  2. Composite v2 — из all_history CLOSED
  3. Каждый Shadow Voter — из per_voter_outcome в shadow_votes CLOSED
     · HIT/MISS/SKIP считает уже shadow_postmortem.py

Генерирует HTML отчёт → data/reports/weekly_backtest_YYYY-MM-DD.html
Отправляет summary в Telegram (@STRK_GUARDIAN_BOT).

Классификация outcome (задано в shadow_postmortem.py):
  RALLY   если change > +3% за window
  CRASH   если change < -3%
  NEUTRAL иначе

Пороги минимальной статистики:
  N < 5   → "insufficient data" (не показываем %)
  N < 15  → показываем с warning
  N ≥ 15  → показываем без warning (достаточно для калибровки)
"""
import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
REPORTS_DIR = SCRIPT_DIR / 'data' / 'reports'
SHADOW_FILE = HISTORY_DIR / 'shadow_votes.jsonl'
HISTORY_FILE = HISTORY_DIR / 'all_history.jsonl'

MIN_N_FOR_CALIBRATION = 15
MIN_N_TO_SHOW = 5


def load_jsonl(path):
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


def confluence_signal_family(signal):
    """Reduce 'RALLY_HIGH_CONFLUENCE' / 'RALLY_MEDIUM' → 'RALLY' for comparison."""
    if not signal:
        return None
    s = str(signal).upper()
    if 'RALLY' in s:
        return 'RALLY'
    if 'CRASH' in s:
        return 'CRASH'
    return 'NEUTRAL'


def evaluate_confluence(shadow_records):
    """Из shadow_votes.jsonl CLOSED считаем accuracy confluence_gate."""
    results = {'72h': {'hits': 0, 'total': 0, 'per_signal': defaultdict(lambda: {'hits': 0, 'total': 0})},
               '7d':  {'hits': 0, 'total': 0, 'per_signal': defaultdict(lambda: {'hits': 0, 'total': 0})}}
    for r in shadow_records:
        window = r.get('window')
        if window not in results:
            continue
        outcome_signal = r.get('outcome_signal')
        if not outcome_signal:  # PENDING
            continue
        predicted_raw = r.get('current_confluence_signal', '')
        predicted = confluence_signal_family(predicted_raw)
        if not predicted:
            continue
        # Confidence — уточним по HIGH/MEDIUM
        confidence = r.get('current_confluence_confidence', 'LOW')
        # Family match
        is_hit = (predicted == outcome_signal)
        results[window]['total'] += 1
        if is_hit:
            results[window]['hits'] += 1
        # Per-signal breakdown
        key = f"{predicted}_{confidence}"
        results[window]['per_signal'][key]['total'] += 1
        if is_hit:
            results[window]['per_signal'][key]['hits'] += 1
    return results


def evaluate_composite_v2(history_records):
    """Из all_history.jsonl CLOSED считаем composite_v2 accuracy."""
    results = {'72h': {'hits': 0, 'total': 0}, '7d': {'hits': 0, 'total': 0}}
    for r in history_records:
        if r.get('status') != 'CLOSED':
            continue
        live = r.get('live_signals') or {}
        comp = live.get('composite_v2') or live.get('composite_signal_v2') or {}
        predicted = confluence_signal_family(comp.get('signal') or comp.get('direction'))
        if not predicted:
            continue
        for window in ['72h', '7d']:
            outcome = r.get(f'outcome_{window}')
            if not outcome or not isinstance(outcome, dict):
                continue
            outcome_signal = outcome.get('signal')
            if not outcome_signal:
                continue
            results[window]['total'] += 1
            if predicted == outcome_signal:
                results[window]['hits'] += 1
    return results


def evaluate_shadow_voters(shadow_records):
    """Из per_voter_outcome в CLOSED shadow_votes считаем accuracy каждого voter'а."""
    voters = defaultdict(lambda: {'72h': {'hits': 0, 'total': 0, 'skips': 0},
                                   '7d':  {'hits': 0, 'total': 0, 'skips': 0}})
    for r in shadow_records:
        window = r.get('window')
        if window not in ('72h', '7d'):
            continue
        per_voter = r.get('per_voter_outcome')
        if not per_voter:  # PENDING
            continue
        for voter_name, verdict in per_voter.items():
            v = str(verdict).upper()
            if v == 'HIT':
                voters[voter_name][window]['hits'] += 1
                voters[voter_name][window]['total'] += 1
            elif v == 'MISS':
                voters[voter_name][window]['total'] += 1
            elif v == 'SKIP':
                voters[voter_name][window]['skips'] += 1
    return dict(voters)


def format_pct(hits, total):
    if total == 0:
        return 'N/A'
    return f"{hits/total*100:.1f}%"


def n_warning(total):
    if total < MIN_N_TO_SHOW:
        return " ⚠️ insufficient data (N&lt;5)"
    if total < MIN_N_FOR_CALIBRATION:
        return f" ⚠️ N={total}&lt;15 not calibrated"
    return ""


def build_html_report(shadow_all, history_all, conf_results, comp_results, voter_results):
    """Full HTML report with breakdown."""
    now = datetime.now(timezone.utc)
    now_str = now.strftime('%Y-%m-%d %H:%M UTC')

    # Общая статистика
    shadow_closed = sum(1 for r in shadow_all if r.get('outcome_signal'))
    shadow_pending = len(shadow_all) - shadow_closed
    history_closed = sum(1 for r in history_all if r.get('status') == 'CLOSED')
    history_pending = len(history_all) - history_closed

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>STRK Engine · Weekly Backtest · {now_str}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; max-width: 900px; margin: 20px auto; padding: 20px; background: #f7f7f9; color: #222; }}
  h1 {{ color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ color: #444; margin-top: 32px; border-left: 4px solid #4c9aff; padding-left: 10px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; background: #fff; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
  th {{ background: #f0f0f5; }}
  .warn {{ color: #cc8800; font-size: 0.9em; }}
  .ok {{ color: #0a7000; font-weight: bold; }}
  .miss {{ color: #a00000; font-weight: bold; }}
  .na {{ color: #888; }}
  .footer {{ margin-top: 40px; font-size: 0.85em; color: #888; }}
  pre {{ background: #f0f0f5; padding: 10px; border-radius: 4px; overflow-x: auto; }}
</style></head>
<body>

<h1>📊 STRK Engine · Weekly Backtest</h1>
<p><b>Generated:</b> {now_str}</p>

<h2>📈 Общая статистика</h2>
<table>
  <tr><th>Источник</th><th>Всего</th><th>CLOSED</th><th>PENDING</th></tr>
  <tr><td>shadow_votes.jsonl</td><td>{len(shadow_all)}</td><td>{shadow_closed}</td><td>{shadow_pending}</td></tr>
  <tr><td>all_history.jsonl</td><td>{len(history_all)}</td><td>{history_closed}</td><td>{history_pending}</td></tr>
</table>

<h2>🎯 Confluence Gate (real DECISION)</h2>
<table>
  <tr><th>Window</th><th>Accuracy</th><th>N (closed)</th><th>Note</th></tr>'''

    for window in ['72h', '7d']:
        r = conf_results[window]
        acc = format_pct(r['hits'], r['total'])
        warn = n_warning(r['total'])
        html += f'''
  <tr><td>{window}</td><td>{acc}</td><td>{r['total']}</td><td class="warn">{warn}</td></tr>'''

    html += '''
</table>

<h3>Breakdown по signal type</h3>
<table>
  <tr><th>Window</th><th>Signal</th><th>Accuracy</th><th>N</th></tr>'''

    for window in ['72h', '7d']:
        for sig_conf, stats in sorted(conf_results[window]['per_signal'].items()):
            acc = format_pct(stats['hits'], stats['total'])
            html += f'''
  <tr><td>{window}</td><td>{sig_conf}</td><td>{acc}</td><td>{stats['total']}</td></tr>'''

    html += '''
</table>

<h2>🧠 Composite v2 (context signal)</h2>
<table>
  <tr><th>Window</th><th>Accuracy</th><th>N (closed)</th><th>Note</th></tr>'''

    for window in ['72h', '7d']:
        r = comp_results[window]
        acc = format_pct(r['hits'], r['total'])
        warn = n_warning(r['total'])
        html += f'''
  <tr><td>{window}</td><td>{acc}</td><td>{r['total']}</td><td class="warn">{warn}</td></tr>'''

    html += '''
</table>

<h2>🔍 Shadow Voters (candidates for voter_wire_v2)</h2>
<p>HIT/MISS/SKIP уже посчитан <code>shadow_postmortem.py</code>. Accuracy = HITS / (HITS + MISSES), SKIP excluded.</p>
<table>
  <tr><th>Voter</th><th>72h Accuracy</th><th>72h N</th><th>7d Accuracy</th><th>7d N</th><th>Ready for wire-in?</th></tr>'''

    for voter, wins in sorted(voter_results.items()):
        r72 = wins['72h']
        r7d = wins['7d']
        acc72 = format_pct(r72['hits'], r72['total'])
        acc7d = format_pct(r7d['hits'], r7d['total'])
        # Ready if N>=15 AND accuracy>=55% on 72h
        ready = "no"
        if r72['total'] >= MIN_N_FOR_CALIBRATION and r72['hits'] / r72['total'] >= 0.55:
            ready = "<b class='ok'>YES</b>"
        elif r72['total'] < MIN_N_FOR_CALIBRATION:
            ready = f"<span class='warn'>N={r72['total']}&lt;15</span>"
        else:
            ready = f"<span class='miss'>precision {acc72}&lt;55%</span>"
        html += f'''
  <tr><td><code>{voter}</code></td><td>{acc72}</td><td>{r72['total']}</td><td>{acc7d}</td><td>{r7d['total']}</td><td>{ready}</td></tr>'''

    html += f'''
</table>

<h2>💡 Что это значит</h2>
<ul>
  <li><b>Confluence Gate</b> — главный DECISION. Если accuracy на 72h &lt; 55%, пороги нужно пересматривать.</li>
  <li><b>Composite v2</b> — старый composite baseline. Точность для сравнения.</li>
  <li><b>Shadow Voters</b> — кандидаты в voter_wire_v2 (ШАГ 3). Wire-in только если precision ≥ 55% на N≥15.</li>
  <li><b>SKIP votes</b> — voter отказался голосовать (NEUTRAL/UNKNOWN). Не считаются ошибкой.</li>
</ul>

<div class="footer">
  <p>STRK Engine · weekly_backtest.py · N&lt;15 = недостаточно для калибровки.</p>
  <p>Пороги: RALLY &gt; +3% · CRASH &lt; -3% · NEUTRAL иначе.</p>
</div>

</body></html>'''
    return html


def build_telegram_summary(shadow_all, history_all, conf_results, comp_results, voter_results):
    """Compact Telegram summary — sent as regular message."""
    now = datetime.now(timezone.utc)
    shadow_closed = sum(1 for r in shadow_all if r.get('outcome_signal'))

    lines = []
    lines.append(f"<b>📊 STRK Engine · Weekly Backtest</b>")
    lines.append(f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n")

    lines.append(f"<b>Data:</b> {shadow_closed}/{len(shadow_all)} shadow closed · {len(history_all)} history records\n")

    # Confluence
    r72 = conf_results['72h']
    r7d = conf_results['7d']
    if r72['total'] >= MIN_N_TO_SHOW or r7d['total'] >= MIN_N_TO_SHOW:
        lines.append(f"<b>🎯 Confluence Gate:</b>")
        lines.append(f"  72h: {format_pct(r72['hits'], r72['total'])} (N={r72['total']})")
        lines.append(f"  7d:  {format_pct(r7d['hits'], r7d['total'])} (N={r7d['total']})")
    else:
        lines.append(f"<b>🎯 Confluence:</b> insufficient data (N&lt;5)")
    lines.append("")

    # Composite
    r72 = comp_results['72h']
    if r72['total'] >= MIN_N_TO_SHOW:
        lines.append(f"<b>🧠 Composite v2 (72h):</b> {format_pct(r72['hits'], r72['total'])} (N={r72['total']})")
    else:
        lines.append(f"<b>🧠 Composite v2:</b> insufficient data")
    lines.append("")

    # Voters ranked
    lines.append(f"<b>🔍 Shadow Voters (72h):</b>")
    voter_ranked = []
    for voter, wins in voter_results.items():
        r = wins['72h']
        if r['total'] >= MIN_N_TO_SHOW:
            pct = r['hits'] / r['total'] * 100
            voter_ranked.append((voter, pct, r['total']))
        else:
            voter_ranked.append((voter, None, r['total']))
    voter_ranked.sort(key=lambda x: -(x[1] or 0))

    for voter, pct, total in voter_ranked:
        if pct is None:
            lines.append(f"  · {voter}: N={total} insufficient")
        else:
            mark = "✅" if pct >= 55 and total >= MIN_N_FOR_CALIBRATION else "·"
            lines.append(f"  {mark} {voter}: {pct:.1f}% (N={total})")

    ready = [v for v, p, n in voter_ranked if p is not None and p >= 55 and n >= MIN_N_FOR_CALIBRATION]
    if ready:
        lines.append(f"\n<b>✅ Ready for voter_wire_v2:</b> {', '.join(ready)}")
    else:
        lines.append(f"\n<i>Пока никто не готов для wire-in (нужно N≥15 + precision≥55%).</i>")

    lines.append(f"\n<i>💡 Full HTML report — вложением.</i>")
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured; would send:")
        logger.warning(text[:500])
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"Summary sent · message_id={result.get('result', {}).get('message_id')}")
                return True
            logger.error(f"Telegram error: {result}")
            return False
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def send_telegram_document(file_path, caption=''):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured")
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    # Multipart POST
    import mimetypes
    boundary = f'----WebKitFormBoundary{datetime.now().timestamp():.0f}'
    filename = Path(file_path).name
    with open(file_path, 'rb') as f:
        file_data = f.read()
    body = []
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
    body.append(b'Content-Type: text/html\r\n\r\n')
    body.append(file_data)
    body.append(f'\r\n--{boundary}--\r\n'.encode())
    body_bytes = b''.join(body)
    try:
        req = urllib.request.Request(url, data=body_bytes)
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"Document sent: {filename}")
                return True
            logger.error(f"Telegram doc error: {result}")
            return False
    except Exception as e:
        logger.error(f"Send doc failed: {e}")
        return False


def main():
    logger.info("=" * 60)
    logger.info("WEEKLY BACKTEST · STRK Engine")
    logger.info("=" * 60)

    # Load
    shadow_records = load_jsonl(SHADOW_FILE)
    history_records = load_jsonl(HISTORY_FILE)
    logger.info(f"Loaded: {len(shadow_records)} shadow votes · {len(history_records)} history records")

    if not shadow_records and not history_records:
        logger.warning("No data yet — nothing to backtest")
        send_telegram("<b>📊 Weekly Backtest</b>\n\nNo data yet — need to accumulate at least 5 closed forecasts (~5-7 days).")
        return 0

    # Evaluate
    conf_results = evaluate_confluence(shadow_records)
    comp_results = evaluate_composite_v2(history_records)
    voter_results = evaluate_shadow_voters(shadow_records)

    logger.info(f"Confluence 72h: {conf_results['72h']['hits']}/{conf_results['72h']['total']}")
    logger.info(f"Confluence 7d:  {conf_results['7d']['hits']}/{conf_results['7d']['total']}")
    logger.info(f"Composite 72h:  {comp_results['72h']['hits']}/{comp_results['72h']['total']}")
    logger.info(f"Voters: {len(voter_results)}")
    for voter, wins in voter_results.items():
        logger.info(f"  · {voter}: 72h {wins['72h']['hits']}/{wins['72h']['total']} · 7d {wins['7d']['hits']}/{wins['7d']['total']}")

    # Build reports
    html_report = build_html_report(shadow_records, history_records, conf_results, comp_results, voter_results)
    telegram_summary = build_telegram_summary(shadow_records, history_records, conf_results, comp_results, voter_results)

    # Save HTML
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    html_path = REPORTS_DIR / f'weekly_backtest_{date_str}.html'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_report)
    logger.info(f"HTML saved: {html_path}")

    # Also save as 'latest' for convenient link
    latest_path = REPORTS_DIR / 'weekly_backtest_latest.html'
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(html_report)

    # Send to Telegram
    send_telegram(telegram_summary)
    send_telegram_document(html_path, caption=f"📊 STRK Weekly Backtest · {date_str}")

    logger.info("=" * 60)
    logger.info("Weekly backtest complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())