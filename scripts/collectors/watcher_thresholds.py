#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STRK ON-CHAIN WATCHER BOT v1.0
================================
Философия: TA-скелет (regime/structure/cycle) НЕ показал доказанного edge
на backtest'е (Sharpe 0.15 на 30 мес истории). On-chain/social слой —
единственная непроверенная гипотеза edge, потому что физически
не backtestable (нет исторических данных по alerts/trader_quality).

Этот бот НЕ принимает торговых решений. Он МОНИТОРИТ пороги и
говорит "зайди сделай LIQ/RUN" — финальное решение остаётся за
полным движком (Claude + MASTER_INSTRUCTION).

Источники (без Nansen — работает standalone):
- Starkscan: large transfers, stake trend, fees
- DefiLlama: TVL trend, MC/TVL
- OKX: price, funding rate, OI, regime metrics

Источники (требуют отдельный Nansen API ключ, опционально):
- HL top-25 positions, trader_quality, contrarian signal
  → см. NANSEN_ENABLED флаг ниже

Usage:
    python3 STRK_ONCHAIN_WATCHER.py           # запуск в цикле (для cron/systemd)
    python3 STRK_ONCHAIN_WATCHER.py --once    # один проход (для теста)
"""

import urllib.request
import json
import time
import sys
import argparse
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
import os

# ============================================================
# CONFIG
# ============================================================

CONFIG = {
    # API keys — TOLKO из env, никогда не хардкодить
    'starkscan_api_key': os.environ.get('STARKSCAN_API_KEY', ''),
    'starkscan_base': 'https://api.starkscan.co',
    'strk_token_address': '0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d',
    'vstrk_address': '0x0782f0ddca11d9950bc3220e35ac82cf868778edb67a5e58b39838544bc4cd0f',
    'staking_contract': '0x00ca1702e64c81d9a07b86bd2c540188d92a2c73cf5cc0e508d949015e7e84a7',
    'staking_selector': '0x226ffc5db8f68325947f4c4fcbea7117624ed26d4a1354693f63de203c453c8',
    'rpc_url': os.environ.get('STARKNET_RPC_URL', 'https://rpc.starknet.lava.build'),
    
    # Telegram — из env
    'telegram_bot_token': os.environ.get('TELEGRAM_BOT_TOKEN', ''),
    'telegram_chat_id': os.environ.get('TELEGRAM_CHAT_ID', ''),
    
    # Nansen — опционально
    'nansen_enabled': os.environ.get('NANSEN_ENABLED', 'false').lower() == 'true',
    'nansen_api_key': os.environ.get('NANSEN_API_KEY', ''),
    
    # Safety — обязательно
    'strict_no_trading': os.environ.get('STRICT_NO_TRADING', 'true').lower() == 'true',
    
    # Check interval
    'check_interval_seconds': int(os.environ.get('CHECK_INTERVAL_SECONDS', 1800)),
    
    # ============================================================
    # THRESHOLDS — калиброваны из истории RUN/LIQ этого проекта
    # ============================================================
    'thresholds': {
        # Whale transfers
        'large_transfer_strk': 5_000_000,       # >5M STRK = discord-alert уровень
        'mega_transfer_strk': 20_000_000,       # >20M STRK = критично, будит сразу
        
        # Stake
        'stake_accel_pct_per_day': 0.15,        # >0.15%/день = заметное ускорение
        'stake_reversal_threshold': -0.05,       # отрицательный тренд = разворот
        
        # TVL
        'tvl_7d_drop_alert': -5.0,              # -5% за 7д = alert (percent units, matches trend_7d)
        'tvl_7d_drop_critical': -8.0,           # -8% за 7д = критично
        'mc_tvl_thresholds': [0.7, 0.8, 0.9, 1.0, 1.1, 1.2],  # пересечения уровней
        
        # Funding (derivatives)
        'funding_extreme_pct': 15.0,             # ±15% годовых = extreme
        'funding_very_extreme_pct': 25.0,        # ±25% = very extreme
        'funding_flip_flop_hours': 24,           # флип за <24ч = шум, не сигнал
        
        # Price vs structure (нужен свежий VAL/VAH — считаем сами)
        'cushion_to_liq_alert_pct': 8.0,         # <8% до long-liq = alert
        'cushion_to_liq_critical_pct': 4.0,      # <4% = критично
        
        # Fees weekday health
        'fees_weekday_low': 3000,                # $ ниже нормы
        'fees_weekday_critical': 1500,
    },
    
    # State persistence (чтобы не спамить повторными алертами)
    'state_file': 'strk_watcher_state.json',
    'log_file': 'strk_watcher.log',
}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(CONFIG['log_file'], mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger('STRK_WATCHER')


# ============================================================
# STATE MANAGEMENT (avoid alert spam)
# ============================================================

def load_state() -> Dict:
    if os.path.exists(CONFIG['state_file']):
        try:
            with open(CONFIG['state_file'], 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        'last_stake': None,
        'last_tvl': None,
        'last_mc_tvl_bucket': None,
        'last_funding': None,
        'last_funding_change_time': None,
        'last_alert_times': {},  # alert_type -> timestamp, для cooldown
        'known_large_transfers': [],  # tx hashes уже увиденных
    }


def save_state(state: Dict):
    with open(CONFIG['state_file'], 'w') as f:
        json.dump(state, f, indent=2, default=str)


def should_alert(state: Dict, alert_key: str, cooldown_hours: float = 4) -> bool:
    """Anti-spam: не повторять один и тот же алерт чаще чем cooldown."""
    last = state['last_alert_times'].get(alert_key)
    if last is None:
        return True
    last_dt = datetime.fromisoformat(last)
    return (datetime.now(timezone.utc).replace(tzinfo=None) - last_dt) > timedelta(hours=cooldown_hours)


def mark_alerted(state: Dict, alert_key: str):
    state['last_alert_times'][alert_key] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


# ============================================================
# DATA FETCHERS
# ============================================================

def fetch_price_and_regime() -> Optional[Dict]:
    """Price + basic regime metrics from OKX."""
    try:
        r = urllib.request.Request(
            'https://www.okx.com/api/v5/market/ticker?instId=STRK-USDT',
            headers={'User-Agent': 'Mozilla/5.0'})
        tk = json.loads(urllib.request.urlopen(r, timeout=10).read())
        price = float(tk['data'][0]['last'])
        vol24h = float(tk['data'][0]['vol24h'])
        
        time.sleep(0.3)
        r2 = urllib.request.Request(
            'https://www.okx.com/api/v5/market/candles?instId=STRK-USDT&bar=4H&limit=100',
            headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r2, timeout=15).read())
        rows = list(reversed(data['data']))
        
        import numpy as np
        closes = np.array([float(x[4]) for x in rows])
        highs = np.array([float(x[2]) for x in rows])
        lows = np.array([float(x[3]) for x in rows])
        vols = np.array([float(x[5]) for x in rows])
        
        # Regime
        atr = np.mean(highs[-14:] - lows[-14:])
        sma20 = closes[-20:].mean()
        std20 = closes[-20:].std()
        bb_width = (std20 * 2 / sma20) * 100
        
        # Structure (simplified VP on last 90 bars if available)
        n = min(90, len(closes))
        lo, hi = lows[-n:].min(), highs[-n:].max()
        bins = np.linspace(lo, hi, 51)
        vol_at = np.zeros(50)
        for i in range(len(closes)-n, len(closes)):
            mask = (bins[:-1] <= highs[i]) & (bins[1:] >= lows[i])
            cnt = mask.sum()
            if cnt > 0:
                vol_at[mask] += vols[i] / cnt
        poc_idx = int(np.argmax(vol_at))
        poc = (bins[poc_idx] + bins[poc_idx+1]) / 2
        total_vol = vol_at.sum()
        target = total_vol * 0.7
        covered = vol_at[poc_idx]
        lo_i = hi_i = poc_idx
        while covered < target and (lo_i > 0 or hi_i < 49):
            left = vol_at[lo_i-1] if lo_i > 0 else 0
            right = vol_at[hi_i+1] if hi_i < 49 else 0
            if left >= right and lo_i > 0:
                lo_i -= 1; covered += left
            elif hi_i < 49:
                hi_i += 1; covered += right
            else:
                break
        val = bins[lo_i]
        vah = bins[hi_i+1] if hi_i+1 < 51 else bins[-1]
        
        return {
            'price': price,
            'vol24h_strk': vol24h,
            'bb_width': bb_width,
            'val': val, 'poc': poc, 'vah': vah,
        }
    except Exception as e:
        logger.error(f"fetch_price_and_regime failed: {e}")
        return None


def fetch_funding() -> Optional[float]:
    """Funding rate annualized, OKX swap."""
    try:
        r = urllib.request.Request(
            'https://www.okx.com/api/v5/public/funding-rate?instId=STRK-USDT-SWAP',
            headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=10).read())
        rate = float(data['data'][0]['fundingRate'])
        # OKX funding period is 8h, annualize: rate * 3 * 365
        annualized = rate * 3 * 365 * 100
        return annualized
    except Exception as e:
        logger.error(f"fetch_funding failed: {e}")
        return None


def fetch_stake() -> Optional[float]:
    """Total staked STRK via Starknet RPC."""
    try:
        payload = {
            'jsonrpc': '2.0', 'method': 'starknet_call',
            'params': {
                'request': {
                    'contract_address': CONFIG['staking_contract'],
                    'entry_point_selector': CONFIG['staking_selector'],
                    'calldata': []
                },
                'block_id': 'latest'
            }, 'id': 1
        }
        r = urllib.request.Request(
            CONFIG['rpc_url'], data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        resp = json.loads(urllib.request.urlopen(r, timeout=15).read())
        return int(resp['result'][0], 16) / 1e18
    except Exception as e:
        logger.error(f"fetch_stake failed: {e}")
        return None


def fetch_tvl() -> Optional[Dict]:
    """TVL + trends from DefiLlama."""
    try:
        r = urllib.request.Request(
            'https://api.llama.fi/v2/historicalChainTvl/Starknet',
            headers={'User-Agent': 'Mozilla/5.0'})
        data = json.loads(urllib.request.urlopen(r, timeout=15).read())
        today = data[-1]['tvl']
        day7 = data[-8]['tvl'] if len(data) > 8 else today
        day30 = data[-31]['tvl'] if len(data) > 31 else today
        return {
            'tvl': today,
            'trend_7d': (today / day7 - 1) * 100 if day7 > 0 else 0,
            'trend_30d': (today / day30 - 1) * 100 if day30 > 0 else 0,
        }
    except Exception as e:
        logger.error(f"fetch_tvl failed: {e}")
        return None


def fetch_large_transfers(since_minutes: int = 35) -> List[Dict]:
    """Recent large STRK transfers (whale alert equivalent)."""
    try:
        url = f"{CONFIG['starkscan_base']}/api/v1/SN_MAIN/token/{CONFIG['strk_token_address']}/transfers?limit=100"
        r = urllib.request.Request(url, headers={
            'X-Starkscan-Api-Key': CONFIG['starkscan_api_key'],
            'User-Agent': 'Mozilla/5.0'})
        resp = json.loads(urllib.request.urlopen(r, timeout=15).read())
        items = resp.get('items', [])
        
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=since_minutes)
        large = []
        for it in items:
            try:
                ts = datetime.fromisoformat(it['timestampIso'].replace('Z', '+00:00')).replace(tzinfo=None)
                if ts < cutoff:
                    continue
                amount = int(it['amount']) / 1e18
                if amount >= CONFIG['thresholds']['large_transfer_strk']:
                    large.append({
                        'tx_hash': it['txHash'],
                        'amount_strk': amount,
                        'from': it['fromAddress'],
                        'to': it['toAddress'],
                        'timestamp': it['timestampIso'],
                    })
            except (KeyError, ValueError, TypeError):
                continue
        return large
    except Exception as e:
        logger.error(f"fetch_large_transfers failed: {e}")
        return []


def fetch_fees_last_weekday() -> Optional[Dict]:
    """Last complete weekday's L2 fees."""
    try:
        url = f"{CONFIG['starkscan_base']}/api/v1/SN_MAIN/metrics/network?window=30d"
        r = urllib.request.Request(url, headers={
            'X-Starkscan-Api-Key': CONFIG['starkscan_api_key'],
            'User-Agent': 'Mozilla/5.0'})
        resp = json.loads(urllib.request.urlopen(r, timeout=15).read())
        items = resp.get('items', [])
        
        # need current price for USD conversion
        price_data = fetch_price_and_regime()
        px = price_data['price'] if price_data else 0.025
        
        for it in reversed(items):
            interval = it.get('blockIntervalSecondsSum') or 0
            if interval != 86400:
                continue
            date_str = it['bucketStartIso'][:10]
            date = datetime.strptime(date_str, '%Y-%m-%d')
            if date.weekday() >= 5:  # weekend, skip
                continue
            fee_raw = it.get('actualFeeFriTotalRaw')
            if fee_raw:
                fee_usd = int(fee_raw) / 1e18 * px
                return {'date': date_str, 'fee_usd': fee_usd}
        return None
    except Exception as e:
        logger.error(f"fetch_fees failed: {e}")
        return None


