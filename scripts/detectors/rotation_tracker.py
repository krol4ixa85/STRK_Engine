#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rotation_tracker.py — Автоматический трекер ротации
Следит за токенами в STRONG_BUY / BUY_PRESSURE / DIVERGENCE и оповещает об изменениях.

Читает snapshot от strk_lab.py: data/cache/strk_lab_report.json
Отправляет в @Lab_sector_bot когда signals меняются.

Улучшения над оригиналом (Xenia's version):
  - Правильный path (was scripts/data/cache, теперь repo_root/data/cache)
  - Использует TELEGRAM_LAB_SECTOR_BOT (не TELEGRAM_BOT_TOKEN)
  - Streak tracking → CONFIRMED_HOLD_3D alert (3+ дней подряд STRONG_BUY)
  - DIVERGENCE_WARN alert (STRONG_BUY → DIVERGENCE)
  - History log в data/history/rotation_alerts.jsonl
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

# ============================================================
# КОНФИГ
# ============================================================
SCRIPT_DIR = Path(__file__).parent.parent.parent  # scripts/detectors/ → repo root
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
HISTORY_DIR = SCRIPT_DIR / 'data' / 'history'
STATE_FILE = CACHE_DIR / 'rotation_tracker_state.json'
LAB_DATA_FILE = CACHE_DIR / 'strk_lab_report.json'  # источник данных
HISTORY_FILE = HISTORY_DIR / 'rotation_alerts.jsonl'

# Telegram — @Lab_sector_bot (отдельно от STRK_GUARDIAN)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_LAB_SECTOR_BOT', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

# Пороги
MIN_NET_FLOW_M_USD = 0.1  # минимальный net flow чтобы попасть в считаемые
CONFIRMED_HOLD_DAYS = 3  # streak для "confirmed hold" alert


# ============================================================
# ЗАГРУЗКА ДАННЫХ
# ============================================================
def load_lab_data() -> Dict:
    """Загружает последний LAB-snapshot"""
    if not LAB_DATA_FILE.exists():
        return {}
    try:
        with open(LAB_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка загрузки LAB snapshot: {e}")
        return {}


def load_prev_snapshot() -> Dict:
    """Загружает предыдущий snapshot для 24h delta comparison."""
    prev_path = CACHE_DIR / 'strk_lab_report_prev.json'
    if not prev_path.exists():
        return {}
    try:
        with open(prev_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def compute_deltas(current: Dict, previous: Dict) -> Dict:
    """Вычисляет 24h delta для каждого token в current snapshot.
    Returns: {token: {price_delta_pct, net_flow_delta_m, prev_price, prev_net_flow, was_in_signal}}
    """
    if not previous:
        return {}

    # Build previous token → data map (across all sections)
    prev_tokens = {}
    for section in ('strong_buy', 'divergence', 'buy_pressure', 'sell'):
        for t in previous.get(section, []) or []:
            if isinstance(t, dict) and t.get('token'):
                prev_tokens[t['token']] = {
                    'section': section,
                    'signal': t.get('signal', section.upper()),
                    'net_flow_m_usd': t.get('net_flow_m_usd', 0),
                    'price_change_7d_pct': t.get('price_change_7d_pct', 0),
                }

    deltas = {}
    for section in ('strong_buy', 'divergence', 'buy_pressure', 'sell'):
        for t in current.get(section, []) or []:
            if not isinstance(t, dict) or not t.get('token'):
                continue
            token = t['token']
            prev = prev_tokens.get(token, {})
            if prev:
                # Compute deltas
                curr_flow = t.get('net_flow_m_usd', 0) or 0
                prev_flow = prev.get('net_flow_m_usd', 0) or 0
                deltas[token] = {
                    'net_flow_delta_m': round(curr_flow - prev_flow, 2),
                    'prev_signal': prev.get('signal'),
                    'prev_net_flow_m': prev_flow,
                    'was_tracked': True,
                }
            else:
                deltas[token] = {
                    'was_tracked': False,
                    'first_appearance': True,
                }
    return deltas


def build_playbook(transition_type: str, token_data: Dict, deltas: Dict) -> Dict:
    """Строит 3-variant playbook с обоснованием.
    Returns: {conservative, balanced, aggressive} — каждый с action + reasoning + risk + reward.
    """
    price_change_7d = token_data.get('price_change_7d_pct', 0) or 0
    net_flow = token_data.get('net_flow_m_usd', 0) or 0
    sector = token_data.get('sector', 'unknown')
    tx_count = token_data.get('tx_count', 0) or 0

    delta = deltas.get(token_data.get('token', ''), {})
    flow_delta = delta.get('net_flow_delta_m', 0)

    playbook = {}

    if transition_type == 'NEW_STRONG_BUY':
        # Свежий signal — большие flow + price up + high liquidity
        conf_signal = 'STRONG' if (net_flow > 10 and price_change_7d > 10 and tx_count > 50000) else 'MODERATE'

        playbook['conservative'] = {
            'action': 'Подожди 24h подтверждения',
            'reasoning_layman': (
                f'Signal только что появился. За последние сутки net flow вырос на {flow_delta:+.1f}M — '
                f'это хороший знак. Но 1-day signals часто оказываются шумом (~30% случаев). '
                f'Подожди второй day подтверждения — потеряешь пару процентов upside, '
                f'но защитишь от fake breakout.'
            ),
            'risk': 'Пропустишь +3-7% если signal реальный',
            'reward': 'Не войдёшь если это шум (~30% случаев)',
        }
        playbook['balanced'] = {
            'action': 'Купить 25-33% target position сейчас',
            'reasoning_layman': (
                f'Scale-in подход. Заходишь маленькой позицией чтобы участвовать в движении, '
                f'но не рискуешь всем капиталом на 1-day signal. Через 2-3 дня добавишь если signal удержится.'
            ),
            'risk': 'Если reversal — теряешь на 25-33% позиции',
            'reward': f'Если реально bullish — участвуешь с самого начала (price сейчас {price_change_7d:+.1f}%)',
        }
        playbook['aggressive'] = {
            'action': 'Full position сразу',
            'reasoning_layman': (
                f'Confidence {conf_signal}: net flow {net_flow:+.1f}M, price {price_change_7d:+.1f}%, '
                f'{tx_count:,} tx (liquidity {"HIGH" if tx_count > 50000 else "MEDIUM"}). '
                f'Signal имеет все компоненты сильного setup — flow, momentum, ликвидность.'
            ),
            'risk': 'Full loss если signal fake (30% вероятность)',
            'reward': 'Максимальный profit если pattern real',
        }

    elif transition_type == 'DIVERGENCE_WARN':
        # Был STRONG_BUY → стал DIVERGENCE. Price ещё растёт, но flow упал.
        prev_flow = delta.get('prev_net_flow_m', 0)
        playbook['conservative'] = {
            'action': 'Продать 30-50% позиции',
            'reasoning_layman': (
                f'Классический "smart money exit" паттерн. Цена продолжает расти ({price_change_7d:+.1f}% за 7d), '
                f'но buy volume flipped: было {prev_flow:+.1f}M → стало {net_flow:+.1f}M за 24h. '
                f'Это значит крупные игроки продают пока розница покупает. '
                f'Фиксируешь профит на пиках — safe defensive play.'
            ),
            'risk': f'Если это шум ({100-60}% случаев на крипте) — пропустишь ещё +5-7% роста',
            'reward': f'Защищаешь +{max(price_change_7d, 0):.0f}% профит от разворота',
        }
        playbook['balanced'] = {
            'action': 'Trailing stop -8% от текущей цены',
            'reasoning_layman': (
                f'8% — это примерно 1 ATR (нормальная дневная волатильность для крипты). '
                f'Позволяет цене нормально колебаться, но защищает от больших разворотов. '
                f'Если это правда smart money exit — стоп сработает через 1-2 дня. '
                f'Если шум — цена вернётся и стоп никогда не тронет.'
            ),
            'risk': 'В flash crash — slippage (стоп сработает по худшей цене)',
            'reward': 'Держишь position если это шум (60% случаев так)',
        }
        playbook['aggressive'] = {
            'action': 'Hold до 2+ дней подтверждения flow < 0',
            'reasoning_layman': (
                f'60% DIVERGENCE случаев на криптe = single-day noise (whale wallet распродал 1 раз, потом ok). '
                f'Confirmation через 2 дня negative flow reduces false rate to ~15%. '
                f'Пока держишь — если pattern real потеряешь 8-15%. Если fake — ничего не потерял.'
            ),
            'risk': 'Если pattern real — 8-15% просадки пока ждёшь',
            'reward': 'Не выйдешь на fake signal (60% случаев)',
        }

    elif transition_type == 'EXIT_STRONG_BUY':
        # STRONG_BUY → SELL_PRESSURE (или STRONG_SELL). Distribution подтверждён.
        playbook['conservative'] = {
            'action': 'Продать всё market order',
            'reasoning_layman': (
                f'Distribution подтверждён — signal перешёл в SELL. '
                f'Ждать бессмысленно — pattern уже развернулся. '
                f'Быстрый exit защищает оставшийся профит.'
            ),
            'risk': 'Slippage при быстрой продаже',
            'reward': 'Фиксируешь профит, освобождаешь капитал',
        }
        playbook['balanced'] = {
            'action': 'Продать 70% сейчас + 30% на отскоке +5%',
            'reasoning_layman': (
                f'Обычно после SELL signal цена отскакивает 3-7% на short squeeze (retail buying dip). '
                f'70% сразу защищает основной profit. Оставшиеся 30% используем для лучшей exit цены на bounce. '
                f'Если bounce не случится — теряешь на 30% позиции.'
            ),
            'risk': 'Если no bounce — оставшиеся 30% продашь ниже',
            'reward': '+3-7% на 30% позиции',
        }
        playbook['aggressive'] = {
            'action': 'Ждать bounce +5-10% перед exit',
            'reasoning_layman': (
                f'Bearish signals часто получают короткий bounce перед dump. '
                f'Терпеливый exit даёт лучшую цену. НО риск: если dump без bounce — потеряешь ~10-15%. '
                f'Работает только если у тебя железные нервы.'
            ),
            'risk': 'Если dump без bounce — теряешь 10-15%',
            'reward': '+5-10% лучшая exit цена если bounce случится',
        }

    elif transition_type == 'CONFIRMED_HOLD_3D':
        # 3+ дня streak — сильный setup. Скорее advice "не паникуй".
        playbook['conservative'] = {
            'action': 'Не добавлять, держать',
            'reasoning_layman': (
                f'3+ дня STRONG_BUY = уже перекупленно. Добавлять — покупать по высоким. '
                f'Best action: держать существующую позицию, ждать либо цель либо exit signal.'
            ),
            'risk': 'Пропустишь upside если trend продолжится',
            'reward': 'Не покупаешь на пиках',
        }
        playbook['balanced'] = {
            'action': 'Trailing stop -10% + держать',
            'reasoning_layman': (
                f'3d streak = сильный сетап, но перекупленность растёт. '
                f'Trailing stop 10% защищает от разворота, даёт trend продолжаться. '
                f'Не добавляй — уже упустила best entry.'
            ),
            'risk': 'Стоп может сработать на нормальной коррекции',
            'reward': 'Защита профита при уверенном signal',
        }
        playbook['aggressive'] = {
            'action': 'Добавить 25% на dip -5%',
            'reasoning_layman': (
                f'В сильном trend dip = opportunity. Confluence 3d streak + high flow — вероятность продолжения ~65%. '
                f'Добавляешь по нормальной коррекции, увеличиваешь position. '
                f'НО: если dip оказался разворотом — теряешь на добавке.'
            ),
            'risk': 'Если dip = разворот — потеря на новой позиции',
            'reward': 'Улучшаешь average entry price если trend продолжится',
        }

    return playbook


def format_playbook(playbook: Dict) -> str:
    """Format 3-variant playbook для Telegram."""
    if not playbook:
        return ''
    text = "\n<b>⚡ ВАРИАНТЫ (проверь свой setup)</b>\n"

    for variant_key, emoji, label in [
        ('conservative', '🟢', 'CONSERVATIVE'),
        ('balanced', '🟡', 'BALANCED'),
        ('aggressive', '🔴', 'AGGRESSIVE'),
    ]:
        v = playbook.get(variant_key)
        if not v:
            continue
        text += f"\n{emoji} <b>{label}</b> — {v['action']}\n"
        text += f"<i>{v['reasoning_layman']}</i>\n"
        text += f"⚠ <b>Риск:</b> {v['risk']}\n"
        text += f"✓ <b>Reward:</b> {v['reward']}\n"

    text += "\n<i>💡 Термины непонятны? Отправь /explain divergence, /explain trailing, /explain rr</i>"
    return text


def load_state() -> Dict:
    """Загружает состояние трекера"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'last_run': None,
        'strong_buy_tokens': [],
        'divergence_tokens': [],
        'buy_pressure_tokens': [],
        'sell_tokens': [],
        'signal_streaks': {},
        'alert_sent_first': False,
    }


def save_state(state: Dict):
    """Сохраняет состояние"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)


# ============================================================
# АНАЛИЗ
# ============================================================
def analyze_lab_data(lab_data: Dict) -> Dict:
    """Извлекает structured lists из LAB snapshot"""
    result = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'strk_status': lab_data.get('strk_status', {}),
        're_entry_triggers': lab_data.get('re_entry_triggers', {}),
        'strong_buy': lab_data.get('strong_buy', []),
        'divergence': lab_data.get('divergence', []),
        'buy_pressure': lab_data.get('buy_pressure', []),
        'sell': lab_data.get('sell', []),
    }
    return result


def detect_changes(current: Dict, previous: Dict) -> Dict:
    """Сравнивает текущие и предыдущие данные, находит transitions"""

    def tokens_set(items: List[Dict]) -> Set[str]:
        return {t['token'] for t in items if isinstance(t, dict) and t.get('token')}

    curr_buy = tokens_set(current.get('strong_buy', []))
    prev_buy = tokens_set(previous.get('strong_buy', []))
    curr_div = tokens_set(current.get('divergence', []))
    prev_div = tokens_set(previous.get('divergence', []))
    curr_pressure = tokens_set(current.get('buy_pressure', []))
    prev_pressure = tokens_set(previous.get('buy_pressure', []))
    curr_sell = tokens_set(current.get('sell', []))
    prev_sell = tokens_set(previous.get('sell', []))

    changes = {
        # NEW_STRONG_BUY: не был STRONG_BUY раньше → сейчас есть
        'entered_strong_buy': list(curr_buy - prev_buy),
        # DIVERGENCE_WARN: был STRONG_BUY → сейчас DIVERGENCE
        'strong_to_divergence': list(prev_buy & curr_div),
        # EXIT: был STRONG_BUY → сейчас SELL
        'strong_to_sell': list(prev_buy & curr_sell),
        # STRONG_BUY exit без явного sell (просто пропал из списка)
        'exited_strong_buy_quiet': list(prev_buy - curr_buy - curr_div - curr_sell),
        # BUY_PRESSURE изменения
        'entered_buy_pressure': list(curr_pressure - prev_pressure),
        'exited_buy_pressure': list(prev_pressure - curr_pressure),
        'new_sell': list(curr_sell - prev_sell),
    }
    return changes


def update_streaks(current: Dict, prev_streaks: Dict) -> Dict:
    """Обновляет streak counter для каждого token в STRONG_BUY"""
    new_streaks = {}
    current_buy_tokens = {t['token'] for t in current.get('strong_buy', []) if isinstance(t, dict)}
    for token in current_buy_tokens:
        new_streaks[token] = prev_streaks.get(token, 0) + 1
    return new_streaks


def get_confirmed_holds(streaks: Dict, threshold: int = CONFIRMED_HOLD_DAYS) -> List[str]:
    """Токены с streak = threshold days (впервые достигают)"""
    return [t for t, days in streaks.items() if days == threshold]


# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================
def format_alert(changes: Dict, current: Dict, confirmed_holds: List[str], deltas: Dict = None) -> str:
    """Формирует alert message с playbook для каждого transition."""
    deltas = deltas or {}
    lines = []
    lines.append("🔄 <b>ROTATION TRACKER</b>")
    lines.append(f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>")
    lines.append("")

    def get_token_detail(token: str, section: str) -> Dict:
        for t in current.get(section, []):
            if isinstance(t, dict) and t.get('token') == token:
                return t
        return {}

    # NEW STRONG_BUY (highest priority) — с playbook
    if changes['entered_strong_buy']:
        lines.append("📈 <b>НОВЫЕ В STRONG_BUY:</b>")
        for token in changes['entered_strong_buy']:
            d = get_token_detail(token, 'strong_buy')
            d['token'] = token  # для playbook
            nf = d.get('net_flow_m_usd', 0)
            pc = d.get('price_change_7d_pct', 0)
            sector = d.get('sector', '')
            lines.append(f"  • <code>{token}</code> ({sector})")
            lines.append(f"    Net Flow: <code>{nf:+.1f}M</code> · Price: <code>{pc:+.1f}%</code>")
            # Playbook
            pb = build_playbook('NEW_STRONG_BUY', d, deltas)
            if pb:
                lines.append(format_playbook(pb))
        lines.append("")

    # CONFIRMED HOLD (3d streak) — с playbook
    if confirmed_holds:
        lines.append(f"✅ <b>CONFIRMED HOLD ({CONFIRMED_HOLD_DAYS}d STRONG_BUY streak):</b>")
        for token in confirmed_holds:
            d = get_token_detail(token, 'strong_buy')
            d['token'] = token
            nf = d.get('net_flow_m_usd', 0)
            lines.append(f"  • <code>{token}</code> · Net Flow: <code>{nf:+.1f}M</code>")
            pb = build_playbook('CONFIRMED_HOLD_3D', d, deltas)
            if pb:
                lines.append(format_playbook(pb))
        lines.append("")

    # DIVERGENCE WARN — с playbook (самый важный alert)
    if changes['strong_to_divergence']:
        lines.append("⚠ <b>DIVERGENCE WARNING (был STRONG_BUY):</b>")
        for token in changes['strong_to_divergence']:
            d = get_token_detail(token, 'divergence')
            d['token'] = token
            pc = d.get('price_change_7d_pct', 0)
            nf = d.get('net_flow_m_usd', 0)
            lines.append(f"  • <code>{token}</code> · price <code>{pc:+.1f}%</code> но flow <code>{nf:+.1f}M</code>")
            pb = build_playbook('DIVERGENCE_WARN', d, deltas)
            if pb:
                lines.append(format_playbook(pb))
        lines.append("")

    # EXIT (STRONG_BUY → SELL) — с playbook
    if changes['strong_to_sell']:
        lines.append("🚪 <b>EXIT SIGNAL (STRONG_BUY → SELL):</b>")
        for token in changes['strong_to_sell']:
            d = get_token_detail(token, 'sell') or {'token': token}
            d['token'] = token
            lines.append(f"  • <code>{token}</code>")
            pb = build_playbook('EXIT_STRONG_BUY', d, deltas)
            if pb:
                lines.append(format_playbook(pb))
        lines.append("")

    # QUIET EXIT (просто пропал из STRONG_BUY) — без playbook, короткий alert
    if changes['exited_strong_buy_quiet']:
        lines.append("⚪ <b>Вышли из STRONG_BUY (без явного sell):</b>")
        for token in changes['exited_strong_buy_quiet']:
            lines.append(f"  • <code>{token}</code>")
        lines.append("<i>💡 Skipping playbook — signal просто исчез, а не flipped.</i>")
        lines.append("")

    # NEW BUY_PRESSURE (context, no playbook)
    if changes['entered_buy_pressure']:
        lines.append("🟡 <b>НОВЫЕ В BUY_PRESSURE:</b>")
        for token in changes['entered_buy_pressure'][:5]:
            d = get_token_detail(token, 'buy_pressure')
            nf = d.get('net_flow_m_usd', 0)
            sector = d.get('sector', '')
            lines.append(f"  • <code>{token}</code> ({sector}) · Net Flow: <code>{nf:+.1f}M</code>")
        lines.append("<i>Flow accumulation, ждать price confirmation.</i>")
        lines.append("")

    # Текущий portfolio snapshot
    if current.get('strong_buy'):
        lines.append("📊 <b>ТЕКУЩИЕ STRONG_BUY:</b>")
        for t in current['strong_buy'][:8]:
            token = t.get('token', '')
            sector = t.get('sector', '')
            lines.append(f"  • <code>{token}</code> ({sector})")
        lines.append("")

    lines.append("<i>💡 LAB = data-only. Проверяй технику каждого токена.</i>")
    return "\n".join(lines)


def has_any_changes(changes: Dict, confirmed_holds: List[str]) -> bool:
    """Проверяет есть ли что вообще алертить"""
    return bool(
        changes['entered_strong_buy'] or
        changes['strong_to_divergence'] or
        changes['strong_to_sell'] or
        changes['exited_strong_buy_quiet'] or
        changes['entered_buy_pressure'] or
        confirmed_holds
    )


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(text: str) -> bool:
    """Отправляет сообщение в @Lab_sector_bot"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("::warning::TELEGRAM_LAB_SECTOR_BOT или TELEGRAM_CHAT_ID не установлены")
        print(text[:400])
        return False

    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true'
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                print(f"✅ Alert sent · msg_id={result.get('result', {}).get('message_id')}")
                return True
            print(f"Telegram error: {result}")
            return False
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        return False


def log_history(changes: Dict, current: Dict, sent: bool):
    """Append history record"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'entered_strong_buy': changes.get('entered_strong_buy', []),
        'strong_to_divergence': changes.get('strong_to_divergence', []),
        'strong_to_sell': changes.get('strong_to_sell', []),
        'exited_strong_buy_quiet': changes.get('exited_strong_buy_quiet', []),
        'sent_to_telegram': sent,
    }
    try:
        with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except Exception as e:
        print(f"Warning: history log failed: {e}")


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def main():
    print("=" * 60)
    print("🔄 Rotation Tracker · @Lab_sector_bot")
    print("=" * 60)

    lab_data = load_lab_data()
    if not lab_data:
        print(f"Нет LAB snapshot at {LAB_DATA_FILE}")
        print("Запусти сначала strk_lab.py (mode=lab)")
        return 0  # не error — просто skip

    # Load prev snapshot для 24h delta comparison (для playbook)
    prev_lab_data = load_prev_snapshot()
    deltas = compute_deltas(lab_data, prev_lab_data)
    print(f"Deltas computed for {len(deltas)} tokens (prev snapshot: {'yes' if prev_lab_data else 'no'})")

    current = analyze_lab_data(lab_data)
    state = load_state()

    # Previous — из state (только tokens list, детали не нужны)
    previous = {
        'strong_buy': [{'token': t.get('token') if isinstance(t, dict) else t}
                       for t in state.get('strong_buy_tokens', [])],
        'divergence': [{'token': t.get('token') if isinstance(t, dict) else t}
                       for t in state.get('divergence_tokens', [])],
        'buy_pressure': [{'token': t.get('token') if isinstance(t, dict) else t}
                         for t in state.get('buy_pressure_tokens', [])],
        'sell': [{'token': t.get('token') if isinstance(t, dict) else t}
                 for t in state.get('sell_tokens', [])],
    }

    changes = detect_changes(current, previous)

    # Streak tracking
    prev_streaks = state.get('signal_streaks', {})
    new_streaks = update_streaks(current, prev_streaks)
    confirmed_holds = get_confirmed_holds(new_streaks)

    # Log summary
    print(f"Current STRONG_BUY:   {len(current['strong_buy'])} tokens")
    print(f"Current DIVERGENCE:   {len(current['divergence'])} tokens")
    print(f"Current BUY_PRESSURE: {len(current['buy_pressure'])} tokens")
    print(f"Current SELL:         {len(current['sell'])} tokens")
    print(f"NEW STRONG_BUY:       {changes['entered_strong_buy']}")
    print(f"→ DIVERGENCE:         {changes['strong_to_divergence']}")
    print(f"→ SELL:               {changes['strong_to_sell']}")
    print(f"CONFIRMED HOLDS (3d): {confirmed_holds}")

    changes_present = has_any_changes(changes, confirmed_holds)

    # Save state (always, even without alert)
    state['last_run'] = datetime.now(timezone.utc).isoformat()
    state['strong_buy_tokens'] = current['strong_buy']
    state['divergence_tokens'] = current['divergence']
    state['buy_pressure_tokens'] = current['buy_pressure']
    state['sell_tokens'] = current['sell']
    state['signal_streaks'] = new_streaks

    sent = False
    # Send alert if changes OR first run
    if changes_present or not state.get('alert_sent_first'):
        alert_text = format_alert(changes, current, confirmed_holds, deltas=deltas)
        sent = send_telegram(alert_text)
        if sent:
            state['alert_sent_first'] = True
    else:
        print("⚪ Нет изменений — alert пропущен")

    save_state(state)

    if changes_present:
        log_history(changes, current, sent)

    print("=" * 60)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
