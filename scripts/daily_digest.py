#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_digest.py — Telegram delivery для STRK Engine (digest / liq / run)

Modes (via env MODE):
  · MODE=digest — краткий heartbeat (по умолчанию, для cron 6h)
  · MODE=liq    — LIQ формат: DECISION + action + MICRO/SWING/FUND, 1 msg
  · MODE=run    — 3 сообщения "RUN · 1/3" .. "3/3" + опциональный HTML

Vocabulary горизонтов (единая по всем сообщениям):
  · MICRO (4-24h)      — шумно, не decision-relevant
  · SWING (3-14d)      — tactical context
  · FUNDAMENTAL (30-90d+) — usage/fees/staking/unlock/thesis health

Action ТОЛЬКО из DECISION (confluence_gate). Все горизонты = context.

Empty/missing cache → NOT_CHECKED (не подделываем 0).

Telegram limit: 4096 chars per message. Truncation если превышаем.

Персистентность: каждый send → data/history/alerts.jsonl (inline log_alert).
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
DECISION_LOG = CACHE_DIR / 'decision_log.json'
WHALE_EVENTS = CACHE_DIR / 'whale_events_state.json'

# ============================================================
# INLINE ALERT LOGGER (no import — гарантированно работает
# независимо от наличия alert_logger.py или sys.path)
# Пишет в data/history/alerts.jsonl одну строку JSON после каждого send.
# Никогда не бросает исключений — не может сломать digest.
# ============================================================
def _log_alert(event_type, text='', sent=True, error_msg='', extra=None):
    try:
        import hashlib
        history_dir = SCRIPT_DIR / 'data' / 'history'
        history_dir.mkdir(parents=True, exist_ok=True)
        alerts_file = history_dir / 'alerts.jsonl'
        now = datetime.now(timezone.utc)
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        # Determine sent status
        if isinstance(sent, dict):
            status = sent.get('status', 'UNKNOWN')
        elif not token or not chat_id:
            status = 'DRY_RUN'
        elif sent:
            status = 'SENT'
        else:
            status = 'FAILED'
        # Build record
        record = {
            'ts': now.isoformat(),
            'event_type': event_type,
            'sent_status': status,
            'error_msg': str(error_msg or ''),
            'chat_id_hash': ('sha256:' + hashlib.sha256(chat_id.encode()).hexdigest()[:16]) if chat_id else '',
            'text_length_chars': len(text or ''),
            'text_sha256': 'sha256:' + hashlib.sha256((text or '').encode('utf-8')).hexdigest()[:16],
            'extra': extra or {},
        }
        with open(alerts_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception:
        pass  # never raise from log function


# ============================================================
# HELPERS
# ============================================================
TELEGRAM_MAX = 4096
NOT_CHECKED = 'NOT_CHECKED'

# ============================================================
# CHECK TRIGGERS · human-readable trigger conditions per check
# Used in WHAT TO DO NOW block — тебе понятно что должно случиться
# ============================================================
CHECK_TRIGGERS = {
    # RALLY checks (что должно быть true для LONG signal)
    'on_chain_ok':                    'Wyckoff phase не в DISTRIBUTION',
    'price_up_3d':                    'Цена выросла ≥ +3% за 3 дня',
    'accelerating':                   'Momentum ускоряется (slope 7d > slope 30d)',
    'vol_expanding':                  'Объём ≥ 1.5× среднего за 30d',
    'not_distributing_cex':           'CEX flow не MILD_DISTRIBUTION/STRONG_DISTRIBUTION',
    'events_supportive':              'Event layer SLIGHT_BULLISH или POSITIVE_CATALYST',
    'liquidity_shift_bullish':        'LP inflow — Ekubo/Endur staking accumulation',
    'bridge_inflow_bullish':          'Bridge net INFLOW к Starknet',
    'strk_outperforming_l2_sector':   'STRK alpha vs L2 (ARB/OP/MATIC) ≥ +5% за 7d',
    'post_capitulation_squeeze':      'Setup: price -20% от high + shorts crowded',
    'strong_off_chain_bull':          'Event layer = POSITIVE_CATALYST (не просто supportive)',
    'not_extreme_short':              'Funding не в extreme short crowded (ниже -20%)',
    'not_bouncing':                   'Не в fake bounce после selloff',
    # CRASH checks (что должно быть true для SHORT signal)
    'liquidity_shift_bearish':        'LP outflow — Ekubo/Endur staking distribution',
    'bridge_outflow_bearish':         'Bridge net OUTFLOW от Starknet',
    'strk_underperforming_l2_sector': 'STRK alpha vs L2 (ARB/OP/MATIC) ≤ -5% за 7d',
    'strong_off_chain_bear':          'Event layer = NEGATIVE_CATALYST',
    'events_bearish':                 'Event layer BEARISH или NEGATIVE_CATALYST',
    'event_bearish':                  'Event layer BEARISH',
    'event_bullish':                  'Event layer BULLISH',
}


# ============================================================
# LAYMAN VERDICT · signal + confidence → человеческая фраза
# Используется в digest, LIQ, RUN — единый tone of voice
# ============================================================
LAYMAN_VERDICTS = {
    ('RALLY_HIGH', 'HIGH'):
        'Setup для LONG — 6+ независимых сигналов согласны. Проверь LIQ перед входом.',
    ('CRASH_HIGH', 'HIGH'):
        'Setup для SHORT/REDUCE — 6+ независимых сигналов согласны. Проверь LIQ перед действием.',
    ('RALLY_MEDIUM', 'MEDIUM'):
        'Картина не худшая, но входить нельзя — не хватает силы (цена/объём/импульс).',
    ('CRASH_MEDIUM', 'MEDIUM'):
        'Есть тревожные сигналы, но не сетап для SHORT — недостаточно подтверждений.',
    ('NO_SIGNAL', 'LOW'):
        'Нет чёткой картины. Активность рынка низкая, направление размыто.',
    ('NO_SIGNAL', 'MEDIUM'):
        'Смешанные сигналы. Некоторые модули видят движение, но конфликтуют.',
}

# ============================================================
# 3-HORIZON ACTION VERDICT · FUND / SWING / SQZ
# Правила из спеки Xenia (LIQ_v1_3horizon):
#   FUND (30-90d)   — Wyckoff, SMART, News, Unlock, Bridge
#   SWING (3-14d)   — Range, BTC cycle, CEX 7d, Volume, Alpha
#   SQZ (4-24h)     — RSI, CVD, Funding, Slope 3d
# Три независимых вердикта, НЕ смешивать между собой.
# ============================================================
def _compute_action_3horizons(wyckoff, tech, cex, cohorts, unlock, news, btc_ctx, funding, cvd_data):
    """Return dict {fund, swing, sqz} — each with verdict, data, action."""
    # === Extract data ===
    phase = str(wyckoff.get('phase', 'UNKNOWN'))
    news_signal = str(news.get('overall_signal', 'NEUTRAL')).upper()
    unlock_days = ((unlock.get('next_cliff') or {}).get('days_until'))
    btc_cycle = str(btc_ctx.get('cycle', 'UNKNOWN')).upper()
    rsi = tech.get('rsi')
    slope_3d = tech.get('slope_3d_pct')
    vol_ratio = tech.get('vol_ratio_3d_vs_30d')
    high_14d = tech.get('high_14d')
    low_14d = tech.get('low_14d')
    price = tech.get('price') or tech.get('price_now')
    cex_signal = str((cex.get('classification') or {}).get('signal', 'NEUTRAL')).upper()
    fund_apr = ((funding.get('funding_metrics') or {}).get('current_annualized_pct'))

    # SMART cohort net flow
    coh = cohorts.get('cohorts') or {}
    smart = coh.get('SMART') or coh.get('smart') or {}
    smart_net = smart.get('net_flow_strk') or smart.get('net_24h_strk') or 0

    # CVD 1h signal
    cvd_1h = ((cvd_data.get('timeframes') or {}).get('1h') or
              (cvd_data.get('timeframes') or {}).get('1H') or {})
    cvd_signal = str(cvd_1h.get('signal', '')).upper()
    cvd_decelerating = any(k in cvd_signal for k in ['NEUTRAL', 'ACCUMULATION', 'DIVERGENCE_BULL'])

    # === FUND (30-90d) ===
    fund = {'verdict': '', 'data': '', 'action': '', 'emoji': '⚪'}
    is_accumulation = 'ACCUMULATION' in phase.upper()
    is_distribution = 'DISTRIBUTION' in phase.upper()
    smart_positive = smart_net > 0
    news_bullish = 'BULLISH' in news_signal
    unlock_close = unlock_days is not None and unlock_days < 7

    if is_accumulation and smart_positive and news_bullish:
        fund['emoji'] = '🟢'
        fund['verdict'] = 'ЗОНА НАБОРА'
        fund['data'] = f'Phase {phase}, SMART +{smart_net/1e6:.2f}M, news {news_signal}'
        fund['action'] = 'DCA от текущей цены. Разбить бюджет на 3-5 частей.'
    elif is_distribution or (smart_net < 0) or unlock_close:
        fund['emoji'] = '🔴'
        fund['verdict'] = 'НЕ ТРОГАТЬ'
        _r = []
        if is_distribution: _r.append(f'Phase {phase}')
        if smart_net < 0: _r.append(f'SMART {smart_net/1e6:+.2f}M')
        if unlock_close: _r.append(f'Unlock через {unlock_days}d')
        fund['data'] = ', '.join(_r) if _r else 'см. вердикт'
        fund['action'] = 'Ждать разблокировки или разворота SMART cohort.'
    else:
        fund['emoji'] = '🟡'
        fund['verdict'] = 'НЕЙТРАЛЬНО'
        fund['data'] = f'Phase {phase}, SMART {smart_net/1e6:+.2f}M, news {news_signal}'
        fund['action'] = 'Копить кэш. Ждать чёткого phase change или BULLISH news.'

    # === SWING (3-14d) ===
    swing = {'verdict': '', 'data': '', 'action': '', 'emoji': '⚪'}
    # Range position
    range_pos = 'MID'
    if price and high_14d and low_14d and high_14d > low_14d:
        pos = (price - low_14d) / (high_14d - low_14d)
        if pos < 0.25: range_pos = 'LOW'
        elif pos > 0.75: range_pos = 'HIGH'
    btc_up = 'UP' in btc_cycle
    btc_down = 'DOWN' in btc_cycle
    cex_dist = 'DISTRIBUTION' in cex_signal
    vol_high = vol_ratio and vol_ratio > 1.0
    vol_breakout = vol_ratio and vol_ratio > 1.5

    if btc_up and range_pos == 'LOW' and vol_high:
        swing['emoji'] = '🟢'
        swing['verdict'] = 'ЛОНГ ОТ ПОДДЕРЖКИ'
        swing['data'] = f'BTC {btc_cycle}, price near low, Vol {vol_ratio:.2f}x'
        _stop = low_14d * 0.985 if low_14d else 0
        _target = (low_14d + (high_14d - low_14d) * 0.5) if (low_14d and high_14d) else 0
        swing['action'] = f'Long от ${low_14d:.4f}, stop ${_stop:.4f}, target ${_target:.4f}'
    elif btc_up and range_pos == 'HIGH' and vol_breakout:
        swing['emoji'] = '🟢'
        swing['verdict'] = 'ВХОД НА ПРОБОЕ'
        swing['data'] = f'BTC {btc_cycle}, testing top, Vol {vol_ratio:.2f}x'
        _stop = high_14d * 0.99 if high_14d else 0
        swing['action'] = f'Long на break > ${high_14d:.4f}, stop ${_stop:.4f}'
    elif btc_down or cex_dist:
        swing['emoji'] = '🟡'
        swing['verdict'] = 'ФЛЭТ'
        _r = []
        if btc_down: _r.append(f'BTC {btc_cycle}')
        if cex_dist: _r.append(f'CEX {cex_signal}')
        swing['data'] = ', '.join(_r)
        _range = f'от ${low_14d:.4f}' if low_14d else '?'
        _up = f'${high_14d:.4f}' if high_14d else '?'
        swing['action'] = f'Ждать пробоя {_up} или отскока {_range} с объёмом.'
    else:
        swing['emoji'] = '⚪'
        swing['verdict'] = 'НЕТ СИГНАЛА'
        swing['data'] = f'BTC {btc_cycle}, range pos {range_pos}, Vol {vol_ratio or "?"}'
        swing['action'] = 'Смешанные условия — не входить.'

    # === SQZ/MICRO (4-24h) ===
    sqz = {'verdict': '', 'data': '', 'action': '', 'emoji': '⚪'}
    rsi_oversold = rsi is not None and rsi < 30
    rsi_overbought = rsi is not None and rsi > 70
    fund_normal = fund_apr is not None and fund_apr < 5
    fund_crowded_long = fund_apr is not None and fund_apr > 20

    if rsi_oversold and cvd_decelerating and fund_normal:
        sqz['emoji'] = '🟢'
        sqz['verdict'] = 'ЛОНГ НА ОТСКОКЕ'
        sqz['data'] = f'RSI {rsi:.0f}, CVD замедляется, funding {fund_apr:.2f}%'
        _stop = price * 0.98 if price else 0
        _take = price * 1.05 if price else 0
        sqz['action'] = f'Long, stop ${_stop:.4f} (-2%), take ${_take:.4f} (+5%)'
    elif rsi_overbought and fund_crowded_long:
        sqz['emoji'] = '🔴'
        sqz['verdict'] = 'КОРОТКИЙ СКВИЗ ВНИЗ'
        sqz['data'] = f'RSI {rsi:.0f} overbought, funding {fund_apr:.2f}% (crowded long)'
        _take = price * 0.97 if price else 0
        sqz['action'] = f'Short, take ${_take:.4f} (-3%), stop над локальным high'
    else:
        sqz['emoji'] = '⚪'
        sqz['verdict'] = 'ШУМ'
        _rsi_str = f'{rsi:.0f}' if rsi is not None else 'NC'
        _fund_str = f'{fund_apr:.2f}%' if fund_apr is not None else 'NC'
        sqz['data'] = f'RSI {_rsi_str}, funding {_fund_str}'
        sqz['action'] = 'Ждать RSI ниже 30 + CVD flip для лонга, или RSI выше 70 + funding выше 20% для шорта.'

    return {'fund': fund, 'swing': swing, 'sqz': sqz}


def _format_3horizon_block(horizons):
    """Format 3-horizon verdict as Telegram-ready block."""
    t = "\n━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>🎯 ЧТО ДЕЛАТЬ СЕЙЧАС (3 горизонта)</b>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n\n"

    t += "<b>🔵 FUND (30-90d) · Инвестор</b>\n"
    t += f"<i>Данные:</i> {horizons['fund']['data']}\n"
    t += f"{horizons['fund']['emoji']} <b>Вердикт:</b> {horizons['fund']['verdict']}\n"
    t += f"→ {horizons['fund']['action']}\n\n"

    t += "<b>🟢 SWING (3-14d) · Трейдер</b>\n"
    t += f"<i>Данные:</i> {horizons['swing']['data']}\n"
    t += f"{horizons['swing']['emoji']} <b>Вердикт:</b> {horizons['swing']['verdict']}\n"
    t += f"→ {horizons['swing']['action']}\n\n"

    t += "<b>🔴 SQZ/MICRO (4-24h) · Скальпер</b>\n"
    t += f"<i>Данные:</i> {horizons['sqz']['data']}\n"
    t += f"{horizons['sqz']['emoji']} <b>Вердикт:</b> {horizons['sqz']['verdict']}\n"
    t += f"→ {horizons['sqz']['action']}\n\n"

    t += "<i>💡 Три независимых горизонта. НЕ смешивать между собой.</i>\n\n"
    return t


def _safe(s):
    """Escape < > в user-generated тексте, чтобы не сломать Telegram HTML parser.
    Использовать ТОЛЬКО для данных из JSON (narrative, story, interpretation).
    НЕ применять к нашим f-strings с <b> <i> тегами — они уже валидные HTML.
    """
    if not isinstance(s, str):
        return s
    return s.replace('<', '&lt;').replace('>', '&gt;')


# ============================================================
# SHADOW VOTERS · читаем последний run из shadow_votes.jsonl
# Показываем: current votes + aggregate + rolling precision (когда наберётся N≥15)
# ============================================================
def _load_latest_shadow_votes():
    """Return dict with current shadow snapshot and per-voter precision.
    Returns None if нет данных.
    """
    path = SCRIPT_DIR / 'data' / 'history' / 'shadow_votes.jsonl'
    if not path.exists():
        return None
    records = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return None
    if not records:
        return None

    # Найти самый свежий run по run_id или issued_at (window=72h — берём его как current)
    latest_72h = None
    latest_ts = None
    for r in records:
        if r.get('window') != '72h':
            continue
        ts = r.get('issued_at', '')
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts
            latest_72h = r

    if not latest_72h:
        # Fallback — берём просто последнюю запись
        latest_72h = records[-1]

    # Precision по voter'ам из CLOSED записей за 30 дней
    from datetime import timedelta
    cutoff = None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    except Exception:
        pass

    voter_stats = {}  # voter_name → {'hits': N, 'total': N}
    for r in records:
        if r.get('status') != 'CLOSED':
            continue
        try:
            r_ts = datetime.fromisoformat(r.get('issued_at', ''))
            if cutoff and r_ts < cutoff:
                continue
        except Exception:
            continue
        per_voter = r.get('per_voter_outcome') or {}
        for voter, verdict in per_voter.items():
            if voter not in voter_stats:
                voter_stats[voter] = {'hits': 0, 'total': 0}
            v = str(verdict).upper()
            if v == 'HIT':
                voter_stats[voter]['hits'] += 1
                voter_stats[voter]['total'] += 1
            elif v == 'MISS':
                voter_stats[voter]['total'] += 1
            # SKIP — не учитываем в total

    return {
        'latest': latest_72h,
        'voter_stats': voter_stats,
        'total_closed': sum(1 for r in records if r.get('status') == 'CLOSED'),
        'total_pending': sum(1 for r in records if r.get('status') == 'PENDING'),
    }


def _format_shadow_voters_block(compact=False):
    """Build shadow voters block for RUN/LIQ/digest.
    compact=True — 4 строки (для LIQ / digest)
    compact=False — полный блок (для RUN MSG1)
    """
    data = _load_latest_shadow_votes()
    if not data or not data.get('latest'):
        if compact:
            return "<b>🔍 Shadow Voters:</b> NOT_CHECKED\n\n"
        return ("━━━━━━━━━━━━━━━━━━━\n"
                "<b>🔍 SHADOW VOTERS</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "<i>Нет данных в shadow_votes.jsonl — ждём accumulate</i>\n\n")

    latest = data['latest']
    votes = latest.get('shadow_votes') or {}
    agg = latest.get('aggregate_shadow') or {}
    stats = data['voter_stats']
    closed = data['total_closed']

    shadow_signal = agg.get('shadow_signal', 'SHADOW_NEUTRAL')
    rally_n = agg.get('rally_votes', 0)
    crash_n = agg.get('crash_votes', 0)
    neutral_n = agg.get('neutral_votes', 0)

    # === COMPACT ===
    if compact:
        # Одной строкой
        _shadow_short = shadow_signal.replace('SHADOW_', '')
        line = f"<b>🔍 Shadow:</b> {_shadow_short} · rally {rally_n} · crash {crash_n} · neutral {neutral_n}"
        line += f" · N_closed={closed}\n\n"
        return line

    # === FULL ===
    text = "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🔍 SHADOW VOTERS</b> <i>(candidates for voter_wire_v2, NOT in DECISION)</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>Aggregate:</b> {shadow_signal}\n"
    text += f"<b>Counts:</b> rally {rally_n} · crash {crash_n} · neutral {neutral_n}\n\n"

    # Per-voter current votes
    text += "<b>Текущие голоса:</b>\n"
    for voter_name, vote_data in votes.items():
        vote = vote_data.get('vote', 'UNKNOWN') if isinstance(vote_data, dict) else str(vote_data)
        value = vote_data.get('value', '?') if isinstance(vote_data, dict) else '?'
        # Emoji per vote type
        emoji = '🟢' if vote == 'RALLY' else ('🔴' if vote == 'CRASH' else '⚪')
        # Precision if есть
        s = stats.get(voter_name, {})
        n = s.get('total', 0)
        if n >= 15:
            pct = s['hits'] / n * 100
            prec_str = f" · <b>{pct:.0f}%</b> (N={n})"
        elif n > 0:
            prec_str = f" · N={n}&lt;15"
        else:
            prec_str = ""
        text += f"  {emoji} <code>{voter_name}</code>: {vote} ({_safe(str(value))}){prec_str}\n"

    text += f"\n<i>N closed forecasts: {closed} · Wire-in требует N≥15 + precision≥55%</i>\n"
    text += "<i>💡 Voters ТОЛЬКО shadow — не влияют на current DECISION.</i>\n\n"
    return text


def _load_dune_starknet():
    """Load daily + weekly + monthly + cex_flow Dune data.
    Возвращает normalized структуру с columns lookup для robust parsing."""
    daily_path = SCRIPT_DIR / 'data' / 'cache' / 'dune_starknet.json'
    weekly_path = SCRIPT_DIR / 'data' / 'cache' / 'dune_starknet_weekly.json'
    monthly_path = SCRIPT_DIR / 'data' / 'cache' / 'dune_starknet_monthly.json'
    cex_flow_path = SCRIPT_DIR / 'data' / 'cache' / 'dune_cex_flow.json'
    out = {'daily': None, 'weekly': None, 'monthly': None, 'cex_flow': None,
           'daily_cols': [], 'weekly_cols': [], 'monthly_cols': [], 'cex_flow_cols': [],
           'age_h': None}

    for key, path in [('daily', daily_path), ('weekly', weekly_path),
                      ('monthly', monthly_path), ('cex_flow', cex_flow_path)]:
        if not path.exists():
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            rows = data.get('rows', [])
            columns = data.get('columns', [])
            if not rows:
                continue
            out[key] = rows
            out[f'{key}_cols'] = columns
            if key == 'daily':
                try:
                    ts = datetime.fromisoformat(data.get('collected_at', ''))
                    out['age_h'] = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                except Exception:
                    pass
        except Exception:
            continue
    return out


def _get_col(row, cols, name, positional_idx=None, default=None):
    """Robust cell access — работает с dict rows и list rows.
    Prefer column name lookup, fall back to positional index."""
    if row is None:
        return default
    # Dict row (named columns)
    if isinstance(row, dict):
        val = row.get(name)
        return val if val is not None else default
    # List row (positional)
    if isinstance(row, list):
        # Try column names first
        if cols and name in cols:
            try:
                idx = cols.index(name)
                if idx < len(row):
                    return row[idx] if row[idx] is not None else default
            except (ValueError, IndexError):
                pass
        # Fall back to positional index if provided
        if positional_idx is not None and positional_idx < len(row):
            return row[positional_idx] if row[positional_idx] is not None else default
    return default


def _format_dune_starknet_block(compact=False):
    """STARKNET NETWORK block из Dune.
    compact=True — 3-4 строки для LIQ.
    compact=False — full детальный для digest / RUN.
    """
    data = _load_dune_starknet()
    if not data['daily'] and not data['weekly'] and not data.get('monthly'):
        return ""

    def _r(row, idx):
        if isinstance(row, list) and len(row) > idx:
            return row[idx]
        return None

    # === COMPACT === (для LIQ)
    if compact:
        text = "<b>🌐 STARKNET (Dune):</b>\n"
        if data['daily'] and len(data['daily']) >= 7:
            rows = data['daily']
            cols = data.get('daily_cols', [])
            latest_txs = _get_col(rows[0], cols, 'total_txs', 1, 0) or 0
            wow_txs = _get_col(rows[6], cols, 'total_txs', 1, 1) or 1
            txs_wow = (latest_txs / wow_txs - 1) * 100 if wow_txs else 0
            latest_new = _get_col(rows[0], cols, 'new_accounts', 6, 0) or 0
            wow_new = _get_col(rows[6], cols, 'new_accounts', 6, 1) or 1
            new_wow = (latest_new / wow_new - 1) * 100 if wow_new else 0
            _warn_a = ' ⚠' if txs_wow <= -20 else ''
            _warn_n = ' ⚠' if new_wow <= -20 else ''
            text += f"  Activity: <code>{int(latest_txs)/1000:.0f}K</code>/day · WoW <code>{txs_wow:+.0f}%</code>{_warn_a}\n"
            text += f"  Adoption: <code>{int(latest_new)}</code>/day · WoW <code>{new_wow:+.0f}%</code>{_warn_n}\n"
        if data.get('monthly') and len(data['monthly']) > 0:
            m_rows = data['monthly']
            m_cols = data.get('monthly_cols', [])
            latest_m = m_rows[0]
            # Support both v1 (signal, pct_from_30d_max) и v2 (phase_signal, w_m_pct)
            m_signal = (_get_col(latest_m, m_cols, 'phase_signal', None) or
                        _get_col(latest_m, m_cols, 'signal', 6, 'UNKNOWN') or 'UNKNOWN')
            m_pct = _get_col(latest_m, m_cols, 'w_m_pct', None)
            if m_pct is None:
                m_pct = _get_col(latest_m, m_cols, 'pct_from_30d_max', 5, 0)
            try:
                m_pct = float(m_pct) if m_pct is not None else 0
            except Exception:
                m_pct = 0

            def _lsig(r):
                return (_get_col(r, m_cols, 'phase_signal', None) or
                        _get_col(r, m_cols, 'signal', 6))
            bearish_30d = sum(1 for r in m_rows[:30] if _lsig(r) == 'BEARISH_BREAKDOWN')
            text += f"  Monthly: <code>{_safe(str(m_signal))}</code> ({bearish_30d}/30d bearish, trend {m_pct:+.0f}%)\n"
        text += "\n"
        return text

    # === FULL === (existing detailed rendering)
    text = "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🌐 STARKNET NETWORK</b> <i>(7d, Dune)</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"

    # DAILY — network activity
    if data['daily']:
        rows = data['daily']
        # Rows отсортированы DESC (сегодня первый). Берём:
        latest = rows[0] if rows else {}
        # Row может быть list (positional) или dict (named)
        def _row_val(row, key, idx):
            if isinstance(row, dict):
                return row.get(key)
            elif isinstance(row, list) and len(row) > idx:
                return row[idx]
            return None

        # Columns: day, total_txs, active_senders, avg_fee_wei, total_fees_wei, invokes, new_accounts, l1_messages
        latest_txs = _row_val(latest, 'total_txs', 1) or 0
        latest_users = _row_val(latest, 'active_senders', 2) or 0
        latest_new = _row_val(latest, 'new_accounts', 6) or 0
        latest_l1 = _row_val(latest, 'l1_messages', 7) or 0
        latest_fee = _row_val(latest, 'avg_fee_wei', 3) or 0

        # WoW = latest vs 7 days ago (last row) — если есть 7d
        if len(rows) >= 7:
            wow_row = rows[6]
            wow_txs = _row_val(wow_row, 'total_txs', 1) or 1
            wow_users = _row_val(wow_row, 'active_senders', 2) or 1
            wow_new = _row_val(wow_row, 'new_accounts', 6) or 1

            txs_wow_pct = (latest_txs / wow_txs - 1) * 100 if wow_txs else 0
            users_wow_pct = (latest_users / wow_users - 1) * 100 if wow_users else 0
            new_wow_pct = (latest_new / wow_new - 1) * 100 if wow_new else 0
        else:
            txs_wow_pct = users_wow_pct = new_wow_pct = None

        # avg fee — из wei (fri в v3+) → STRK (10^18 fri = 1 STRK)
        try:
            fee_strk = float(latest_fee) / 1e18
        except Exception:
            fee_strk = 0

        def _wow_str(pct):
            if pct is None:
                return ''
            arrow = '↑' if pct > 0 else ('↓' if pct < 0 else '=')
            warn = ' ⚠' if pct <= -20 else ''
            return f' · WoW {arrow}{pct:+.0f}%{warn}'

        text += f"Activity:      <code>{int(latest_txs)/1000:.0f}K</code> txs/day{_wow_str(txs_wow_pct)}\n"
        text += f"Active users:  <code>{int(latest_users):,}</code>/day{_wow_str(users_wow_pct)}\n"
        text += f"New accounts:  <code>{int(latest_new)}</code>/day{_wow_str(new_wow_pct)}\n"
        text += f"StarkGate L1:  <code>{int(latest_l1)}</code> msg/day\n"
        text += f"Avg fee:       <code>{fee_strk:.3f}</code> STRK\n\n"

    # WEEKLY — STRK transfers
    if data['weekly']:
        rows = data['weekly']
        latest = rows[0] if rows else {}
        def _row_val_w(row, key, idx):
            if isinstance(row, dict):
                return row.get(key)
            elif isinstance(row, list) and len(row) > idx:
                return row[idx]
            return None

        w_transfers = _row_val_w(latest, 'transfers', 1) or 0
        w_total_vol = _row_val_w(latest, 'total_volume', 4) or 0
        w_median = _row_val_w(latest, 'median', 6) or 0
        w_max = _row_val_w(latest, 'max_amount', 7) or 0

        text += "<b>STRK transfers:</b>\n"
        try:
            text += f"  Volume:      <code>{float(w_total_vol)/1e6:.0f}M</code> STRK/day\n"
            text += f"  Median:      <code>{float(w_median):.2f}</code> STRK <i>(retail)</i>\n"
            text += f"  Max single:  <code>{float(w_max)/1e6:.1f}M</code> STRK <i>(whale)</i>\n"
        except Exception:
            pass
        text += "\n"

    # MONTHLY view — structural signal (embedded SQL classification)
    if data['monthly']:
        rows = data['monthly']
        cols = data.get('monthly_cols', [])
        latest_m = rows[0] if rows else []

        # Columns support two SQL versions:
        # v1: signal, pct_from_30d_max, verdict
        # v2: phase_signal, swing_trading_signal, d_w_pct, w_m_pct
        # _get_col will find whichever exists.
        def _monthly_signal(row):
            return (_get_col(row, cols, 'phase_signal', None) or
                    _get_col(row, cols, 'signal', 6) or 'UNKNOWN')

        def _monthly_verdict(row):
            return (_get_col(row, cols, 'swing_trading_signal', None) or
                    _get_col(row, cols, 'verdict', 9) or '')

        def _monthly_pct(row):
            # w_m_pct = weekly vs monthly (structural), predpochtitelno
            # d_w_pct = daily vs weekly (краткосрочный шум)
            v = (_get_col(row, cols, 'w_m_pct', None) or
                 _get_col(row, cols, 'pct_from_30d_max', 5))
            if v is None:
                return 0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0

        m_signal = _monthly_signal(latest_m)
        m_verdict = _monthly_verdict(latest_m)
        m_pct_from_max = _monthly_pct(latest_m)

        # Streak
        streak = 1
        for i in range(1, min(len(rows), 30)):
            if _monthly_signal(rows[i]) == m_signal:
                streak += 1
            else:
                break

        text += "<b>Monthly view</b> <i>(SQL-classified)</i>:\n"
        if m_signal == 'UNKNOWN':
            text += f"  <i>⚠ Signal UNKNOWN. Columns: {_safe(str(cols)[:120])}</i>\n"
            if latest_m:
                row_type = 'dict' if isinstance(latest_m, dict) else ('list' if isinstance(latest_m, list) else 'other')
                text += f"  <i>Row[0] ({row_type}): {_safe(str(latest_m)[:200])}</i>\n"
        else:
            text += f"  Signal:  <code>{_safe(str(m_signal))}</code> ({streak}d streak)\n"
            if m_verdict:
                text += f"  Verdict: {_safe(str(m_verdict))}\n"
            try:
                text += f"  Trend: <code>{m_pct_from_max:+.1f}%</code>\n"
            except Exception:
                pass
        text += "\n"

    # CEX FLOW ETH-side (Dune) — ERC-20 STRK wrapper
    if data.get('cex_flow'):
        rows = data['cex_flow']
        cols = data.get('cex_flow_cols', [])
        # NB: rows часто DESC от Dune — берём последние 7 (не в конце, а первые)
        recent = rows[:7] if len(rows) >= 7 else rows
        try:
            total_in = sum(float(_get_col(r, cols, 'inflow_strk', 1, 0) or 0) for r in recent)
            total_out = sum(float(_get_col(r, cols, 'outflow_strk', 2, 0) or 0) for r in recent)
            total_net = sum(float(_get_col(r, cols, 'net_flow_strk', 3, 0) or 0) for r in recent)

            text += "<b>CEX flow ETH-side (Dune):</b>\n"
            # DEBUG fallback: если все нули — покажем сырую диагностику
            if total_in == 0 and total_out == 0:
                text += f"  <i>⚠ Все нули. Columns: {_safe(str(cols)[:120])}</i>\n"
                if rows:
                    first_row = rows[0]
                    row_type = 'dict' if isinstance(first_row, dict) else ('list' if isinstance(first_row, list) else 'other')
                    text += f"  <i>Row[0] ({row_type}): {_safe(str(first_row)[:200])}</i>\n"
            else:
                text += f"  7d inflow:   <code>{total_in/1e6:+.1f}M</code> STRK\n"
                text += f"  7d outflow:  <code>{total_out/1e6:+.1f}M</code> STRK\n"
                text += f"  7d net:      <code>{total_net/1e6:+.2f}M</code> STRK "
                if total_net < -1_000_000:
                    text += "<i>(accumulation)</i>\n"
                elif total_net > 1_000_000:
                    text += "<i>(distribution)</i>\n"
                else:
                    text += "<i>(neutral)</i>\n"
            text += "\n"
        except Exception as e:
            text += f"<i>CEX flow parse error: {_safe(str(e))}</i>\n\n"

    # Impact hint для FUND horizon
    if data['daily']:
        rows = data['daily']
        if len(rows) >= 7:
            def _row_val_s(row, key, idx):
                if isinstance(row, dict):
                    return row.get(key)
                elif isinstance(row, list) and len(row) > idx:
                    return row[idx]
                return None
            latest_new = _row_val_s(rows[0], 'new_accounts', 6) or 1
            wow_new = _row_val_s(rows[6], 'new_accounts', 6) or 1
            new_wow = latest_new / wow_new - 1 if wow_new else 0

            if new_wow <= -0.30:
                text += "<i>⚠ Ecosystem cooling — adoption -30%+ WoW. FUND bearish context.</i>\n"
            elif new_wow >= 0.15:
                text += "<i>✓ Ecosystem growing — adoption +15%+ WoW. FUND bullish context.</i>\n"
            else:
                text += "<i>Ecosystem neutral — стабильный adoption.</i>\n"

    text += "\n"
    return text


def _compute_current_phase(wyckoff, technical, dune_data, squeeze_state):
    """Synthesize current market phase из wyckoff + Dune monthly + squeeze + technical.
    Returns dict: {phase, emoji, confidence, description, guidance, evidence}"""

    wyckoff_phase = str(wyckoff.get('phase', 'UNKNOWN')).upper()
    tech_features = technical.get('features') or {}
    rsi = tech_features.get('rsi')

    # === Parse Dune monthly ===
    dune_signal = None
    dune_streak = 0
    dune_pct_from_peak = 0
    prev_bearish_streak = 0
    bearish_days_30d = 0

    monthly = dune_data.get('monthly') if dune_data else None
    monthly_cols = dune_data.get('monthly_cols', []) if dune_data else []
    if monthly and len(monthly) > 0:
        def _v(row, name, idx):
            """Read cell — works with dict rows OR list rows."""
            if row is None:
                return None
            if isinstance(row, dict):
                return row.get(name)
            if isinstance(row, list):
                if monthly_cols and name in monthly_cols:
                    try:
                        i = monthly_cols.index(name)
                        if i < len(row):
                            return row[i]
                    except (ValueError, IndexError):
                        pass
                if idx is not None and idx < len(row):
                    return row[idx]
            return None

        def _sig(row):
            """Get signal — supports both 'phase_signal' (v2) and 'signal' (v1)."""
            return _v(row, 'phase_signal', None) or _v(row, 'signal', 6) or 'UNKNOWN'

        def _pct(row):
            v = _v(row, 'w_m_pct', None) or _v(row, 'pct_from_30d_max', 5)
            if v is None:
                return 0
            try:
                return float(v)
            except (ValueError, TypeError):
                return 0

        latest = monthly[0]
        dune_signal = _sig(latest)
        dune_pct_from_peak = _pct(latest)

        # Current streak
        dune_streak = 1
        for i in range(1, min(len(monthly), 30)):
            if _sig(monthly[i]) == dune_signal:
                dune_streak += 1
            else:
                break

        # Previous streak
        if dune_streak < len(monthly):
            prev_sig = _sig(monthly[dune_streak])
            if prev_sig and prev_sig != dune_signal:
                for i in range(dune_streak, min(len(monthly), 30)):
                    if _sig(monthly[i]) == prev_sig:
                        prev_bearish_streak += 1
                    else:
                        break

        # Total bearish days in last 30 rows
        for i in range(min(len(monthly), 30)):
            if _sig(monthly[i]) == 'BEARISH_BREAKDOWN':
                bearish_days_30d += 1

    # === Squeeze active? ===
    squeeze_level = squeeze_state.get('level', 'INACTIVE') if squeeze_state else 'INACTIVE'

    evidence_parts = []
    if wyckoff_phase != 'UNKNOWN':
        evidence_parts.append(f'Wyckoff: {wyckoff_phase}')
    if dune_signal:
        evidence_parts.append(f'Dune: {dune_signal} ({dune_streak}d)')
    if bearish_days_30d > 0:
        evidence_parts.append(f'{bearish_days_30d}/30d bearish')
    if squeeze_level != 'INACTIVE':
        evidence_parts.append(f'Squeeze: {squeeze_level}')

    # === Phase classification (в порядке приоритета) ===

    # 1. INFLECTION POINT — structural bear (12+/30d) ослабевает
    if (dune_signal in ('NEUTRAL_CONSOLIDATION', 'MIXED_SIGNAL') and
            dune_streak <= 5 and bearish_days_30d >= 12):
        conf_level = 'MEDIUM' if bearish_days_30d >= 20 else 'LOW'
        return {
            'phase': 'INFLECTION_POINT',
            'emoji': '🟡',
            'confidence': conf_level,
            'description': f'Structural bear ({bearish_days_30d}/30d) → {dune_streak}d neutral',
            'guidance': 'Bear phase может заканчиваться. Ждать 5-7 дней подтверждения. Слишком рано входить.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 2. STRUCTURAL BEAR — активный BEARISH_BREAKDOWN
    if dune_signal == 'BEARISH_BREAKDOWN' and (dune_streak >= 5 or bearish_days_30d >= 15):
        return {
            'phase': 'STRUCTURAL_BEAR',
            'emoji': '🔴',
            'confidence': 'HIGH',
            'description': f'{dune_streak}d bearish streak · {bearish_days_30d}/30d bearish · {dune_pct_from_peak:+.0f}% от peak',
            'guidance': 'Ecosystem cooling. Не накапливать. Ждать capitulation.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 3. SQUEEZE SETUP — technical bounce, не изменение фазы
    if squeeze_level in ('ACTIVE', 'STRONG'):
        rsi_str = f'RSI {rsi:.0f}' if rsi is not None else ''
        # Если внутри structural bear — предупредить что squeeze temporary
        bear_context = f' · но {bearish_days_30d}/30d bearish' if bearish_days_30d >= 15 else ''
        return {
            'phase': 'SQUEEZE_SETUP',
            'emoji': '🟢',
            'confidence': 'MEDIUM' if squeeze_level == 'STRONG' else 'LOW',
            'description': f'Squeeze {squeeze_level} · {rsi_str}{bear_context}',
            'guidance': 'Технический setup для 4-24h bounce. НЕ путать с изменением phase.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 4. WYCKOFF DISTRIBUTION
    if wyckoff_phase in ('DISTRIBUTION', 'DISTRIBUTION_ACTIVE'):
        return {
            'phase': 'DISTRIBUTION',
            'emoji': '🔴',
            'confidence': 'HIGH',
            'description': 'Wyckoff distribution phase',
            'guidance': 'Selling pressure. Не покупать. Scale-out если в лонге.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 5. EARLY ACCUMULATION
    if (wyckoff_phase in ('ACCUMULATION', 'ACCUMULATION_BASE') and
            dune_signal in ('NEUTRAL_CONSOLIDATION', 'MIXED_SIGNAL') and
            bearish_days_30d < 10):
        return {
            'phase': 'EARLY_ACCUMULATION',
            'emoji': '🟢',
            'confidence': 'MEDIUM',
            'description': 'Wyckoff acc + Dune neutral · low bear pressure',
            'guidance': 'Правильная фаза для scaled entries. FUND horizon подходящий.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 6. MARKUP
    if wyckoff_phase in ('MARKUP', 'MARKUP_TREND'):
        return {
            'phase': 'MARKUP',
            'emoji': '🟢',
            'confidence': 'HIGH',
            'description': 'Wyckoff markup uptrend',
            'guidance': 'Confirmed uptrend. Держать longs.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 7. BEAR CONSOLIDATION — consolidation но structural bear
    if wyckoff_phase == 'CONSOLIDATION' and bearish_days_30d >= 12:
        return {
            'phase': 'BEAR_CONSOLIDATION',
            'emoji': '🔴',
            'confidence': 'MEDIUM',
            'description': f'Wyckoff consolidation, но {bearish_days_30d}/30d bearish · {dune_pct_from_peak:+.0f}% от peak',
            'guidance': 'Consolidation внутри bear phase, не accumulation. Ждать structural signal.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 8. RANGE CONSOLIDATION
    if wyckoff_phase == 'CONSOLIDATION':
        return {
            'phase': 'RANGE_CONSOLIDATION',
            'emoji': '🟡',
            'confidence': 'MEDIUM',
            'description': f'Wyckoff consolidation · Dune {dune_signal or "unknown"}',
            'guidance': 'Range trading conditions. Ждать breakout with volume.',
            'evidence': ' · '.join(evidence_parts),
        }

    # 9. Fallback
    return {
        'phase': 'TRANSITIONAL',
        'emoji': '⚪',
        'confidence': 'LOW',
        'description': f'Wyckoff {wyckoff_phase} · Dune {dune_signal or "unknown"}',
        'guidance': 'Signals неоднозначны. Wait for clarity.',
        'evidence': ' · '.join(evidence_parts),
    }


def _format_current_phase_block(wyckoff, technical, dune_data, squeeze_state):
    """Синтетический CURRENT PHASE block — читается первым после header."""
    phase_info = _compute_current_phase(wyckoff, technical, dune_data, squeeze_state)

    text = "━━━━━━━━━━━━━━━━━━━\n"
    text += f"{phase_info['emoji']} <b>CURRENT PHASE · {phase_info['phase']}</b> <i>({phase_info['confidence']})</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>State:</b> {_safe(phase_info['description'])}\n"
    if phase_info.get('evidence'):
        text += f"<b>Evidence:</b> <code>{_safe(phase_info['evidence'])}</code>\n"
    text += f"<i>💡 {_safe(phase_info['guidance'])}</i>\n\n"
    return text


def _get_layman_verdict(signal, confidence):
    """Return human-readable verdict for DECISION signal + confidence.
    Fallback — generic based on confidence level."""
    key = (str(signal), str(confidence))
    if key in LAYMAN_VERDICTS:
        return LAYMAN_VERDICTS[key]
    # Fallback by confidence
    conf = str(confidence).upper()
    if conf == 'HIGH' and 'RALLY' in str(signal):
        return 'Сильный rally сигнал, но требует проверки LIQ.'
    if conf == 'HIGH' and 'CRASH' in str(signal):
        return 'Сильный crash сигнал, но требует проверки LIQ.'
    if conf == 'MEDIUM':
        return 'Партиальные сигналы, ждём подтверждения одной из сторон.'
    return 'Недостаточно данных для чёткого вердикта — мониторим.'


def nv(value, default=NOT_CHECKED, formatter=None):
    """Safe value or NOT_CHECKED. Never returns fake 0 for missing data."""
    if value is None:
        return default
    if isinstance(value, (dict, list)) and not value:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        if formatter:
            return formatter(value)
    except Exception:
        return default
    return value

def truncate(text, max_len=TELEGRAM_MAX):
    """Truncate text to Telegram limit, preserving HTML if possible."""
    if len(text) <= max_len:
        return text
    # Обрезаем до max_len - 100 и добавляем предупреждение
    truncated = text[:max_len - 60]
    # Не обрываем в середине HTML тега
    last_lt = truncated.rfind('<')
    last_gt = truncated.rfind('>')
    if last_lt > last_gt:
        truncated = truncated[:last_lt]
    return truncated + '\n\n<i>...truncated (limit 4096)</i>'


def split_digest_into_parts(text, max_len=TELEGRAM_MAX):
    """Split digest into multiple Telegram messages at logical block boundaries.
    Разбивает по '━━━' границам блоков, чтобы каждая часть <= max_len.
    Возвращает list of strings.

    Стратегия:
      - Если text <= max_len → вернуть [text] (одно сообщение)
      - Иначе — разбить по границам блоков (пустые строки \\n\\n)
      - Добавить индикатор страницы 'Часть N/M' в начало каждой части (кроме одной)
    """
    if len(text) <= max_len:
        return [text]

    # Порог: max_len - 60 (место для индикатора страницы)
    limit = max_len - 60
    parts = []
    current = ''

    # Разбиваем по двойному переводу строки (границы блоков)
    blocks = text.split('\n\n')

    for block in blocks:
        block_with_sep = block + '\n\n'
        # Если этот блок сам по себе больше limit — вынужденная truncate
        if len(block_with_sep) > limit:
            # Финализируем предыдущий part
            if current:
                parts.append(current.rstrip())
                current = ''
            # Truncate этот блок и вынести в отдельный part
            parts.append(truncate(block_with_sep, limit))
            continue

        # Если добавление превысит limit — финализируем current
        if len(current) + len(block_with_sep) > limit:
            parts.append(current.rstrip())
            current = block_with_sep
        else:
            current += block_with_sep

    if current:
        parts.append(current.rstrip())

    # Добавить индикатор страницы к каждому part
    total = len(parts)
    if total > 1:
        for i, p in enumerate(parts):
            if i == 0:
                # Первое сообщение — footer
                parts[i] = p + f'\n\n<i>▶ Часть 1/{total} · продолжение ниже</i>'
            elif i == total - 1:
                # Последнее — header
                parts[i] = f'<i>◀ Часть {i+1}/{total} · окончание</i>\n\n' + p
            else:
                # Middle
                parts[i] = f'<i>Часть {i+1}/{total}</i>\n\n' + p

    return parts



logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('digest')


def load_json(name):
    path = CACHE_DIR / name
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except:
            return None
    return None


def get_recent_decisions(hours_back=6):
    if not DECISION_LOG.exists():
        return [], [], []
    try:
        data = json.loads(DECISION_LOG.read_text(encoding='utf-8'))
        decisions = data.get('decisions', [])
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
        recent = [d for d in decisions if d.get('timestamp', '') > cutoff]
        accepted = [d for d in recent if d.get('action') == 'ACCEPTED']
        rejected = [d for d in recent if d.get('action') == 'REJECTED']
        queued = [d for d in recent if d.get('action') == 'QUEUED_FOR_REVIEW']
        return accepted, rejected, queued
    except:
        return [], [], []


def get_whale_events_24h():
    if not WHALE_EVENTS.exists():
        return 0, 0
    try:
        data = json.loads(WHALE_EVENTS.read_text(encoding='utf-8'))
        events = data.get('recent_events', [])
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
        recent = [e for e in events if e.get('timestamp', 0) > cutoff]
        count = len(recent)
        total_strk = sum(e.get('amount_strk', 0) for e in recent)
        return count, total_strk
    except:
        return 0, 0


def count_wallets():
    """Safe wallet count — не падает если wallet_registry недоступен."""
    try:
        from wallet_registry import load_registry
        wallets = load_registry()
    except (ImportError, Exception):
        # Fallback 1: try load_seeds
        try:
            from wallet_registry import load_seeds
            seeds = load_seeds()
            wallets = {}
            for cat, entries in seeds.items():
                if cat.startswith('_'):
                    continue
                if isinstance(entries, dict):
                    for name, info in entries.items():
                        if isinstance(info, dict):
                            wallets[name] = {'category': cat}
        except Exception:
            # Fallback 2: read flow_seeds.json напрямую
            try:
                seeds_file = SCRIPT_DIR / 'data' / 'seeds' / 'flow_seeds.json'
                if seeds_file.exists():
                    import json as _json
                    data = _json.loads(seeds_file.read_text(encoding='utf-8'))
                    wallets = {}
                    for cat, entries in data.items():
                        if cat.startswith('_'):
                            continue
                        if isinstance(entries, dict):
                            for name, info in entries.items():
                                if isinstance(info, dict):
                                    wallets[name] = {'category': cat}
                else:
                    wallets = {}
            except Exception:
                wallets = {}
    total = len(wallets)
    by_cat = {}
    for w in wallets.values():
        cat = w.get('category', 'unknown') if isinstance(w, dict) else 'unknown'
        by_cat[cat] = by_cat.get(cat, 0) + 1
    return total, by_cat


PHASE_EMOJI = {
    'ACCUMULATION': '🟢',
    'MARKUP': '📈',
    'DISTRIBUTION': '🟡',
    'MARKDOWN': '📉',
    'UNKNOWN': '⚪'
}

CONFIDENCE_EMOJI = {
    'HIGH': '🎯',
    'MEDIUM': '🟠',
    'LOW': '⚪',
    'UNKNOWN': '❓'
}


def format_digest():
    """Format the digest text"""
    now = datetime.now(timezone.utc)
    
    # Load Wyckoff data (primary)
    wyckoff = load_json('wyckoff_phase.json')
    composite = load_json('composite_signal_v2.json')
    confluence = load_json('confluence_gate.json')
    technical = load_json('technical_momentum.json')
    
    # Recent activity
    accepted, rejected, queued = get_recent_decisions(hours_back=6)
    whale_count, whale_amt = get_whale_events_24h()
    wallet_total, wallet_by_cat = count_wallets()
    
    if not wyckoff:
        return format_fallback_digest(composite, accepted, rejected, whale_count, wallet_total)
    
    phase = wyckoff['phase']
    sub_phase = wyckoff.get('sub_phase', '')
    confidence = wyckoff['confidence']
    tech = wyckoff.get('technical', {})
    layman = wyckoff.get('layman_explanation', '')
    
    price = tech.get('price_now', 0)
    
    # === HEADER ===
    text = f"<b>🤖 STRK-GUARD · Phase Analysis</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
    text += "<i>⚠ Любой HIGH signal → открой /liq для полного контекста, а не мгновенное buy/sell.</i>\n\n"

    # === CURRENT PHASE === synthesis: wyckoff + Dune monthly + squeeze
    # ЧИТАЕТСЯ ПЕРВЫМ — даёт structural context перед всеми actions.
    _tech_phase = load_json('technical_momentum.json') or {}
    _squeeze_state = load_json('squeeze_state.json') or {}
    _dune_phase = _load_dune_starknet()
    text += _format_current_phase_block(wyckoff or {}, _tech_phase, _dune_phase, _squeeze_state)

    # === 3-HORIZON ACTION VERDICT (ПЕРВЫЙ БЛОК — самое главное) ===
    _wyk_h = wyckoff or {}
    _tech_h = ((load_json('technical_momentum.json') or {}).get('features') or {})
    _cex_h = load_json('cex_flow.json') or {}
    _coh_h = load_json('cohort_tracker.json') or {}
    _unlock_h = load_json('unlock_signal.json') or {}
    _news_h = load_json('news_aggregator.json') or {}
    _cvd_h = load_json('cvd_analysis.json') or {}
    _fund_h = load_json('funding_signal.json') or {}
    _comp_h = load_json('composite_signal_v2.json') or {}
    _macro_h = load_json('agent_input.json') or {}
    _btc_h = _get_btc_context(_comp_h, _macro_h)
    _horizons = _compute_action_3horizons(_wyk_h, _tech_h, _cex_h, _coh_h,
                                           _unlock_h, _news_h, _btc_h, _fund_h, _cvd_h)
    text += _format_3horizon_block(_horizons)

    # SHADOW voters (compact for digest)
    text += _format_shadow_voters_block(compact=True)

    # === DECISION (single source of truth) — signal + action одной секцией ===
    if confluence:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🎯 DECISION</b> <i>(single source of truth)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        signal = confluence.get('signal', 'NO_SIGNAL')
        conf = confluence.get('confidence', 'LOW')
        summary = confluence.get('summary', '')
        action = confluence.get('action', 'STAY FLAT')
        
        # Одна строка — signal + action (чтобы не спорить с INTERPRETATION ниже)
        text += f"<b>{signal}</b> · <b>{action}</b> · <i>{conf}</i>\n\n"
        
        # Signal emoji
        if 'RALLY' in signal and 'HIGH' in conf:
            emoji = '🟢🟢'
        elif 'RALLY' in signal:
            emoji = '🟢'
        elif 'CRASH' in signal and 'HIGH' in conf:
            emoji = '🔴🔴'
        elif 'CRASH' in signal:
            emoji = '🔴'
        elif 'PARTIAL' in signal:
            emoji = '🟡'
        else:
            emoji = '⚪'
        
        text += f"{emoji} <b>{signal}</b>\n"
        text += f"Confidence: <b>{conf}</b>\n"
        text += f"<i>{summary}</i>\n\n"
        
        # Show checks passed
        rally = confluence.get('rally_score', 0)
        crash = confluence.get('crash_score', 0)
        total = confluence.get('total_checks', 5)
        
        text += f"<b>Rally checks:</b> {rally}/{total}\n"
        text += f"<b>Crash checks:</b> {crash}/{total}\n\n"
        
        # If HIGH signal, show which checks passed
        rally_checks = confluence.get('rally_checks_passed', [])
        crash_checks = confluence.get('crash_checks_passed', [])
        
        if rally_checks and 'RALLY' in signal:
            text += f"<b>✅ Passed (rally):</b>\n"
            for c in rally_checks:
                text += f"  · {c.replace('_', ' ')}\n"
            text += "\n"
        
        if crash_checks and 'CRASH' in signal:
            text += f"<b>✅ Passed (crash):</b>\n"
            for c in crash_checks:
                text += f"  · {c.replace('_', ' ')}\n"
            text += "\n"
    
    # === INTERPRETATION LAYER (narrative · action см. DECISION) ===
    interpretation = load_json('interpretation.json')
    if interpretation and interpretation.get('interpretation', {}).get('primary'):
        interp = interpretation.get('interpretation', {})
        primary = interp.get('primary', {})
        secondary = interp.get('secondary', {})
        
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🧠 INTERPRETATION</b> <i>(narrative · action см. DECISION)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        pattern_name = primary.get('pattern', 'UNKNOWN').replace('pattern_', '').replace('_', ' ').title()
        pattern_prob = primary.get('probability', 0)
        
        text += f"<b>Primary:</b> {pattern_name}\n"
        text += f"<b>Probability:</b> {pattern_prob*100:.0f}%\n"
        text += f"<b>Direction:</b> {primary.get('direction', 'UNKNOWN')}\n\n"
        
        # Story (narrative)
        story = primary.get('story', '')
        if story:
            text += f"<i>{story[:400]}</i>\n\n"
        
        # Secondary if exists
        if secondary and secondary.get('pattern'):
            sec_name = secondary.get('pattern', '').replace('pattern_', '').replace('_', ' ').title()
            sec_prob = secondary.get('probability', 0)
            text += f"<b>Alt:</b> {sec_name} ({sec_prob*100:.0f}%)\n\n"
        
        # Position hint removed — все action-указания только в DECISION блоке.
        # (interpretation.position_hint часто содержит "не лонгуй / trail stop" —
        # это action, а Xenia хочет single source of truth = confluence_gate.action)
        text += "\n"
    
    # === WHALE INTERPRETATION ===
    whale = load_json('whale_analysis.json')
    if whale:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🐋 WHALE INTERPRETATION</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        cohort = whale.get('cohort_read', 'MIXED_SIGNALS')
        text += f"<b>{cohort.replace('_', ' ')}</b>\n"
        
        stats = whale.get('stats', {})
        n = stats.get('n_events', 0)
        text += f"Events analyzed: {n}\n"
        text += f"CEX→private: {stats.get('cex_to_private_pct', 0):.0f}%\n"
        text += f"Private→CEX: {stats.get('private_to_cex_pct', 0):.0f}%\n"
        
        top_direction = whale.get('top_pattern', {}).get('direction', 'unknown')
        if top_direction != 'unknown':
            text += f"<b>Dominant:</b> {top_direction.replace('_', ' → ')}\n"
        
        text += "\n"
    
    # === COHORT BEHAVIOR === (CORRECT keys: net_flow_strk / address_count / behavior)
    cohorts = load_json('cohort_tracker.json') or {}
    if cohorts.get('cohorts'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>👥 COHORT BEHAVIOR</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        cohort_data = cohorts.get('cohorts', {}) or {}
        # cohort_tracker пишет aggregate_signal на top-level, не в 'aggregate'
        agg_signal = cohorts.get('aggregate_signal') or (cohorts.get('aggregate') or {}).get('signal') or NOT_CHECKED
        text += f"<b>Aggregate:</b> {agg_signal}\n\n"
        lines = []
        all_zero = True
        for cohort_name, cohort_info in cohort_data.items():
            if not isinstance(cohort_info, dict):
                continue
            # Правильные ключи (cohort_tracker.py schema)
            n_wallets = cohort_info.get('address_count') or cohort_info.get('n_wallets') or 0
            net_flow = cohort_info.get('net_flow_strk')
            if net_flow is None:
                net_flow = cohort_info.get('net_24h_strk')  # fallback на старую схему
            behavior = cohort_info.get('behavior') or cohort_info.get('direction') or 'STABLE'
            display_name = cohort_name.replace('_', ' ').title()
            arrow = '↗' if 'ACCUM' in behavior or 'INFLOW' in behavior else ('↘' if 'DISTRIB' in behavior or 'OUTFLOW' in behavior else '→')
            if net_flow is None:
                continue
            if net_flow == 0 and n_wallets > 0:
                lines.append(f"<b>{display_name}</b> ({n_wallets}w): no flow (last 24h)")
            elif net_flow != 0:
                lines.append(f"<b>{display_name}</b> ({n_wallets}w): {arrow} {net_flow:+,.0f} STRK · <i>{behavior}</i>")
                all_zero = False
        if not lines:
            text += f"{NOT_CHECKED}\n\n"
        elif all_zero:
            text += "\n".join(lines) + "\n"
            text += "<i>All cohorts flat — no meaningful 24h flow yet.</i>\n\n"
        else:
            text += "\n".join(lines) + "\n\n"
    
    # === STRUCTURE / МЕСТО === (use same helpers as LIQ/RUN — no more UNKNOWN)
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += f"<b>📍 STRUCTURE / МЕСТО</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    _composite_for_struct = load_json('composite_signal_v2.json') or {}
    _macro_for_struct = load_json('agent_input.json') or {}
    _btc_ctx = _get_btc_context(_composite_for_struct, _macro_for_struct)
    _tech_feat = (technical or {}).get('features') or {}
    
    # Regime: wyk.regime → computed из tech.slope+vol → UNKNOWN
    regime = wyckoff.get('regime') or _compute_regime(_tech_feat) or 'UNKNOWN'
    text += f"<b>Regime:</b> {regime}\n"
    
    # BTC cycle: composite.inputs.btc_context.cycle (авторитетно) → wyk.btc_cycle → UNKNOWN
    btc_cycle = _btc_ctx.get('cycle') or wyckoff.get('btc_cycle') or 'UNKNOWN'
    text += f"<b>Cycle (BTC):</b> {btc_cycle}\n"
    
    text += f"<b>Phase (Wyckoff):</b> {phase} {PHASE_EMOJI.get(phase, '')}\n"
    text += f"<b>Sub-phase:</b> {sub_phase}\n"
    text += f"<b>Wyckoff confidence:</b> {CONFIDENCE_EMOJI.get(confidence, '')} {confidence}\n\n"
    
    # Simple explanation with Composite context disclaimer
    text += f"<b>Что это значит:</b>\n"
    text += f"<i>{layman}</i>\n"
    text += f"<i>⓵ Composite phase/BTC cycle — контекст, не вердикт. Action = DECISION выше.</i>\n\n"
    
    # === TECHNICAL MOMENTUM === (aligned keys with LIQ/RUN + NOT_CHECKED for missing)
    if technical and technical.get('features'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📈 TECHNICAL MOMENTUM</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        
        feat = technical['features']
        # technical_momentum.py не пишет top-level signal/confidence — только features.
        # Вычисляем описание из features (без выдумывания "NOT_CHECKED")
        _feat = technical.get('features') or {}
        _slope = _feat.get('slope_3d_pct')
        _rsi = _feat.get('rsi')
        _vol = _feat.get('vol_ratio_3d_vs_30d')
        _computed_signal = 'NEUTRAL'
        if _slope is not None and _vol is not None:
            if _slope > 3 and _vol > 1.2:
                _computed_signal = 'HEALTHY_MARKUP'
            elif _slope < -3 and _vol > 1.2:
                _computed_signal = 'HEALTHY_MARKDOWN'
            elif abs(_slope) < 2:
                _computed_signal = 'CONSOLIDATION'
            else:
                _computed_signal = 'DRIFT'
        if _rsi is not None:
            if _rsi < 30:
                _computed_signal += ' (oversold)'
            elif _rsi > 70:
                _computed_signal += ' (overbought)'
        text += f"<b>Signal (derived):</b> {_computed_signal}\n"
        # Slope 3d — no fake 0
        slope = feat.get('slope_3d_pct')
        if slope is not None:
            text += f"<b>Slope 3d:</b> {slope:+.2f}% ({'↑' if slope > 0 else '↓'})\n"
        else:
            text += f"<b>Slope 3d:</b> {NOT_CHECKED}\n"
        # Volume
        vol = feat.get('vol_ratio_3d_vs_30d')
        if vol is not None:
            text += f"<b>Volume 3d/30d:</b> {vol:.2f}x\n"
        else:
            text += f"<b>Volume 3d/30d:</b> {NOT_CHECKED}\n"
        # RSI
        rsi = feat.get('rsi')
        if rsi is not None:
            text += f"<b>RSI:</b> {rsi:.0f}\n"
        else:
            text += f"<b>RSI:</b> {NOT_CHECKED}\n"
        # From 14d high/low — правильные ключи pct_from_high / pct_from_low
        pfh = feat.get('pct_from_high')
        if pfh is None:
            pfh = feat.get('pct_from_14d_high')
        if pfh is not None:
            text += f"<b>From 14d high:</b> {pfh:+.1f}%\n"
        else:
            text += f"<b>From 14d high:</b> {NOT_CHECKED}\n"
        pfl = feat.get('pct_from_low')
        if pfl is None:
            pfl = feat.get('pct_from_14d_low')
        if pfl is not None:
            text += f"<b>From 14d low:</b> {pfl:+.1f}%\n\n"
        else:
            text += f"<b>From 14d low:</b> {NOT_CHECKED}\n\n"
    
    # === ON-CHAIN EVIDENCE === (полный)
    conc_data = load_json('concentration_metrics.json')
    if conc_data and conc_data.get('metrics'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🔗 ON-CHAIN EVIDENCE</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        m = conc_data['metrics']
        text += f"<b>Signal:</b> {conc_data.get('signal', 'NEUTRAL')}\n"
        text += f"<b>HHI:</b> {m.get('hhi', 0):.4f} ({conc_data.get('hhi_regime', 'UNKNOWN')})\n"
        text += f"<b>Top10 share:</b> {m.get('top10_share_pct', 0):.2f}%\n"
        text += f"<b>Entropy:</b> {m.get('entropy_norm', 0):.3f}\n"
        conc_layman = conc_data.get('layman', '')
        if conc_layman:
            text += f"<i>{_safe(conc_layman[:200])}</i>\n"
        text += "\n"

    # === MICRO + SWING CONTEXT === (horizon split) Effort/CVD
    eff = load_json('effort_result.json')
    cvd_data = load_json('cvd_analysis.json')
    _micro_lines = []
    _swing_lines = []

    if eff:
        for tf, r in (eff.get('timeframes') or {}).items():
            if r.get('signal') == 'NEUTRAL':
                continue
            line = f"  · Effort {tf}: {r['signal']}"
            if r.get('interpretation'):
                line += f"\n    <i>{_safe(r['interpretation'][:100])}</i>"
            if str(tf).lower() in ('1h', '15m', '30m'):
                _micro_lines.append(line)
            else:
                _swing_lines.append(line)

    if cvd_data:
        for tf, r in (cvd_data.get('timeframes') or {}).items():
            if r.get('signal') == 'NEUTRAL':
                continue
            line = f"  · CVD {tf}: {r['signal']}"
            if r.get('interpretation'):
                line += f"\n    <i>{_safe(r['interpretation'][:100])}</i>"
            if str(tf).lower() in ('1h', '15m', '30m'):
                _micro_lines.append(line)
            else:
                _swing_lines.append(line)

    if _micro_lines:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚡ MICRO CONTEXT</b> <i>(тактика 4-24h)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        if eff:
            text += f"<b>Effort/Result:</b> {eff.get('consensus', 'MIXED')}\n"
        if cvd_data:
            text += f"<b>CVD:</b> {cvd_data.get('consensus', 'MIXED')}\n"
        for line in _micro_lines[:3]:
            text += line + "\n"
        text += "\n"

    if _swing_lines:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>⚖ SWING CONTEXT</b> <i>(3-14d)</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        for line in _swing_lines[:3]:
            text += line + "\n"
        text += "\n"

    
    # === CEX FLOW === (real classification path, NOT_CHECKED for missing)
    cex = load_json('cex_flow.json') or {}
    cex_class = cex.get('classification') or {}
    if cex_class.get('signal'):
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>🏦 CEX FLOW (7d)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"<b>Signal:</b> {cex_class.get('signal', NOT_CHECKED)}\n"
        text += f"<b>Confidence:</b> {cex_class.get('confidence', NOT_CHECKED)}\n"
        stats = cex_class.get('stats') or {}
        net = stats.get('total_net_strk')
        if net is not None:
            # % от circulating supply если знаем
            unlock_for_supply = load_json('unlock_signal.json') or {}
            circ = unlock_for_supply.get('circulating_supply_est')
            if circ:
                pct = (net / circ) * 100
                text += f"<b>Net 7d:</b> {net:+,.0f} STRK ({pct:+.2f}% supply)\n"
            else:
                text += f"<b>Net 7d:</b> {net:+,.0f} STRK\n"
        else:
            text += f"<b>Net 7d:</b> {NOT_CHECKED}\n"
        if stats.get('total_inflow_strk') is not None:
            text += f"<b>Inflow:</b> {stats['total_inflow_strk']:,.0f} STRK\n"
        if stats.get('total_outflow_strk') is not None:
            text += f"<b>Outflow:</b> {stats['total_outflow_strk']:,.0f} STRK\n"
        if stats.get('consecutive_bearish'):
            text += f"<b>Bearish streak:</b> {stats['consecutive_bearish']} days\n"
        interp = _safe(cex_class.get('interpretation', ''))
        if interp:
            text += f"<i>{_safe(interp[:200])}</i>\n"
        text += "\n"
    
    # === EVENT LAYER ===
    event_layer = load_json('event_layer.json')
    if event_layer and event_layer.get('signal') and event_layer.get('signal') != 'NEUTRAL':
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📅 EVENT LAYER</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += f"<b>Signal:</b> {event_layer.get('signal', 'NEUTRAL')}\n"
        text += f"<b>Confidence:</b> {event_layer.get('confidence', 'LOW')}\n"
        upcoming = event_layer.get('upcoming_events', [])
        if upcoming:
            top_event = upcoming[0]
            text += f"<b>Next ({top_event.get('days_until', 0)}d):</b> {_safe(top_event.get('title', '')[:50])}\n"
            text += f"<b>Impact:</b> {top_event.get('impact', 'unknown')}\n"
        reason = event_layer.get('reason', '')
        if reason:
            text += f"<i>{_safe(reason[:150])}</i>\n"
        text += "\n"

    # === DERIVATIVES ===
    funding = load_json('funding_signal.json')
    if funding:
        text += "━━━━━━━━━━━━━━━━━━━\n"
        text += "<b>📊 DERIVATIVES</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
        m = funding.get('funding_metrics', {})
        text += f"<b>Signal:</b> {funding.get('signal', 'NEUTRAL')}\n"
        text += f"<b>Funding (annualized):</b> {m.get('current_annualized_pct', 0):+.2f}%\n"
        if m.get('short_crowded'):
            text += f"⚠️ <b>Short crowded</b> — squeeze potential\n"
        if m.get('long_crowded'):
            text += f"⚠️ <b>Long crowded</b> — flush risk\n"
        text += "\n"

    
    # === MACRO CONTEXT === (BTC из composite, STRK из technical)
    composite = load_json('composite_signal_v2.json') or {}
    ma = load_json('agent_input.json') or {}
    btc_ctx = _get_btc_context(composite, ma)
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🌐 MACRO</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    # BTC
    if btc_ctx.get('price'):
        btc_line = f"<b>BTC:</b> ${btc_ctx['price']:,.0f}"
        if btc_ctx.get('dist200_pct') is not None:
            btc_line += f" (dist200 {btc_ctx['dist200_pct']:+.1f}%)"
        if btc_ctx.get('cycle'):
            btc_line += f" · cycle: {btc_ctx['cycle']}"
        text += btc_line + "\n"
    else:
        text += f"<b>BTC:</b> {NOT_CHECKED}\n"
    # STRK — из technical features (реальный источник)
    strk_price = (technical or {}).get('features', {}).get('price') if technical else None
    if strk_price is None and ma.get('strk'):
        strk_price = ma['strk'].get('price_usd') or ma['strk'].get('price')
    if strk_price:
        text += f"<b>STRK:</b> ${strk_price:.4f}\n\n"
    else:
        text += f"<b>STRK:</b> {NOT_CHECKED}\n\n"
    
    # === WHAT TO WATCH === (2-4 строки: invalidation + unlock + staking)
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🔄 WHAT TO WATCH</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    _watch_lines = []
    # 1-2. Invalidation levels
    _tech_feat = (technical or {}).get('features', {}) if technical else {}
    _high_14 = _tech_feat.get('high_14d')
    _low_14 = _tech_feat.get('low_14d')
    if _low_14 and _high_14:
        _watch_lines.append(f"Break below ${_low_14:.4f} → invalidates rally")
        _watch_lines.append(f"Break above ${_high_14:.4f} → invalidates crash")
    # 3. Unlock
    _unlock_data = load_json('unlock_signal.json') or {}
    _next_cliff = _unlock_data.get('next_cliff') or {}
    if _next_cliff.get('days_until') is not None:
        _u = f"Unlock in {_next_cliff['days_until']}d"
        if _next_cliff.get('pct_of_current_circ') is not None:
            _u += f" ({_next_cliff['pct_of_current_circ']:.1f}% supply)"
        _watch_lines.append(_u)
    # 4. Staking direction
    _stak = load_json('native_staking_flow.json') or {}
    _d7 = (_stak.get('deltas') or {}).get('delta_7d')
    if _d7 is not None and _d7 != 0:
        _arrow = '↗' if _d7 > 0 else '↘'
        _watch_lines.append(f"Staking7d {_arrow} {_d7/1e6:+.1f}M STRK")
    if _watch_lines:
        for line in _watch_lines[:4]:
            text += f"· {line}\n"
    else:
        text += f"· {NOT_CHECKED}\n"
    text += "\n"
    
    # === WHAT TO DO NOW === (enriched: passed/failed checks + human triggers)
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>💡 WHAT TO DO NOW</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    
    if confluence:
        _conf_sig = confluence.get('signal', 'NO_SIGNAL')
        _conf_lvl = confluence.get('confidence', 'LOW')
        _r_score = confluence.get('rally_score', 0)
        _c_score = confluence.get('crash_score', 0)
        _checks = confluence.get('checks') or {}
        _action = confluence.get('action', 'STAY FLAT')
        
        # Signal + Action одной строкой
        if _conf_lvl == 'HIGH' and 'RALLY' in _conf_sig:
            text += f"🟢🟢 <b>{_conf_sig}</b> · confidence <b>HIGH</b> · <b>{_action}</b>\n"
        elif _conf_lvl == 'HIGH' and 'CRASH' in _conf_sig:
            text += f"🔴🔴 <b>{_conf_sig}</b> · confidence <b>HIGH</b> · <b>{_action}</b>\n"
        elif _conf_lvl == 'MEDIUM':
            text += f"🟡 <b>{_conf_sig}</b> · confidence <b>MEDIUM</b> · <b>{_action}</b>\n"
        else:
            text += f"⚪ <b>{_conf_sig}</b> · confidence <b>{_conf_lvl}</b> · <b>{_action}</b>\n"
        text += f"→ RALLY {_r_score}/9 · CRASH {_c_score}/9\n"
        # Layman-friendly объяснение — та же фраза во всех режимах
        text += f"<i>{_get_layman_verdict(_conf_sig, _conf_lvl)}</i>\n\n"
        
        # Разделить checks на rally/crash intent
        _rally_intent = {'on_chain_ok', 'price_up_3d', 'accelerating', 'vol_expanding',
                         'not_distributing_cex', 'events_supportive', 'liquidity_shift_bullish',
                         'bridge_inflow_bullish', 'strk_outperforming_l2_sector',
                         'post_capitulation_squeeze', 'strong_off_chain_bull',
                         'not_extreme_short', 'not_bouncing', 'event_bullish'}
        _crash_intent = {'liquidity_shift_bearish', 'bridge_outflow_bearish',
                         'strk_underperforming_l2_sector', 'strong_off_chain_bear',
                         'events_bearish', 'event_bearish'}
        
        rally_passed, rally_failed, crash_passed, crash_failed = [], [], [], []
        for name, ok in _checks.items():
            trig = CHECK_TRIGGERS.get(name, name)
            if name in _rally_intent:
                (rally_passed if ok else rally_failed).append(trig)
            elif name in _crash_intent:
                (crash_passed if ok else crash_failed).append(trig)
        
        # Активные checks
        if rally_passed:
            text += "<b>✓ Rally checks passed:</b>\n"
            for t in rally_passed[:5]:
                text += f"  · {t}\n"
            text += "\n"
        if crash_passed:
            text += "<b>✓ Crash checks passed:</b>\n"
            for t in crash_passed[:5]:
                text += f"  · {t}\n"
            text += "\n"
        
        # Триггеры для смены DECISION
        if rally_failed:
            text += "<b>Что перевернёт в LONG</b> <i>(rally checks failing)</i>:\n"
            for t in rally_failed[:4]:
                text += f"  · {t}\n"
            text += "\n"
        if crash_failed:
            text += "<b>Что перевернёт в SHORT</b> <i>(crash checks failing)</i>:\n"
            for t in crash_failed[:4]:
                text += f"  · {t}\n"
            text += "\n"
        
        text += "<i>💡 Action = только из DECISION. Triggers = что мониторить для смены.</i>\n\n"
    else:
        text += "⚪ <b>DECISION: CONFLUENCE_GATE недоступен</b>\n"
        text += "→ STAY FLAT до восстановления пайплайна.\n\n"

    # === STARKNET NETWORK (Dune) === ecosystem health FUND context
    text += _format_dune_starknet_block()

    # === WHALE 6h === (агрегат whale событий вместо потока alerts)
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>🐋 WHALE 6h</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    _whale_events_log = SCRIPT_DIR / 'data' / 'history' / 'whale_events.jsonl'
    _whale_6h_events = []
    if _whale_events_log.exists():
        _cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
        try:
            with open(_whale_events_log, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        _ts = datetime.fromisoformat(e['ts'])
                        if _ts > _cutoff:
                            _whale_6h_events.append(e)
                    except Exception:
                        continue
        except Exception:
            pass
    if _whale_6h_events:
        _total = len(_whale_6h_events)
        _both_known = sum(1 for e in _whale_6h_events if e.get('both_known'))
        _candidates = sum(1 for e in _whale_6h_events if not e.get('both_known') and not e.get('watchlist_hit'))
        _total_amt = sum(e.get('amount_strk', 0) for e in _whale_6h_events)
        text += f"{_total} events · {_both_known} already watched · {_candidates} candidate ADD\n"
        text += f"Total volume: {_total_amt/1e6:.1f}M STRK\n\n"
        # Show top 3 candidate addresses (не в seeds)
        _cands = sorted(
            (e for e in _whale_6h_events if not e.get('both_known') and not e.get('watchlist_hit')),
            key=lambda x: x.get('amount_strk', 0),
            reverse=True
        )[:3]
        if _cands:
            text += "<b>Top candidates (ADD?):</b>\n"
            for e in _cands:
                # Which side is unknown?
                _unk = e.get('to_addr') if not e.get('to_cohort') else e.get('from_addr')
                _amt = e.get('amount_strk', 0) / 1e6
                text += f"<code>{_unk}</code>\n  · {_amt:.2f}M STRK\n"
    else:
        text += f"{NOT_CHECKED} · нет whale событий за 6h\n"
    text += "\n"
    
    # === DISCOVERY (6h) ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>👁 DISCOVERY (6h)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    if accepted:
        text += f"<b>New wallets:</b> {len(accepted)}\n"
        for d in accepted[:3]:
            text += f"  ✓ {_safe(d.get('name', 'unnamed')[:20])} → {d.get('assigned_category', 'watchlist')}\n"
    else:
        text += "<b>New wallets:</b> 0\n"
    text += f"<b>Rejected:</b> {len(rejected)} · <b>In queue:</b> {len(queued)}\n"
    text += f"<b>Whale events 24h:</b> {whale_count} ({whale_amt:,.0f} STRK)\n\n"

    # === MODEL HONESTY ===
    text += "━━━━━━━━━━━━━━━━━━━\n"
    text += "<b>📋 MODEL HONESTY</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━\n"
    if wyckoff.get('confidence') == 'HIGH':
        text += "⚠️ HIGH confidence — но backtest на 9 events дал 33% accuracy.\n"
        text += "⚠️ N=9, не статистика. Sub-phases не откалиброваны.\n"
    elif wyckoff.get('confidence') == 'MEDIUM':
        text += "Baseline v2: 66.7% (6/9). Все 3 fails — Distribution phase.\n"
    else:
        text += "Baseline v2 (6 signals): 66.7% accuracy on 9 tests.\n"
    text += "<i>Читать: /probability для деталей.</i>\n\n"

    
    # === SCENARIOS === Обёрнуто в try/except — падение блока НЕ роняет digest
    try:
        scenario_data = load_json('scenario_analysis.json')
        if scenario_data and scenario_data.get('scenarios'):
            text += "━━━━━━━━━━━━━━━━━━━\n"
            text += "<b>🎯 SCENARIOS (7-14d)</b>\n"
            text += "━━━━━━━━━━━━━━━━━━━\n"
            
            raw_scenarios = scenario_data.get('scenarios', {})
            primary = scenario_data.get('primary', 'BASE')
            # Current price for computing price_change_pct if scenario_engine doesn't provide
            _current_price = (technical or {}).get('features', {}).get('price') if technical else None
            
            # Normalize: scenario_engine пишет list, digest ждёт dict.
            # Принимаем оба формата.
            scenarios_dict = {}
            if isinstance(raw_scenarios, dict):
                # уже dict — используем как есть
                for key, value in raw_scenarios.items():
                    if isinstance(value, dict):
                        scenarios_dict[str(key).lower()] = value
            elif isinstance(raw_scenarios, list):
                # list — маппим по name/type/label
                for item in raw_scenarios:
                    if not isinstance(item, dict):
                        continue
                    # Пробуем разные ключи для имени
                    name = (item.get('name') or item.get('type') or 
                            item.get('label') or item.get('scenario') or '').lower()
                    if 'bull' in name:
                        scenarios_dict['bull'] = item
                    elif 'bear' in name:
                        scenarios_dict['bear'] = item
                    elif 'base' in name or 'neutral' in name:
                        scenarios_dict['base'] = item
            
            # Show all 3 scenarios with probabilities
            for scen_name in ['bull', 'base', 'bear']:
                scen = scenarios_dict.get(scen_name, {})
                if not isinstance(scen, dict):
                    continue
                
                # Пробуем разные форматы probability
                prob = scen.get('probability', scen.get('probability_pct', 0))
                if isinstance(prob, (int, float)) and prob > 1.5:
                    prob = prob / 100.0  # если это проценты — конвертируем в fraction
                
                # target price
                target = scen.get('target_price', 0)
                if not target:
                    # Может быть price_range list или dict
                    pr = scen.get('price_range', 0)
                    if isinstance(pr, list) and len(pr) >= 2:
                        target = (pr[0] + pr[1]) / 2
                    elif isinstance(pr, dict):
                        target = (pr.get('low', 0) + pr.get('high', 0)) / 2
                
                # price change
                # Если price_change_pct = 0 или отсутствует — вычислить из target vs current
                change = scen.get('price_change_pct', scen.get('change_pct'))
                if not change:
                    _target = scen.get('target_price')
                    if _target and _current_price:
                        change = ((_target - _current_price) / _current_price) * 100
                    else:
                        change = 0
                
                emoji = '🟢' if scen_name == 'bull' else ('⚪' if scen_name == 'base' else '🔴')
                is_primary = ' ⭐ PRIMARY' if scen_name.upper() == str(primary).upper() else ''
                
                text += f"{emoji} <b>{scen_name.upper()}</b> ({prob*100:.0f}%){is_primary}\n"
                text += f"   → ${target:.4f} ({change:+.1f}%)\n"
            
            text += "\n"
    except Exception as _scen_err:
        # SCENARIOS блок сломался — не критично, продолжаем digest
        logger.warning(f"SCENARIOS block skipped: {_scen_err}")
    
    # === FOOTER ===
    text += f"<b>Coverage:</b> {wallet_total} wallets\n"
    for cat, cnt in sorted(wallet_by_cat.items()):
        text += f"  · {cat}: {cnt}\n"
    text += f"\n<i>Next update: in ~6h. If you want a full RUN report — reply /run</i>"
    
    return text


def format_fallback_digest(composite, accepted, rejected, whale_count, wallet_total):
    """Fallback if Wyckoff data missing"""
    now = datetime.now(timezone.utc)
    
    text = f"<b>🤖 STRK-GUARD</b>\n"
    text += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"
    text += f"⚠️ Wyckoff analysis unavailable\n\n"
    
    if composite:
        text += f"<b>Composite signal:</b>\n"
        text += f"{composite.get('direction', 'UNKNOWN')} · "
        text += f"{composite.get('strength', 0):.2f} strength · "
        text += f"{composite.get('confidence', 'UNKNOWN')} confidence\n\n"
    
    text += f"<b>Recent activity (6h):</b>\n"
    text += f"· Accepted: {len(accepted)}\n"
    text += f"· Rejected: {len(rejected)}\n"
    text += f"· Whale events 24h: {whale_count}\n\n"
    
    text += f"<b>Coverage:</b> {wallet_total} wallets\n"
    
    return text


def send_telegram(text):
    """Send text to Telegram. Logs full response so root causes are visible in Actions."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    logger.info(f"send_telegram: text_length={len(text)} chars")
    
    if not token or not chat_id:
        logger.warning("Telegram not configured. Would send:")
        logger.warning(text[:500])
        return False
    
    import urllib.request
    import urllib.parse
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }).encode()
    
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"Sent OK · msg_length={len(text)} · message_id={result.get('result', {}).get('message_id')}")
                return True
            else:
                # Telegram error — вывести полный ответ и первые 300 chars текста
                logger.error(f"Telegram REJECTED: {result}")
                logger.error(f"Text preview (first 300 chars): {text[:300]}")
                return False
    except urllib.request.HTTPError as e:
        # 400 Bad Request → тело ответа расскажет что не так с HTML
        try:
            err_body = e.read().decode('utf-8')
            logger.error(f"HTTP {e.code}: {err_body}")
            logger.error(f"Text preview (first 300 chars): {text[:300]}")
        except Exception:
            logger.error(f"HTTP {e.code} (body unavailable)")
        return False
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")
        return False


def send_telegram_document(file_path, caption=''):
    """Send file (HTML/PDF etc) to Telegram via multipart POST."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        logger.warning("Telegram not configured — document not sent")
        return False
    if not Path(file_path).exists():
        logger.warning(f"Document not found: {file_path}")
        return False

    import urllib.request
    import uuid
    boundary = f"----STRK{uuid.uuid4().hex}"

    with open(file_path, 'rb') as f:
        file_bytes = f.read()

    filename = Path(file_path).name
    body = []
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'.encode())
    body.append(f'{chat_id}\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="caption"\r\n\r\n'.encode())
    body.append(f'{caption[:1024]}\r\n'.encode())
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'.encode())
    body.append(b'HTML\r\n')
    body.append(f'--{boundary}\r\n'.encode())
    body.append(f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode())
    body.append(b'Content-Type: text/html\r\n\r\n')
    body.append(file_bytes)
    body.append(f'\r\n--{boundary}--\r\n'.encode())
    data = b''.join(body)

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                logger.info(f"Document sent: {filename}")
                return True
            logger.error(f"sendDocument error: {result}")
            return False
    except Exception as e:
        logger.error(f"Failed to send document: {e}")
        return False


# ============================================================
# HORIZON EXTRACTORS — единая vocabulary для LIQ/RUN
# Все возвращают строки. Если данных нет — "NOT_CHECKED".
# ============================================================
def _extract_micro_line(eff, cvd, tech):
    """MICRO (4-24h): Effort 1H + CVD 1H + RSI + slope3d.
    Возвращает один-два fact-string'а или NOT_CHECKED."""
    parts = []
    # Effort 1H (или 15m/30m если 1h NEUTRAL)
    for tf in ['1h', '1H', '15m', '30m']:
        r = (eff.get('timeframes') or {}).get(tf)
        if isinstance(r, dict) and r.get('signal') and r['signal'] != 'NEUTRAL':
            parts.append(f"Effort {tf}={r['signal']}")
            break
    # CVD 1H
    for tf in ['1h', '1H', '15m', '30m']:
        r = (cvd.get('timeframes') or {}).get(tf)
        if isinstance(r, dict) and r.get('signal') and r['signal'] != 'NEUTRAL':
            parts.append(f"CVD {tf}={r['signal']}")
            break
    # RSI
    rsi = tech.get('rsi')
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
    # slope 3d
    slope = tech.get('slope_3d_pct')
    if slope is not None:
        parts.append(f"slope3d {slope:+.1f}%")
    return ' · '.join(parts) if parts else NOT_CHECKED


def _extract_swing_line(cex, ct, eff, cvd, cohorts, whale, circ_supply=None):
    """SWING (3-14d): CEX 7d % supply + alpha7d + Effort 4h/1d + whales→CEX + SMART 24h."""
    parts = []
    # CEX 7d
    cex_class = cex.get('classification') or {}
    cex_sig = cex_class.get('signal')
    if cex_sig and cex_sig not in ('INSUFFICIENT_DATA', 'UNKNOWN'):
        stats = cex_class.get('stats') or {}
        net = stats.get('total_net_strk')
        if net is not None and circ_supply:
            pct = (net / circ_supply) * 100
            parts.append(f"CEX7d={cex_sig} ({pct:+.2f}% supply)")
        elif net is not None:
            parts.append(f"CEX7d={cex_sig} ({net/1e6:+.1f}M STRK)")
        else:
            parts.append(f"CEX7d={cex_sig}")
    # Alpha 7d
    alpha = ((ct.get('strk_alpha') or {}).get('alpha_7d_pct'))
    if alpha is not None:
        parts.append(f"alpha7d {alpha:+.1f}%")
    # Effort 4h/1d
    for tf in ['4h', '4H', '1d', '1D']:
        r = (eff.get('timeframes') or {}).get(tf)
        if isinstance(r, dict) and r.get('signal') and r['signal'] != 'NEUTRAL':
            parts.append(f"Effort {tf}={r['signal']}")
            break
    # Whale-to-CEX — только если non-zero
    if isinstance(whale, dict):
        stats = whale.get('stats') or {}
        pct = stats.get('cex_to_private_pct')
        p2c = stats.get('private_to_cex_pct')
        if p2c is not None and p2c > 0:
            parts.append(f"Whales→CEX {p2c:.0f}%")
        elif pct is not None and pct > 0:
            parts.append(f"CEX→private {pct:.0f}%")
    # SMART cohort 24h — правильные ключи cohort_tracker.py
    coh = cohorts.get('cohorts') or {}
    smart = coh.get('SMART') or coh.get('smart') or coh.get('smart_money') or {}
    smart_net = smart.get('net_flow_strk')
    if smart_net is None:
        smart_net = smart.get('net_24h_strk')  # fallback
    if smart_net is not None and smart_net != 0:
        parts.append(f"SMART24h {smart_net:+,.0f} STRK")
    return ' · '.join(parts) if parts else NOT_CHECKED


def _extract_fund_line(unlock, stak, bridge, news):
    """FUNDAMENTAL (30-90d): unlock days + %supply + staking dir + bridge + news."""
    parts = []
    # Unlock next cliff
    nc = unlock.get('next_cliff') or {}
    if nc:
        days = nc.get('days_until')
        pct = nc.get('pct_of_current_circ')
        if days is not None:
            u = f"unlock {days}d"
            if pct is not None:
                u += f" ({pct:.1f}% supply)"
            parts.append(u)
    # Staking direction
    stak_sig = stak.get('signal')
    stak_deltas = stak.get('deltas') or {}
    d7 = stak_deltas.get('delta_7d')
    if d7 is not None:
        arrow = '↗' if d7 > 0 else ('↘' if d7 < 0 else '→')
        parts.append(f"staking7d {arrow} {d7/1e6:+.1f}M")
    elif stak_sig and stak.get('status') != 'NOT_CHECKED':
        parts.append(f"staking={stak_sig}")
    # Bridge activity
    b_sig = (bridge.get('classification') or {}).get('signal') or bridge.get('signal')
    if b_sig and b_sig != 'UNKNOWN':
        parts.append(f"bridge={b_sig}")
    # News overall signal
    n_sig = news.get('overall_signal')
    if n_sig and n_sig not in ('UNKNOWN', 'INSUFFICIENT_DATA'):
        parts.append(f"news={n_sig}")
    return ' · '.join(parts) if parts else NOT_CHECKED


def _get_btc_context(composite, macro):
    """BTC context из composite_signal_v2 → inputs.btc_context (авторитетный источник),
    fallback на agent_input.btc."""
    btc_ctx = ((composite.get('inputs') or {}).get('btc_context') or {})
    macro_btc = macro.get('btc') or {}
    return {
        'cycle': btc_ctx.get('cycle') or macro_btc.get('cycle'),
        'price': btc_ctx.get('btc_price') or macro_btc.get('price'),
        'dist200_pct': btc_ctx.get('dist200_pct') or macro_btc.get('dist200_pct'),
    }


def _compute_regime(tech):
    """Вычислить regime из tech features (никто в JSON не пишет regime явно).
    Правила:
      · slope 3d > +3% AND vol expanding → TRENDING_UP
      · slope 3d < -3% AND vol expanding → TRENDING_DOWN
      · abs(slope 3d) < 2 → RANGING
      · иначе → DRIFT
    """
    slope = tech.get('slope_3d_pct')
    vol = tech.get('vol_ratio_3d_vs_30d', 1.0)
    if slope is None:
        return None
    if slope > 3 and vol > 1.2:
        return 'TRENDING_UP'
    if slope < -3 and vol > 1.2:
        return 'TRENDING_DOWN'
    if abs(slope) < 2:
        return 'RANGING'
    return 'DRIFT'


def _extract_structure_line(wyk, tech, btc_cycle=None, regime=None):
    """Structure: phase.subphase, wyckoff_conf, regime, btc_cycle, price."""
    parts = []
    phase = wyk.get('phase')
    sub = wyk.get('sub_phase')
    if phase:
        p_str = phase
        if sub and sub != '—':
            p_str += f'.{sub}'
        parts.append(f"Phase {p_str}")
    if wyk.get('confidence'):
        parts.append(f"wyk={wyk['confidence']}")
    # regime: сначала из wyk, потом computed
    r = wyk.get('regime') or regime
    if r:
        parts.append(f"regime={r}")
    # BTC cycle: параметр (из composite) или из wyk
    bc = btc_cycle or wyk.get('btc_cycle')
    if bc:
        parts.append(f"BTC={bc}")
    price = tech.get('price') or tech.get('price_now')
    if price:
        parts.append(f"px=${price:.4f}")
    return ' · '.join(parts) if parts else NOT_CHECKED


# ============================================================
# SOFT SPLIT — truncate или split на 4096 boundary
# ============================================================
def _soft_split_4096(text, max_len=TELEGRAM_MAX):
    """Если текст > max_len, режем по последней '━━━' до границы, возвращаем [part1, part2]."""
    if len(text) <= max_len:
        return [text]
    # Найдём последний ━━━ до max_len
    boundary_marker = '━━━━━━━━━━━━━━━━━━━'
    split_at = text.rfind(boundary_marker, 0, max_len - 200)
    if split_at <= 0 or split_at < max_len // 2:
        # Fallback: жёсткая обрезка
        return [text[:max_len - 60] + '\n\n<i>...truncated (limit 4096)</i>']
    part1 = text[:split_at].rstrip() + '\n\n<i>→ продолжение ниже</i>'
    part2 = '<i>...continued</i>\n\n' + text[split_at:]
    # Убедимся что part2 тоже < max_len
    if len(part2) > max_len:
        part2 = part2[:max_len - 60] + '\n\n<i>...truncated</i>'
    return [part1, part2]


# ============================================================
# LIQ MODE — 1 сообщение (max 2 если overflow)
# ============================================================
def format_liq():
    """LIQ format: DECISION canonical + MICRO + SWING + FUND + STRUCTURE + CTA."""
    now = datetime.now(timezone.utc)
    conf = load_json('confluence_gate.json') or {}
    wyk = load_json('wyckoff_phase.json') or {}
    tech_full = load_json('technical_momentum.json') or {}
    tech = tech_full.get('features') or {}
    cex = load_json('cex_flow.json') or {}
    eff = load_json('effort_result.json') or {}
    cvd = load_json('cvd_analysis.json') or {}
    ct = load_json('cross_token_correlation.json') or {}
    stak = load_json('native_staking_flow.json') or {}
    scen = load_json('scenario_analysis.json') or {}
    unlock = load_json('unlock_signal.json') or {}
    bridge = load_json('bridge_activity.json') or {}
    news = load_json('news_aggregator.json') or {}
    whale = load_json('whale_analysis.json') or {}
    cohorts = load_json('cohort_tracker.json') or {}
    circ_supply = unlock.get('circulating_supply_est')

    t = f"<b>⚡ LIQ · STRK</b>\n"
    t += f"<i>{now.strftime('%Y-%m-%d %H:%M UTC')}</i>\n\n"

    # === CURRENT PHASE (compact 1-liner) === читается ПЕРЕД 3-horizon в LIQ
    _sqz_liq = load_json('squeeze_state.json') or {}
    _dune_liq = _load_dune_starknet()
    _phase_liq = _compute_current_phase(wyk, tech_full, _dune_liq, _sqz_liq)
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += f"{_phase_liq['emoji']} <b>PHASE:</b> {_phase_liq['phase']} <i>({_phase_liq['confidence']})</i>\n"
    t += f"<i>{_safe(_phase_liq['description'])}</i>\n\n"

    # === CURRENT PHASE === synthesis: wyckoff + Dune monthly + squeeze
    _squeeze_liq = load_json('squeeze_state.json') or {}
    _dune_liq = _load_dune_starknet()
    t += _format_current_phase_block(wyk or {}, tech_full, _dune_liq, _squeeze_liq)

    # STARKNET (Dune) compact for LIQ
    t += _format_dune_starknet_block(compact=True)

    # === 3-HORIZON ACTION (ПЕРВЫЙ БЛОК) ===
    _tech_feat = tech_full.get('features') or {}
    _macro_liq = load_json('agent_input.json') or {}
    _composite_liq = load_json('composite_signal_v2.json') or {}
    _btc_liq = _get_btc_context(_composite_liq, _macro_liq)
    _horizons_liq = _compute_action_3horizons(wyk, _tech_feat, cex, cohorts,
                                                unlock, news, _btc_liq, {}, cvd)
    # LIQ compact — только 3 вердикта одной строкой
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>🎯 ЧТО ДЕЛАТЬ СЕЙЧАС</b>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += f"{_horizons_liq['fund']['emoji']} <b>FUND:</b> {_horizons_liq['fund']['verdict']}\n"
    t += f"{_horizons_liq['swing']['emoji']} <b>SWING:</b> {_horizons_liq['swing']['verdict']}\n"
    t += f"{_horizons_liq['sqz']['emoji']} <b>SQZ:</b> {_horizons_liq['sqz']['verdict']}\n\n"

    # SHADOW voters (compact 1-liner)
    t += _format_shadow_voters_block(compact=True)

    # DECISION (canonical)
    sig = conf.get('signal', NOT_CHECKED)
    cfd = conf.get('confidence', NOT_CHECKED)
    action = conf.get('action', 'STAY FLAT')
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>🎯 DECISION</b>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += f"Signal: <b>{sig}</b>\n"
    t += f"Confidence: <b>{cfd}</b>\n"
    t += f"Rally: {conf.get('rally_score', '?')}/9 | Crash: {conf.get('crash_score', '?')}/9\n"
    if conf.get('summary'):
        t += f"<i>{conf['summary']}</i>\n"
    t += f"\n<b>Action:</b> {action}\n"
    t += f"<i>{_get_layman_verdict(sig, cfd)}</i>\n"
    # Primary scenario range if available
    raw_scen = scen.get('scenarios', [])
    primary_str = str(scen.get('primary', '')).upper()
    if isinstance(raw_scen, list):
        for s in raw_scen:
            if not isinstance(s, dict):
                continue
            n = str(s.get('type') or s.get('name') or '').upper()
            if primary_str and primary_str in n:
                pr = s.get('price_range', [])
                if isinstance(pr, list) and len(pr) >= 2:
                    t += f"<b>Primary range:</b> ${pr[0]:.4f} - ${pr[1]:.4f}\n"
                break
    t += "\n"

    # STRUCTURE line — с BTC context из composite и computed regime
    composite = load_json('composite_signal_v2.json') or {}
    macro = load_json('agent_input.json') or {}
    btc_ctx = _get_btc_context(composite, macro)
    regime = _compute_regime(tech)
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>📍 STRUCTURE</b>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += _extract_structure_line(wyk, tech, btc_cycle=btc_ctx.get('cycle'), regime=regime) + "\n\n"

    # MICRO
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>⚡ MICRO (4-24h)</b> <i>· noise, not decision</i>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += _extract_micro_line(eff, cvd, tech) + "\n\n"

    # SWING
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>⚖ SWING (3-14d)</b> <i>· tactical</i>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += _extract_swing_line(cex, ct, eff, cvd, cohorts, whale, circ_supply) + "\n\n"

    # FUNDAMENTAL
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += "<b>🌐 FUNDAMENTAL (30-90d)</b>\n"
    t += "━━━━━━━━━━━━━━━━━━━\n"
    t += _extract_fund_line(unlock, stak, bridge, news) + "\n\n"

    t += "<i>→ workflow_dispatch mode=run для полного контекста (3 msgs + HTML)</i>"

    return _soft_split_4096(t)


# ============================================================
# RUN MODE — ровно 3 сообщения
# ============================================================
def format_run_telegram():
    """Returns list[str] of 3 messages, each ≤ 4096 chars."""
    now = datetime.now(timezone.utc)
    ts = now.strftime('%Y-%m-%d %H:%M UTC')
    conf = load_json('confluence_gate.json') or {}
    wyk = load_json('wyckoff_phase.json') or {}
    tech_full = load_json('technical_momentum.json') or {}
    tech = tech_full.get('features') or {}
    cex = load_json('cex_flow.json') or {}
    eff = load_json('effort_result.json') or {}
    cvd = load_json('cvd_analysis.json') or {}
    ct = load_json('cross_token_correlation.json') or {}
    unlock = load_json('unlock_signal.json') or {}
    stak = load_json('native_staking_flow.json') or {}
    scen = load_json('scenario_analysis.json') or {}
    interp = (load_json('interpretation.json') or {}).get('interpretation') or {}
    conc = load_json('concentration_metrics.json') or {}
    cohorts = load_json('cohort_tracker.json') or {}
    macro = load_json('agent_input.json') or {}
    event_l = load_json('event_layer.json') or {}
    composite = load_json('composite_signal_v2.json') or {}
    news = load_json('news_aggregator.json') or {}
    bridge = load_json('bridge_activity.json') or {}
    whale = load_json('whale_analysis.json') or {}
    fund = load_json('funding_signal.json') or {}
    circ_supply = unlock.get('circulating_supply_est')

    # =========================
    # MSG 1/3: DECISION + gates + invalidation + watch 72h
    # =========================
    m1 = f"<b>📊 RUN · 1/3</b> · <i>{ts}</i>\n\n"

    # === CURRENT PHASE === structural synthesis — читается первым в MSG1
    _sqz_run = load_json('squeeze_state.json') or {}
    _dune_run = _load_dune_starknet()
    m1 += _format_current_phase_block(wyk, tech_full, _dune_run, _sqz_run)

    # === 3-HORIZON ACTION VERDICT (первый содержательный блок в MSG1) ===
    _tech_feat_r = tech_full.get('features') or {}
    _horizons_r = _compute_action_3horizons(wyk, _tech_feat_r, cex, cohorts,
                                              unlock, news, _get_btc_context(composite, macro),
                                              fund, cvd)
    m1 += _format_3horizon_block(_horizons_r)

    # === SHADOW VOTERS === (candidates for voter_wire_v2, NOT in DECISION)
    m1 += _format_shadow_voters_block(compact=False)

    m1 += "━━━━━━━━━━━━━━━━━━━\n"
    m1 += "<b>🎯 DECISION</b>\n"
    m1 += "━━━━━━━━━━━━━━━━━━━\n"
    m1 += f"Signal: <b>{conf.get('signal', NOT_CHECKED)}</b>\n"
    m1 += f"Confidence: <b>{conf.get('confidence', NOT_CHECKED)}</b>\n"
    if conf.get('summary'):
        m1 += f"<i>{conf['summary']}</i>\n"
    m1 += f"\n<b>Action:</b> {conf.get('action', 'STAY FLAT')}\n"
    m1 += f"<i>{_get_layman_verdict(conf.get('signal', ''), conf.get('confidence', ''))}</i>\n\n"

    # GATES (rally + crash breakdown by individual check)
    m1 += "━━━━━━━━━━━━━━━━━━━\n"
    m1 += "<b>✅ GATES BREAKDOWN</b>\n"
    m1 += "━━━━━━━━━━━━━━━━━━━\n"
    m1 += f"Rally checks: <b>{conf.get('rally_score', '?')}/9</b>\n"
    checks = conf.get('checks', {})
    if isinstance(checks, dict) and checks:
        passed = [k for k, v in checks.items() if v]
        failed = [k for k, v in checks.items() if not v]
        for k in passed[:6]:
            m1 += f"  ✓ {k}\n"
        for k in failed[:4]:
            m1 += f"  ✗ {k}\n"
    m1 += f"\nCrash checks: <b>{conf.get('crash_score', '?')}/9</b>\n"

    # INVALIDATION
    m1 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m1 += "<b>⚠ INVALIDATION</b>\n"
    m1 += "━━━━━━━━━━━━━━━━━━━\n"
    high_14d = tech.get('high_14d')
    low_14d = tech.get('low_14d')
    price = tech.get('price') or tech.get('price_now')
    if high_14d and low_14d and price:
        m1 += f"· Break below ${low_14d:.4f} → invalidates rally\n"
        m1 += f"· Break above ${high_14d:.4f} → invalidates crash\n"
    else:
        m1 += f"· Price levels {NOT_CHECKED}\n"
    nc = unlock.get('next_cliff', {})
    if nc.get('days_until') is not None:
        m1 += f"· Unlock day (day {nc['days_until']}) — reset context\n"

    # WATCH 72h
    m1 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m1 += "<b>👁 WATCH 72h</b>\n"
    m1 += "━━━━━━━━━━━━━━━━━━━\n"
    watch = wyk.get('watch_events', []) or []
    if watch:
        for e in watch[:6]:
            m1 += f"· {e}\n"
    else:
        m1 += f"· {NOT_CHECKED}\n"

    # =========================
    # MSG 2/3: STRUCTURE + MICRO + SWING + fact grid
    # =========================
    m2 = f"<b>📊 RUN · 2/3</b> · <i>{ts}</i>\n\n"

    # STARKNET NETWORK (Dune) — full section в MSG2 для context
    m2 += _format_dune_starknet_block(compact=False)

    # STRUCTURE — берём BTC из composite (авторитетно), regime из tech (computed)
    btc_ctx = _get_btc_context(composite, macro)
    regime = _compute_regime(tech)
    m2 += "━━━━━━━━━━━━━━━━━━━\n"
    m2 += "<b>📍 STRUCTURE / MESTO</b>\n"
    m2 += "━━━━━━━━━━━━━━━━━━━\n"
    m2 += f"Phase: {wyk.get('phase', NOT_CHECKED)} · {wyk.get('sub_phase', '—')}\n"
    m2 += f"Wyckoff conf: {wyk.get('confidence', NOT_CHECKED)}\n"
    m2 += f"Regime: {wyk.get('regime') or regime or NOT_CHECKED}\n"
    m2 += f"BTC cycle: {btc_ctx.get('cycle') or wyk.get('btc_cycle') or NOT_CHECKED}\n"
    if btc_ctx.get('price'):
        m2 += f"BTC price: ${btc_ctx['price']:,.0f}"
        if btc_ctx.get('dist200_pct') is not None:
            m2 += f" (dist200 {btc_ctx['dist200_pct']:+.1f}%)"
        m2 += "\n"
    if price:
        m2 += f"Price: <b>${price:.4f}</b>\n"
    if tech.get('rsi') is not None:
        m2 += f"RSI: {tech['rsi']:.0f}\n"
    if tech.get('slope_3d_pct') is not None:
        m2 += f"Slope 3d: {tech['slope_3d_pct']:+.2f}%\n"
    if tech.get('vol_ratio_3d_vs_30d') is not None:
        m2 += f"Vol 3d: {tech['vol_ratio_3d_vs_30d']:.2f}× avg\n"

    m2 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m2 += "<b>⚡ MICRO (4-24h)</b> <i>· noise, not decision</i>\n"
    m2 += "━━━━━━━━━━━━━━━━━━━\n"
    micro_line = _extract_micro_line(eff, cvd, tech)
    m2 += micro_line + "\n"

    m2 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m2 += "<b>⚖ SWING (3-14d)</b> <i>· tactical context</i>\n"
    m2 += "━━━━━━━━━━━━━━━━━━━\n"
    swing_line = _extract_swing_line(cex, ct, eff, cvd, cohorts, whale, circ_supply)
    m2 += swing_line + "\n"

    # COHORT detail
    m2 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m2 += "<b>👥 COHORT (24h)</b>\n"
    m2 += "━━━━━━━━━━━━━━━━━━━\n"
    coh_data = cohorts.get('cohorts', {}) or {}
    coh_lines = []
    all_zero = True
    for name, info in list(coh_data.items())[:5]:
        if not isinstance(info, dict):
            continue
        # Правильные ключи cohort_tracker.py
        n_w = info.get('address_count') or info.get('n_wallets') or 0
        net = info.get('net_flow_strk')
        if net is None:
            net = info.get('net_24h_strk')
        if net is None:
            continue
        behavior = info.get('behavior') or info.get('direction') or 'STABLE'
        arrow = '↗' if 'ACCUM' in behavior or 'INFLOW' in behavior else ('↘' if 'DISTRIB' in behavior or 'OUTFLOW' in behavior else '→')
        if net == 0 and n_w > 0:
            coh_lines.append(f"{name.replace('_', ' ').title()}: no flow (last 24h)")
        elif net != 0:
            coh_lines.append(f"{name.replace('_', ' ').title()}: {arrow} {net:+,.0f} STRK · {behavior}")
            all_zero = False
    if not coh_lines:
        m2 += NOT_CHECKED + "\n"
    elif all_zero:
        m2 += "\n".join(coh_lines) + "\n"
        m2 += "<i>All cohorts flat — no meaningful 24h flow yet.</i>\n"
    else:
        m2 += "\n".join(coh_lines) + "\n"

    # CONCENTRATION
    if conc.get('hhi'):
        m2 += "\n━━━━━━━━━━━━━━━━━━━\n"
        m2 += "<b>🔗 ON-CHAIN</b>\n"
        m2 += "━━━━━━━━━━━━━━━━━━━\n"
        m2 += f"HHI: {conc.get('hhi', 0):.4f}\n"
        if conc.get('top_1_share_pct') is not None:
            m2 += f"Top1: {conc['top_1_share_pct']:.2f}%\n"
        elif conc.get('top1_share_pct') is not None:
            m2 += f"Top1: {conc['top1_share_pct']:.2f}%\n"
        if conc.get('top_10_share_pct') is not None:
            m2 += f"Top10: {conc['top_10_share_pct']:.2f}%\n"
        elif conc.get('top10_share_pct') is not None:
            m2 += f"Top10: {conc['top10_share_pct']:.2f}%\n"

    # =========================
    # MSG 3/3: FUND + SCENARIOS + MODEL HONESTY
    # =========================
    m3 = f"<b>📊 RUN · 3/3</b> · <i>{ts}</i>\n\n"

    m3 += "━━━━━━━━━━━━━━━━━━━\n"
    m3 += "<b>🌐 FUNDAMENTAL (30-90d thesis health)</b>\n"
    m3 += "━━━━━━━━━━━━━━━━━━━\n"
    # Unlock full block
    nc = unlock.get('next_cliff', {})
    if nc.get('days_until') is not None:
        u_line = f"Unlock: {nc['days_until']} days"
        if nc.get('amount_strk'):
            u_line += f" ({nc['amount_strk']/1e6:.0f}M STRK"
            if nc.get('pct_of_current_circ') is not None:
                u_line += f", {nc['pct_of_current_circ']:.1f}% supply)"
            else:
                u_line += ")"
        m3 += u_line + "\n"
        if unlock.get('pressure'):
            m3 += f"Pressure: {unlock['pressure']}\n"
        if unlock.get('weekly_dilution_pct') is not None:
            m3 += f"Weekly dilution: {unlock['weekly_dilution_pct']:.2f}%\n"
    else:
        m3 += f"Unlock: {NOT_CHECKED}\n"
    # Staking full
    stak_deltas = stak.get('deltas', {}) or {}
    if stak_deltas.get('delta_7d') is not None:
        m3 += f"Staking Δ7d: {stak_deltas['delta_7d']/1e6:+.1f}M STRK\n"
    if stak.get('total_stake_strk_now'):
        m3 += f"Total staked: {stak['total_stake_strk_now']/1e6:.1f}M STRK\n"
    if stak.get('signal') and stak.get('status') != 'NOT_CHECKED':
        m3 += f"Staking signal: {stak['signal']}\n"
    # Bridge
    b_sig = (bridge.get('classification') or {}).get('signal') or bridge.get('signal')
    if b_sig and b_sig != 'UNKNOWN':
        m3 += f"Bridge activity: {b_sig}\n"
    # News
    if news.get('overall_signal'):
        m3 += f"News sentiment: {news['overall_signal']}\n"
    # Funding fundamental view
    fm = fund.get('funding_metrics') or {}
    if fm.get('current_annualized_pct') is not None:
        m3 += f"Funding APR: {fm['current_annualized_pct']:+.2f}%"
        if fm.get('trend'):
            m3 += f" ({fm['trend']})"
        m3 += "\n"

    # SCENARIOS
    m3 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m3 += "<b>🎯 SCENARIOS (7-14d)</b>\n"
    m3 += "━━━━━━━━━━━━━━━━━━━\n"
    try:
        raw_scen = scen.get('scenarios', [])
        primary_str = str(scen.get('primary', '')).upper()
        scen_dict = {}
        if isinstance(raw_scen, list):
            for s in raw_scen:
                if not isinstance(s, dict):
                    continue
                name = str(s.get('type') or s.get('name') or s.get('label') or '').upper()
                if 'BULL' in name:
                    scen_dict['BULL'] = s
                elif 'BEAR' in name:
                    scen_dict['BEAR'] = s
                elif 'BASE' in name or 'NEUTRAL' in name:
                    scen_dict['BASE'] = s
        elif isinstance(raw_scen, dict):
            for k, v in raw_scen.items():
                if isinstance(v, dict):
                    scen_dict[str(k).upper()] = v
        for n in ['BULL', 'BASE', 'BEAR']:
            s = scen_dict.get(n)
            if not s:
                continue
            emoji = {'BULL': '🟢', 'BASE': '⚪', 'BEAR': '🔴'}[n]
            prob = s.get('probability_pct') or (s.get('probability', 0) * 100)
            pr = s.get('price_range', [])
            range_str = ''
            if isinstance(pr, list) and len(pr) >= 2:
                range_str = f' → ${pr[0]:.4f}-${pr[1]:.4f}'
            elif isinstance(pr, dict):
                range_str = f' → ${pr.get("low", 0):.4f}-${pr.get("high", 0):.4f}'
            is_p = ' ⭐ PRIMARY' if n in primary_str else ''
            m3 += f"{emoji} <b>{n}</b> ({prob:.0f}%){is_p}{range_str}\n"
            if s.get('narrative'):
                m3 += f"   <i>{_safe(s['narrative'][:120])}</i>\n"
    except Exception as e:
        m3 += f"{NOT_CHECKED} ({e})\n"

    # MODEL HONESTY
    m3 += "\n━━━━━━━━━━━━━━━━━━━\n"
    m3 += "<b>📋 MODEL HONESTY</b>\n"
    m3 += "━━━━━━━━━━━━━━━━━━━\n"
    m3 += "Baseline composite v2: 66.7% on 9 events\n"
    m3 += "Shadow voters: awaiting N=15 for calibration\n"
    m3 += "Sub-phases: not calibrated\n"

    m3 += "\n<i>Full HTML report attached below.</i>"

    # Truncate each to Telegram limit
    return [truncate(m1), truncate(m2), truncate(m3)]


def main():
    """Route via MODE env var: digest (default) | liq | run"""
    mode = (os.environ.get('MODE') or 'digest').lower().strip()
    logger.info("=" * 60)
    logger.info(f"STRK ENGINE · Telegram Delivery · MODE={mode}")
    logger.info("=" * 60)

    if mode == 'liq':
        # LIQ: 1-2 сообщения (soft split на 4096 boundary)
        messages = format_liq()
        logger.info(f"LIQ built: {len(messages)} message(s), sizes: {[len(m) for m in messages]}")
        all_sent = True
        for i, m in enumerate(messages, 1):
            sent = send_telegram(m)
            _log_alert(event_type=f"liq" if len(messages) == 1 else f"liq_msg_{i}_of_{len(messages)}",
                       text=m, sent=sent)
            if sent:
                logger.info(f"LIQ {i}/{len(messages)} sent")
            else:
                logger.error(f"LIQ {i}/{len(messages)} FAILED")
                all_sent = False
            if i < len(messages):
                time.sleep(1)
        return 0 if all_sent else 1

    if mode == 'run':
        # RUN: 3 сообщения + HTML документ
        messages = format_run_telegram()
        logger.info(f"RUN built: 3 messages, sizes: {[len(m) for m in messages]}")
        all_sent = True
        for i, m in enumerate(messages, 1):
            sent = send_telegram(m)
            _log_alert(event_type=f"run_msg_{i}_of_3", text=m, sent=sent)
            if sent:
                logger.info(f"RUN {i}/3 sent")
            else:
                logger.error(f"RUN {i}/3 FAILED")
                all_sent = False
            # Небольшой gap между сообщениями чтобы не поймать rate limit
            if i < len(messages):
                time.sleep(1)

        # HTML документ (опционально)
        report_dir = SCRIPT_DIR / 'data' / 'reports'
        if report_dir.exists():
            # Ищем самый свежий STRK_RUN_*.html
            candidates = sorted(report_dir.glob('STRK_RUN_*.html'), key=lambda p: p.stat().st_mtime, reverse=True)
            # Preference: latest.html если есть
            latest_named = report_dir / 'STRK_RUN_latest.html'
            html_path = latest_named if latest_named.exists() else (candidates[0] if candidates else None)
            if html_path:
                doc_sent = send_telegram_document(
                    html_path,
                    caption=f"📄 STRK RUN report · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · archive, not a substitute"
                )
                _log_alert(event_type="run_html_doc", text=str(html_path), sent=doc_sent)
                if doc_sent:
                    logger.info(f"HTML doc sent: {html_path.name}")
            else:
                logger.warning("No STRK_RUN_*.html found for attachment")

        return 0 if all_sent else 1

    # Default: digest — split при необходимости на несколько сообщений
    text = format_digest()
    logger.info(f"Digest built (length {len(text)})")
    parts = split_digest_into_parts(text, TELEGRAM_MAX)
    if len(parts) > 1:
        logger.info(f"Digest split into {len(parts)} messages")
    all_sent = True
    for i, part in enumerate(parts, 1):
        logger.info(f"Digest {i}/{len(parts)}: {len(part)} chars")
        ok = send_telegram(part)
        _log_alert(event_type=f"digest_{i}_{len(parts)}", text=part, sent=ok)
        if not ok:
            all_sent = False
            logger.error(f"Digest {i}/{len(parts)} FAILED")
        else:
            logger.info(f"Digest {i}/{len(parts)} sent")
        if i < len(parts):
            time.sleep(1)  # rate limit между сообщениями
    return 0 if all_sent else 1


if __name__ == '__main__':
    sys.exit(main())