# ============================================================
# CHECK LOGIC — thresholds → alerts
# ============================================================

def check_all_thresholds(state: Dict) -> List[Dict]:
    """Run all checks, return list of alerts to send."""
    alerts = []
    th = CONFIG['thresholds']
    
    # --- Price + structure + regime ---
    pr = fetch_price_and_regime()
    if pr:
        price = pr['price']
        val, vah = pr['val'], pr['vah']
        
        # Approximate long-liq cluster (empirical -12% from recent low, refine as needed)
        # NOTE: this is a rough proxy; real HL liq data requires Nansen
        approx_liq = price * 0.88
        cushion_pct = (price - approx_liq) / approx_liq * 100
        
        if cushion_pct < th['cushion_to_liq_critical_pct']:
            key = 'cushion_critical'
            if should_alert(state, key, cooldown_hours=2):
                alerts.append({
                    'level': 'CRITICAL',
                    'type': 'PRICE',
                    'msg': f"🔴 Цена ${price:.5f} КРИТИЧЕСКИ близко к liq-зоне (~{cushion_pct:.1f}% cushion). "
                           f"РЕКОМЕНДУЕТСЯ LIQ СЕЙЧАС."
                })
                mark_alerted(state, key)
        elif cushion_pct < th['cushion_to_liq_alert_pct']:
            key = 'cushion_alert'
            if should_alert(state, key, cooldown_hours=6):
                alerts.append({
                    'level': 'ALERT',
                    'type': 'PRICE',
                    'msg': f"🟠 Цена ${price:.5f} приближается к опасной зоне (~{cushion_pct:.1f}% cushion). "
                           f"Стоит сделать LIQ."
                })
                mark_alerted(state, key)
        
        # VAL/VAH crossing
        if price < val:
            key = 'below_val'
            if should_alert(state, key, cooldown_hours=6):
                alerts.append({
                    'level': 'INFO',
                    'type': 'STRUCTURE',
                    'msg': f"📉 Цена ${price:.5f} ниже VAL ${val:.5f} — структурный сдвиг. LIQ рекомендован."
                })
                mark_alerted(state, key)
        elif price > vah:
            key = 'above_vah'
            if should_alert(state, key, cooldown_hours=6):
                alerts.append({
                    'level': 'INFO',
                    'type': 'STRUCTURE',
                    'msg': f"📈 Цена ${price:.5f} выше VAH ${vah:.5f} — потенциальный breakout. LIQ или RUN рекомендован."
                })
                mark_alerted(state, key)
    
    # --- Funding ---
    funding = fetch_funding()
    if funding is not None:
        prev_funding = state.get('last_funding')
        prev_time = state.get('last_funding_change_time')
        
        if abs(funding) > th['funding_very_extreme_pct']:
            key = 'funding_very_extreme'
            if should_alert(state, key, cooldown_hours=4):
                direction = "шорты платят лонгам" if funding < 0 else "лонги платят шортам"
                alerts.append({
                    'level': 'ALERT',
                    'type': 'FUNDING',
                    'msg': f"⚡ Funding {funding:+.2f}% годовых — ОЧЕНЬ экстремально ({direction}). "
                           f"Проверь HL top-25 на contrarian сигнал. LIQ рекомендован."
                })
                mark_alerted(state, key)
        
        # Flip detection (sign change within short window)
        if prev_funding is not None and (prev_funding * funding < 0):  # sign flip
            if prev_time:
                hours_since = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(prev_time)).total_seconds() / 3600
                if hours_since < th['funding_flip_flop_hours']:
                    key = 'funding_flipflop'
                    if should_alert(state, key, cooldown_hours=12):
                        alerts.append({
                            'level': 'INFO',
                            'type': 'FUNDING',
                            'msg': f"🔄 Funding флип-флоп: {prev_funding:+.2f}% → {funding:+.2f}% за {hours_since:.0f}ч. "
                                   f"Не интерпретировать как тренд — это шум."
                        })
                        mark_alerted(state, key)
        
        state['last_funding'] = funding
        state['last_funding_change_time'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    
    # --- Stake ---
    stake = fetch_stake()
    if stake is not None:
        prev_stake = state.get('last_stake')
        if prev_stake:
            change_pct = (stake / prev_stake - 1) * 100
            hours_elapsed = CONFIG['check_interval_seconds'] / 3600
            daily_rate = change_pct * (24 / max(hours_elapsed, 0.1))
            
            if daily_rate < th['stake_reversal_threshold']:
                key = 'stake_reversal'
                if should_alert(state, key, cooldown_hours=12):
                    alerts.append({
                        'level': 'ALERT',
                        'type': 'STAKE',
                        'msg': f"⚠️ Стейкинг РАЗВОРОТ: {daily_rate:+.3f}%/день. Это впервые за проект — "
                               f"стейк обычно только растёт. RUN рекомендован для полного анализа."
                    })
                    mark_alerted(state, key)
            elif abs(daily_rate) > th['stake_accel_pct_per_day']:
                key = 'stake_accel'
                if should_alert(state, key, cooldown_hours=12):
                    alerts.append({
                        'level': 'INFO',
                        'type': 'STAKE',
                        'msg': f"📊 Стейкинг ускорился: {daily_rate:+.3f}%/день (обычно медленнее). "
                               f"Возможно, стоит сделать LIQ."
                    })
                    mark_alerted(state, key)
        state['last_stake'] = stake
    
    # --- TVL + MC/TVL ---
    tvl_data = fetch_tvl()
    if tvl_data and pr:
        tvl = tvl_data['tvl']
        circ_supply = 6_746_937_561  # обновить при необходимости
        mc = pr['price'] * circ_supply
        mc_tvl = mc / tvl
        
        if tvl_data['trend_7d'] < th['tvl_7d_drop_critical']:
            key = 'tvl_critical'
            if should_alert(state, key, cooldown_hours=12):
                alerts.append({
                    'level': 'CRITICAL',
                    'type': 'TVL',
                    'msg': f"🔴 TVL упал {tvl_data['trend_7d']:.2f}% за 7д — критично. RUN рекомендован."
                })
                mark_alerted(state, key)
        elif tvl_data['trend_7d'] < th['tvl_7d_drop_alert']:
            key = 'tvl_alert'
            if should_alert(state, key, cooldown_hours=24):
                alerts.append({
                    'level': 'ALERT',
                    'type': 'TVL',
                    'msg': f"🟠 TVL упал {tvl_data['trend_7d']:.2f}% за 7д. Стоит проверить в LIQ."
                })
                mark_alerted(state, key)
        
        # MC/TVL threshold crossing
        prev_bucket = state.get('last_mc_tvl_bucket')
        thresholds = th['mc_tvl_thresholds']
        current_bucket = None
        for t in thresholds:
            if mc_tvl < t:
                current_bucket = t
                break
        else:
            current_bucket = max(thresholds) + 0.1
        
        if prev_bucket is not None and current_bucket != prev_bucket:
            key = 'mc_tvl_cross'
            if should_alert(state, key, cooldown_hours=24):
                direction = "вверх" if mc_tvl > prev_bucket else "вниз"
                alerts.append({
                    'level': 'INFO',
                    'type': 'FUNDAMENTAL',
                    'msg': f"📐 MC/TVL пересёк уровень {direction}: сейчас {mc_tvl:.3f}. "
                           f"Fundamental valuation signal сдвинулся — стоит пересчитать в RUN."
                })
                mark_alerted(state, key)
        state['last_mc_tvl_bucket'] = current_bucket
    
    # --- Large transfers ---
    check_minutes = CONFIG['check_interval_seconds'] // 60 + 5
    transfers = fetch_large_transfers(since_minutes=check_minutes)
    known = set(state.get('known_large_transfers', []))
    for t in transfers:
        if t['tx_hash'] in known:
            continue
        known.add(t['tx_hash'])
        amount = t['amount_strk']
        if amount >= th['mega_transfer_strk']:
            alerts.append({
                'level': 'CRITICAL',
                'type': 'WHALE',
                'msg': f"🐋 МЕГА-перевод: {amount/1e6:.1f}M STRK. "
                       f"tx: {t['tx_hash'][:10]}... RUN рекомендован немедленно."
            })
        elif amount >= th['large_transfer_strk']:
            alerts.append({
                'level': 'ALERT',
                'type': 'WHALE',
                'msg': f"🐋 Крупный перевод: {amount/1e6:.2f}M STRK. "
                       f"tx: {t['tx_hash'][:10]}... Стоит проверить в LIQ."
            })
    state['known_large_transfers'] = list(known)[-500:]  # keep last 500
    
    # --- Fees weekday health ---
    fees = fetch_fees_last_weekday()
    if fees:
        if fees['fee_usd'] < th['fees_weekday_critical']:
            key = 'fees_critical'
            if should_alert(state, key, cooldown_hours=48):
                alerts.append({
                    'level': 'CRITICAL',
                    'type': 'USAGE',
                    'msg': f"🔴 L2 fees рухнули: ${fees['fee_usd']:.0f} ({fees['date']}, будний). "
                           f"Использование сети падает. RUN для полного анализа."
                })
                mark_alerted(state, key)
        elif fees['fee_usd'] < th['fees_weekday_low']:
            key = 'fees_low'
            if should_alert(state, key, cooldown_hours=48):
                alerts.append({
                    'level': 'INFO',
                    'type': 'USAGE',
                    'msg': f"🟡 L2 fees ниже нормы: ${fees['fee_usd']:.0f} ({fees['date']}, будний)."
                })
                mark_alerted(state, key)
    
    return alerts


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message: str):
    token = CONFIG['telegram_bot_token']
    chat_id = CONFIG['telegram_chat_id']
    if not token or token == 'YOUR_BOT_TOKEN':
        logger.warning("Telegram не настроен — сообщение только в лог")
        logger.info(f"[WOULD SEND] {message}")
        return
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}).encode()
        r = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        resp = urllib.request.urlopen(r, timeout=10)
        if resp.status == 200:
            logger.info("Telegram sent OK")
        else:
            logger.error(f"Telegram error: {resp.status}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


# ============================================================
# MAIN LOOP
# ============================================================

def run_check():
    """Single check cycle."""
    logger.info("=" * 50)
    logger.info("STRK Watcher: запуск проверки")
    
    state = load_state()
    alerts = check_all_thresholds(state)
    save_state(state)
    
    if not alerts:
        logger.info("Нет алертов в этом цикле")
        return
    
    # Sort by severity
    severity_order = {'CRITICAL': 0, 'ALERT': 1, 'INFO': 2}
    alerts.sort(key=lambda a: severity_order.get(a['level'], 3))
    
    for alert in alerts:
        logger.info(f"[{alert['level']}] {alert['type']}: {alert['msg']}")
    
    # Compose combined message if multiple alerts
    if len(alerts) == 1:
        msg = f"<b>STRK WATCHER</b>\n\n{alerts[0]['msg']}"
    else:
        header = f"<b>STRK WATCHER — {len(alerts)} событий</b>\n\n"
        body = "\n\n".join(f"{a['msg']}" for a in alerts)
        msg = header + body
    
    send_telegram(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Run single check and exit')
    args = parser.parse_args()
    
    if args.once:
        run_check()
        return
    
    logger.info(f"STRK Watcher Bot started. Interval: {CONFIG['check_interval_seconds']}s")
    while True:
        try:
            run_check()
        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception as e:
            logger.error(f"Unhandled error: {e}")
        time.sleep(CONFIG['check_interval_seconds'])


if __name__ == '__main__':
    main()
