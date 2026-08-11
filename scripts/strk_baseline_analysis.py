#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strk_baseline_analysis.py — reference baseline для STRK.

Качает 1 год STRK OHLCV через ccxt, тестирует простые baseline strategies
на РЕАЛЬНОЙ истории, генерирует HTML отчёт с метриками для сравнения
с forward-test через 30-60 дней.

Baseline strategies (все на 1h OHLCV):
  1. Buy & Hold — passive benchmark
  2. RSI<30 entry, exit +5% or -3% or 72h (mean reversion long)
  3. Breakout above 14d high, exit +5% or -3% or 72h (trend follow)
  4. Volume spike (>2x avg) + RSI<40, exit 72h hold (capitulation catch)
  5. Weekly DCA — passive comparison
  6. RSI>70 short, cover -5% or +3% or 72h (mean reversion short)

Regime analysis:
  - Trending vs Range vs Whipsaw days
  - Daily volatility calibration (для правильных stop/take)

Output:
  - data/reports/strk_baseline_{date}.html
  - data/reports/strk_baseline_latest.html
  - data/reports/strk_baseline_results.json (для программного доступа)
  - Telegram summary + HTML документ

Не требует vectorbt — только pandas + ccxt.
"""
import os
import sys
import json
import time
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent
REPORTS_DIR = SCRIPT_DIR / 'data' / 'reports'
CACHE_DIR = SCRIPT_DIR / 'data' / 'cache'
OHLCV_CACHE = CACHE_DIR / 'strk_baseline_ohlcv.parquet'

# ============================================================
# CONFIG
# ============================================================
SYMBOL = 'STRK/USDT'
# Fallback list — US-friendly exchanges FIRST (GH Actions runners US-based)
# Binance blocks US IP with 451 → пробуем последним для полноты
EXCHANGES_TO_TRY = ['bybit', 'okx', 'kucoin', 'gate', 'mexc', 'kraken', 'binance']
TIMEFRAME = '1h'
HISTORY_DAYS = 365

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
BREAKOUT_LOOKBACK_HOURS = 14 * 24  # 14 дней в часах
VOL_SPIKE_RATIO = 2.0
VOL_AVG_WINDOW_HOURS = 30 * 24  # 30 дней
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -3.0
MAX_HOLD_HOURS = 72
CACHE_MAX_AGE_HOURS = 12


# ============================================================
# DATA FETCH
# ============================================================
def _fetch_from_coingecko(days):
    """CoinGecko API — гарантированно правильный Starknet STRK.
    Free tier, no auth. Returns daily OHLCV for 365+ days, hourly for 90 days.
    Contract: 0xCa14007Eff0dB1f8135f4C25B34De49AB0d42766 (Starknet).
    """
    url = f"https://api.coingecko.com/api/v3/coins/starknet/market_chart?vs_currency=usd&days={days}"
    logger.info(f"  CoinGecko: fetching {days}d for coin_id='starknet'")

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'strk-baseline/1.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise Exception(f"CoinGecko API: {e}")

    prices = data.get('prices', [])
    volumes = data.get('total_volumes', [])
    if not prices:
        raise Exception("CoinGecko returned no prices")

    # Собираем dataframe. CoinGecko прайсы приходят как [timestamp_ms, price]
    records = []
    vol_by_ts = {v[0]: v[1] for v in volumes}
    for ts_ms, price in prices:
        records.append({
            'ts': ts_ms,
            'close': price,
            'volume': vol_by_ts.get(ts_ms, 0),
        })

    df = pd.DataFrame(records)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df = df.set_index('ts').sort_index().drop_duplicates()

    # CoinGecko не даёт OHLC для free — приближаем из close-to-close
    df['open'] = df['close'].shift(1).fillna(df['close'])
    df['high'] = df[['open', 'close']].max(axis=1) * 1.005  # ~ +0.5% intraday high
    df['low'] = df[['open', 'close']].min(axis=1) * 0.995   # ~ -0.5% intraday low
    df = df[['open', 'high', 'low', 'close', 'volume']]

    return df


def _fetch_from_exchange(exchange_id, symbol, timeframe, days):
    """Single-exchange fetch. Returns df or raises.
    ВАЖНО: проверяет что цена < $2 — иначе это НЕ Starknet STRK
    (KuCoin listed some other STRK token that pumped to $0.20+).
    """
    ex_class = getattr(ccxt, exchange_id)
    ex = ex_class({'enableRateLimit': True, 'timeout': 30000})

    # Verify exchange lists this symbol
    try:
        ex.load_markets()
        if symbol not in ex.symbols:
            alt_symbols = [
                symbol.replace('/', '-'),
                symbol.replace('/', ''),
            ]
            actual = None
            for alt in alt_symbols:
                if alt in ex.symbols:
                    actual = alt
                    break
            if actual:
                symbol = actual
                logger.info(f"  Using alt symbol: {symbol}")
            else:
                raise Exception(f"symbol {symbol} not listed on {exchange_id}")
    except ccxt.BadSymbol:
        raise Exception(f"symbol {symbol} not on {exchange_id}")

    ms_per_bar = ex.parse_timeframe(timeframe) * 1000
    since = ex.milliseconds() - days * 24 * 3600 * 1000

    all_bars = []
    max_iter = 50
    while since < ex.milliseconds() and max_iter > 0:
        max_iter -= 1
        bars = ex.fetch_ohlcv(symbol, timeframe, since, limit=1000)
        if not bars:
            break
        all_bars.extend(bars)
        since = bars[-1][0] + ms_per_bar
        if len(bars) < 1000:
            break
        time.sleep(0.3)

    if not all_bars:
        raise Exception(f"no bars from {exchange_id}")

    df = pd.DataFrame(all_bars, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df = df.set_index('ts').drop_duplicates().sort_index()

    # ==== SANITY CHECK: is this really Starknet STRK? ====
    # Starknet STRK: ATH ~$4.5, current ~$0.02-0.15 range.
    # Other STRK tokens: Strike Protocol pumped to $0.20+, Strike Finance to $1+.
    current = float(df['close'].iloc[-1])
    if current > 2.0:
        raise Exception(f"price ${current:.4f} > $2 — likely NOT Starknet STRK (wrong token listed on {exchange_id})")

    return df


def fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, days=HISTORY_DAYS, force_refresh=False):
    """Download OHLCV. Primary source: CoinGecko (guaranteed Starknet).
    Fallback: ccxt exchanges with price verification (< $2)."""
    # Check cache
    if not force_refresh and OHLCV_CACHE.exists():
        try:
            df = pd.read_parquet(OHLCV_CACHE)
            latest = df.index[-1]
            if latest.tzinfo is None:
                latest = latest.tz_localize('UTC')
            age_h = (datetime.now(timezone.utc) - latest.to_pydatetime()).total_seconds() / 3600
            # Sanity: cached data должна быть тот же STRK
            cached_price = float(df['close'].iloc[-1])
            if age_h < CACHE_MAX_AGE_HOURS and len(df) > 100 and cached_price < 2.0:
                logger.info(f"Using cached OHLCV: {len(df)} bars (age {age_h:.1f}h)")
                return df, 'cache'
            elif cached_price >= 2.0:
                logger.warning(f"Cached price ${cached_price:.4f} — wrong STRK, discarding cache")
        except Exception as e:
            logger.warning(f"Cache read failed: {e}")

    # === PRIMARY: CoinGecko (guaranteed Starknet) ===
    logger.info("Trying CoinGecko (primary source for Starknet STRK)...")
    try:
        df = _fetch_from_coingecko(days)
        if df is not None and len(df) > 30:
            current = float(df['close'].iloc[-1])
            logger.info(f"✓ CoinGecko: {len(df)} bars · current ${current:.4f}")
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                df.to_parquet(OHLCV_CACHE)
            except Exception as e:
                logger.warning(f"Cache save failed: {e}")
            return df, 'coingecko'
    except Exception as e:
        logger.warning(f"  CoinGecko failed: {str(e)[:200]}")

    # === FALLBACK: ccxt exchanges (with price sanity check) ===
    if not HAS_CCXT:
        logger.error("CoinGecko failed and ccxt not installed")
        return None, None

    last_error = None
    for exchange_id in EXCHANGES_TO_TRY:
        logger.info(f"Trying {exchange_id}...")
        try:
            df = _fetch_from_exchange(exchange_id, symbol, timeframe, days)
            if df is not None and len(df) > 100:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                try:
                    df.to_parquet(OHLCV_CACHE)
                except Exception as e:
                    logger.warning(f"Cache save failed: {e}")
                current = float(df['close'].iloc[-1])
                logger.info(f"✓ Fetched {len(df)} bars from {exchange_id} · current ${current:.4f}")
                return df, exchange_id
        except Exception as e:
            last_error = str(e)[:200]
            logger.warning(f"  {exchange_id} failed: {last_error}")
            continue

    logger.error(f"All sources failed. Last error: {last_error}")
    return None, None


# ============================================================
# INDICATORS
# ============================================================
def calc_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss.replace(0, 0.0001)
    return 100 - (100 / (1 + rs))


def calc_vol_ratio(volume, avg_window):
    avg = volume.rolling(avg_window).mean()
    return volume / avg.replace(0, np.nan)


def calc_rolling_high(prices, window):
    return prices.rolling(window).max()


# ============================================================
# BACKTEST ENGINE (simple, no vectorbt required)
# ============================================================
def backtest_strategy(df, entry_signal, exit_after_hours=MAX_HOLD_HOURS,
                     take_profit_pct=TAKE_PROFIT_PCT, stop_loss_pct=STOP_LOSS_PCT,
                     direction='long'):
    """Simple backtest — long or short strategy.
    entry_signal: pd.Series (bool) — when to enter
    exit_after_hours: max hold time
    take_profit_pct: exit at +X%
    stop_loss_pct: exit at -X% (negative number for long)
    direction: 'long' or 'short'
    """
    trades = []
    in_position = False
    entry_price = 0
    entry_time = None
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    idx = df.index
    entry_arr = entry_signal.reindex(df.index).fillna(False).values

    for i in range(len(df)):
        ts = idx[i]

        if not in_position and entry_arr[i]:
            in_position = True
            entry_price = closes[i]
            entry_time = ts
            continue

        if in_position:
            hours_held = (ts - entry_time).total_seconds() / 3600
            # Use high/low for intra-bar TP/SL check
            if direction == 'long':
                # TP triggered if bar high touched +take_profit_pct
                tp_price = entry_price * (1 + take_profit_pct / 100)
                sl_price = entry_price * (1 + stop_loss_pct / 100)
                if highs[i] >= tp_price:
                    trades.append({
                        'entry_time': entry_time, 'exit_time': ts,
                        'entry_price': entry_price, 'exit_price': tp_price,
                        'pnl_pct': take_profit_pct, 'hours_held': hours_held,
                        'exit_reason': 'TP', 'direction': direction,
                    })
                    in_position = False
                elif lows[i] <= sl_price:
                    trades.append({
                        'entry_time': entry_time, 'exit_time': ts,
                        'entry_price': entry_price, 'exit_price': sl_price,
                        'pnl_pct': stop_loss_pct, 'hours_held': hours_held,
                        'exit_reason': 'SL', 'direction': direction,
                    })
                    in_position = False
                elif hours_held >= exit_after_hours:
                    pnl = (closes[i] - entry_price) / entry_price * 100
                    trades.append({
                        'entry_time': entry_time, 'exit_time': ts,
                        'entry_price': entry_price, 'exit_price': closes[i],
                        'pnl_pct': pnl, 'hours_held': hours_held,
                        'exit_reason': 'TIME', 'direction': direction,
                    })
                    in_position = False
            else:  # short
                tp_price = entry_price * (1 - take_profit_pct / 100)
                sl_price = entry_price * (1 - stop_loss_pct / 100)
                if lows[i] <= tp_price:
                    trades.append({
                        'entry_time': entry_time, 'exit_time': ts,
                        'entry_price': entry_price, 'exit_price': tp_price,
                        'pnl_pct': take_profit_pct, 'hours_held': hours_held,
                        'exit_reason': 'TP', 'direction': direction,
                    })
                    in_position = False
                elif highs[i] >= sl_price:
                    trades.append({
                        'entry_time': entry_time, 'exit_time': ts,
                        'entry_price': entry_price, 'exit_price': sl_price,
                        'pnl_pct': stop_loss_pct, 'hours_held': hours_held,
                        'exit_reason': 'SL', 'direction': direction,
                    })
                    in_position = False
                elif hours_held >= exit_after_hours:
                    pnl = (entry_price - closes[i]) / entry_price * 100
                    trades.append({
                        'entry_time': entry_time, 'exit_time': ts,
                        'entry_price': entry_price, 'exit_price': closes[i],
                        'pnl_pct': pnl, 'hours_held': hours_held,
                        'exit_reason': 'TIME', 'direction': direction,
                    })
                    in_position = False

    return pd.DataFrame(trades)


def compute_stats(trades_df, buy_hold_return_pct=None):
    """Compute backtest stats — win rate, PnL, Sharpe, drawdown."""
    if len(trades_df) == 0:
        return {
            'n_trades': 0, 'win_rate_pct': None, 'total_return_pct': None,
            'avg_pnl_pct': None, 'best_trade_pct': None, 'worst_trade_pct': None,
            'sharpe': None, 'max_drawdown_pct': None,
            'edge_vs_bh_pct': None, 'avg_hold_hours': None,
        }

    pnls = trades_df['pnl_pct'].values
    wins = (pnls > 0).sum()
    win_rate = wins / len(pnls) * 100

    # Compound total return
    cum_return = 1.0
    equity = [1.0]
    for pnl in pnls:
        cum_return *= (1 + pnl / 100)
        equity.append(cum_return)
    total_return = (cum_return - 1) * 100

    # Sharpe (rough) — mean/std of trade returns
    std_pnl = np.std(pnls)
    sharpe = np.mean(pnls) / std_pnl * np.sqrt(len(pnls)) if std_pnl > 0 else 0

    # Max drawdown (peak-to-trough on equity curve)
    equity_arr = np.array(equity)
    running_max = np.maximum.accumulate(equity_arr)
    drawdowns = (equity_arr - running_max) / running_max * 100
    max_dd = drawdowns.min()

    edge = (total_return - buy_hold_return_pct) if buy_hold_return_pct is not None else None

    return {
        'n_trades': len(trades_df),
        'win_rate_pct': float(win_rate),
        'total_return_pct': float(total_return),
        'avg_pnl_pct': float(np.mean(pnls)),
        'best_trade_pct': float(pnls.max()),
        'worst_trade_pct': float(pnls.min()),
        'sharpe': float(sharpe),
        'max_drawdown_pct': float(max_dd),
        'buy_hold_return_pct': float(buy_hold_return_pct) if buy_hold_return_pct is not None else None,
        'edge_vs_bh_pct': float(edge) if edge is not None else None,
        'avg_hold_hours': float(trades_df['hours_held'].mean()),
    }


# ============================================================
# STRATEGIES
# ============================================================
def strategy_rsi_oversold(df, rsi_col='rsi_14', threshold=RSI_OVERSOLD):
    """Buy on RSI cross below threshold."""
    below = df[rsi_col] < threshold
    prev_above = df[rsi_col].shift(1) >= threshold
    return below & prev_above


def strategy_breakout(df, high_col='high_14d'):
    """Buy on breakout above 14d high (close > prev 14d high)."""
    curr_above = df['close'] > df[high_col].shift(1)
    prev_below = df['close'].shift(1) <= df[high_col].shift(1)
    return curr_above & prev_below


def strategy_volume_spike(df, vol_col='vol_ratio', rsi_col='rsi_14',
                         vol_threshold=VOL_SPIKE_RATIO, rsi_threshold=40):
    """Volume spike + RSI<40 → capitulation catch."""
    spike = df[vol_col] > vol_threshold
    oversold = df[rsi_col] < rsi_threshold
    entry = spike & oversold
    return entry & ~entry.shift(1, fill_value=False)


def strategy_rsi_overbought_short(df, rsi_col='rsi_14', threshold=RSI_OVERBOUGHT):
    """Short on RSI cross above threshold."""
    above = df[rsi_col] > threshold
    prev_below = df[rsi_col].shift(1) <= threshold
    return above & prev_below


def strategy_dca_weekly(df):
    """DCA каждый понедельник 00:00 UTC."""
    result = (df.index.dayofweek == 0) & (df.index.hour == 0)
    return pd.Series(result, index=df.index)


# ============================================================
# REGIME ANALYSIS
# ============================================================
def analyze_regimes(df):
    """Classify days as trending / range / whipsaw / drift.
    Fixed: не делаем dropna() который делал regime_counts={} на incomplete data."""
    # Resample to daily; НЕ dropna — используем ffill для гарантии non-empty
    daily = df.resample('1D').agg({
        'close': 'last', 'high': 'max', 'low': 'min', 'volume': 'sum'
    }).ffill()

    # Убираем только строки где close всё ещё NaN (первые дни)
    daily = daily[daily['close'].notna()]

    if len(daily) < 14:
        # Slишком мало данных для regime analysis
        return {
            'daily': daily,
            'regime_counts': {'unknown': len(daily)},
            'avg_daily_range_pct': 0.0,
            'avg_daily_vol_pct': 0.0,
            'total_days': int(len(daily)),
            'std_daily_range_pct': 0.0,
        }

    daily['slope_3d'] = (daily['close'] / daily['close'].shift(3) - 1) * 100
    daily['slope_7d'] = (daily['close'] / daily['close'].shift(7) - 1) * 100
    daily['slope_14d'] = (daily['close'] / daily['close'].shift(14) - 1) * 100
    daily['range_pct'] = (daily['high'] - daily['low']) / daily['close'] * 100
    daily['vol_daily'] = daily['range_pct'].rolling(7).mean()

    def classify(row):
        if pd.isna(row['slope_7d']):
            return 'unknown'
        if abs(row['slope_7d']) >= 5:
            return 'trending_up' if row['slope_7d'] > 0 else 'trending_down'
        if not pd.isna(row['slope_14d']) and abs(row['slope_14d']) < 3 and abs(row['slope_3d']) >= 5:
            return 'whipsaw'
        if abs(row['slope_7d']) < 2:
            return 'range'
        return 'drift'

    daily['regime'] = daily.apply(classify, axis=1)
    counts = daily['regime'].value_counts().to_dict()

    # Guarantee non-empty (защита от edge case)
    if not counts:
        counts = {'unknown': len(daily)}

    return {
        'daily': daily,
        'regime_counts': counts,
        'avg_daily_range_pct': float(daily['range_pct'].mean()) if not daily['range_pct'].isna().all() else 0.0,
        'avg_daily_vol_pct': float(daily['vol_daily'].mean()) if not daily['vol_daily'].isna().all() else 0.0,
        'total_days': int(len(daily)),
        'std_daily_range_pct': float(daily['range_pct'].std()) if len(daily) > 1 else 0.0,
    }


# ============================================================
# HTML REPORT
# ============================================================
def build_html_report(df, strategies_results, regime, current_price, buy_hold_return, source_exchange='binance'):
    now = datetime.now(timezone.utc)
    now_str = now.strftime('%Y-%m-%d %H:%M UTC')
    date_range = f"{df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}"
    n_bars = len(df)
    period_days = (df.index[-1] - df.index[0]).days
    high_period = float(df['high'].max())
    low_period = float(df['low'].min())

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>STRK Baseline Analysis · {now_str}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1000px; margin: 20px auto; padding: 20px; background: #f7f7f9; color: #222; }}
  h1 {{ color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ color: #444; margin-top: 32px; border-left: 4px solid #4c9aff; padding-left: 10px; }}
  h3 {{ color: #555; margin-top: 20px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ background: #f0f0f5; font-weight: 600; }}
  .ok {{ color: #0a7000; font-weight: bold; }}
  .miss {{ color: #a00000; font-weight: bold; }}
  .warn {{ color: #cc8800; }}
  .na {{ color: #888; }}
  .stat {{ font-family: 'SF Mono', Monaco, monospace; font-size: 0.95em; }}
  .callout {{ background: #fff5e6; padding: 14px 18px; border-left: 4px solid #ff9500; margin: 16px 0; border-radius: 4px; }}
  .callout-info {{ background: #e6f2ff; border-left-color: #4c9aff; }}
  .footer {{ margin-top: 40px; font-size: 0.85em; color: #888; border-top: 1px solid #ddd; padding-top: 16px; }}
</style></head>
<body>

<h1>📊 STRK Baseline Analysis</h1>
<p><b>Generated:</b> {now_str}<br>
<b>Source:</b> {SYMBOL} · {source_exchange.upper()} · {TIMEFRAME}<br>
<b>Purpose:</b> reference numbers для сравнения с forward-test через 30-60 дней.</p>

<h2>📅 Data Coverage</h2>
<table>
  <tr><th>Period</th><td class="stat">{date_range} ({period_days} days)</td></tr>
  <tr><th>Bars ({TIMEFRAME})</th><td class="stat">{n_bars:,}</td></tr>
  <tr><th>Current price</th><td class="stat">${current_price:.4f}</td></tr>
  <tr><th>Period high</th><td class="stat">${high_period:.4f}</td></tr>
  <tr><th>Period low</th><td class="stat">${low_period:.4f}</td></tr>
  <tr><th>Range (peak-to-trough)</th><td class="stat">{((high_period - low_period) / low_period * 100):+.1f}%</td></tr>
  <tr><th>Buy &amp; Hold return</th><td class="stat"><b>{buy_hold_return:+.1f}%</b></td></tr>
</table>

<h2>🎯 Baseline Strategies</h2>
<p>Простые правила протестированы на РЕАЛЬНОЙ истории STRK. Настройки: TP {TAKE_PROFIT_PCT:+.0f}% · SL {STOP_LOSS_PCT:+.0f}% · max hold {MAX_HOLD_HOURS}h.</p>

<table>
  <tr>
    <th>Strategy</th>
    <th>Trades</th>
    <th>Win %</th>
    <th>Avg PnL</th>
    <th>Total</th>
    <th>Sharpe</th>
    <th>Max DD</th>
    <th>Edge vs B&amp;H</th>
    <th>Avg hold</th>
  </tr>'''

    for strat_name, s in strategies_results.items():
        wr = f"{s['win_rate_pct']:.1f}%" if s['win_rate_pct'] is not None else 'N/A'
        avg = f"{s['avg_pnl_pct']:+.2f}%" if s['avg_pnl_pct'] is not None else 'N/A'
        tot = f"{s['total_return_pct']:+.1f}%" if s['total_return_pct'] is not None else 'N/A'
        sr = f"{s['sharpe']:.2f}" if s['sharpe'] is not None else 'N/A'
        dd = f"{s['max_drawdown_pct']:.1f}%" if s['max_drawdown_pct'] is not None else 'N/A'
        edge = f"{s['edge_vs_bh_pct']:+.1f}%" if s.get('edge_vs_bh_pct') is not None else 'N/A'
        edge_class = 'ok' if (s.get('edge_vs_bh_pct') or 0) > 0 else ('miss' if (s.get('edge_vs_bh_pct') or 0) < 0 else '')
        hold = f"{s['avg_hold_hours']:.0f}h" if s.get('avg_hold_hours') is not None else 'N/A'
        html += f'''
  <tr>
    <td><b>{strat_name}</b></td>
    <td class="stat">{s["n_trades"]}</td>
    <td class="stat">{wr}</td>
    <td class="stat">{avg}</td>
    <td class="stat">{tot}</td>
    <td class="stat">{sr}</td>
    <td class="stat miss">{dd}</td>
    <td class="stat {edge_class}">{edge}</td>
    <td class="stat">{hold}</td>
  </tr>'''

    html += f'''
</table>

<div class="callout callout-info">
  <b>💡 Как читать эти числа:</b><br>
  · <b>Buy &amp; Hold return {buy_hold_return:+.1f}%</b> — это твой benchmark. Всё что даёт МЕНЬШЕ — теряет vs пассивного держания.<br>
  · <b>Win Rate</b> — доля прибыльных trades. Baseline для random = 50%. Ниже 55% edge отсутствует.<br>
  · <b>Sharpe</b> — соотношение доходности к риску. &gt;1 — приемлемо, &gt;2 — хорошо.<br>
  · <b>Max DD</b> — насколько глубоко просаживалась equity curve. &lt;-30% — некомфортно.<br>
  · <b>Edge vs B&amp;H</b> — насколько лучше/хуже пассивного holding. Положительное — strategy добавляет ценность.
</div>

<h2>🌊 Regime Analysis</h2>
<p>Классификация дней по типу рынка. Понимание какие условия доминируют для STRK.</p>

<table>
  <tr><th>Regime</th><th>Days</th><th>Share</th><th>What it means</th></tr>'''

    regime_descriptions = {
        'range': 'Флэт, |slope 7d| &lt; 2% — плохо для trend follow',
        'trending_up': 'Uptrend, slope 7d &gt; +5% — swing long работает',
        'trending_down': 'Downtrend, slope 7d &lt; -5% — swing short или флэт',
        'whipsaw': 'Резкое движение, но 14d flat — dangerous chop, oба TP и SL часто триггерят',
        'drift': 'Мелкий тренд, 2-5% за 7d — mixed conditions',
        'unknown': 'Недостаточно данных для классификации',
    }
    total = regime['total_days']
    for r_name in ['range', 'trending_up', 'trending_down', 'whipsaw', 'drift', 'unknown']:
        count = regime['regime_counts'].get(r_name, 0)
        if count == 0:
            continue
        pct = count / total * 100
        desc = regime_descriptions.get(r_name, '')
        html += f'''
  <tr>
    <td><b>{r_name}</b></td>
    <td class="stat">{count}</td>
    <td class="stat">{pct:.1f}%</td>
    <td>{desc}</td>
  </tr>'''

    html += f'''
</table>

<h3>Volatility Calibration</h3>
<table>
  <tr><th>Metric</th><th>Value</th><th>Practical use</th></tr>
  <tr><td>Avg daily range</td><td class="stat">{regime["avg_daily_range_pct"]:.2f}%</td><td>средний high-low спред за день</td></tr>
  <tr><td>Std daily range</td><td class="stat">{regime["std_daily_range_pct"]:.2f}%</td><td>волатильность волатильности</td></tr>
  <tr><td>7d avg vol</td><td class="stat">{regime["avg_daily_vol_pct"]:.2f}%</td><td>сглаженная недельная волатильность</td></tr>
  <tr><td><b>Suggested stop-loss</b></td><td class="stat"><b>{regime["avg_daily_range_pct"] * 1.5:.1f}%</b></td><td>1.5× daily range — не выбивает шумом</td></tr>
  <tr><td><b>Suggested take-profit</b></td><td class="stat"><b>{regime["avg_daily_range_pct"] * 3:.1f}%</b></td><td>3× daily range — R/R 2:1</td></tr>
</table>

<div class="callout">
  <b>⚠ Важно про STRK:</b><br>
  Средний daily range {regime["avg_daily_range_pct"]:.1f}% значит что stop &lt; {regime["avg_daily_range_pct"]:.1f}% будет часто выбивать нормальным шумом.<br>
  Используй stop минимум {regime["avg_daily_range_pct"] * 1.5:.1f}% и take минимум {regime["avg_daily_range_pct"] * 3:.1f}% для R/R 2:1.
</div>

<h2>📌 Практические выводы</h2>'''

    # Best/worst strategies
    # Filter out strategies with None/NaN — avoid TypeError на max/min
    def _valid(v):
        r = v.get('total_return_pct')
        if r is None:
            return False
        try:
            if pd.isna(r):
                return False
        except Exception:
            pass
        return True
    valid_strats = {k: v for k, v in strategies_results.items() if _valid(v)}
    if valid_strats:
        best_strat = max(valid_strats.items(), key=lambda x: x[1]['total_return_pct'])
        worst_strat = min(valid_strats.items(), key=lambda x: x[1]['total_return_pct'])
    else:
        best_strat = ('N/A · insufficient data', {'total_return_pct': 0})
        worst_strat = ('N/A · insufficient data', {'total_return_pct': 0})

    # Dominant regime
    dominant_regime = max(regime['regime_counts'].items(), key=lambda x: x[1])[0] if regime['regime_counts'] else 'unknown'

    html += f'''
<ul>
  <li><b>Доминирующий режим</b>: {dominant_regime} — {regime["regime_counts"].get(dominant_regime, 0)} дней из {total}</li>
  <li><b>Лучшая baseline strategy</b>: {best_strat[0]} ({best_strat[1]["total_return_pct"]:+.1f}%)</li>
  <li><b>Худшая baseline strategy</b>: {worst_strat[0]} ({worst_strat[1]["total_return_pct"]:+.1f}%)</li>
  <li><b>Buy &amp; Hold reference</b>: {buy_hold_return:+.1f}%</li>
  <li><b>Volatility calibration</b>: normal stop {regime["avg_daily_range_pct"] * 1.5:.1f}% · normal take {regime["avg_daily_range_pct"] * 3:.1f}%</li>
</ul>

<div class="callout callout-info">
  <b>🎯 Ключевой вывод:</b><br>
  Через 30-60 дней твой Confluence Gate + Shadow Voters дадут числа precision по live signals.<br>
  · Если Confluence RALLY signals показывают &gt;{max([s.get('win_rate_pct', 0) or 0 for s in strategies_results.values()]):.0f}% precision — есть edge над baseline.<br>
  · Если ниже — signals не лучше простых правил, нужен tune или упрощение.<br>
  · Если ~{max([s.get('win_rate_pct', 0) or 0 for s in strategies_results.values()]):.0f}% — движок даёт то же что RSI + breakout вместе. Простота выигрывает.
</div>

<div class="footer">
  <p><b>STRK Engine · strk_baseline_analysis.py</b></p>
  <p>Это baseline reference. НЕ trade recommendation. НЕ бэктест твоих Confluence signals (у них нет history).</p>
  <p>Числа для сравнения с forward-test через 30-60 дней через weekly_backtest.py.</p>
</div>

</body></html>'''
    return html


def build_telegram_summary(strategies_results, regime, buy_hold_return, current_price):
    lines = []
    lines.append(f"<b>📊 STRK Baseline Analysis</b>\n")
    lines.append(f"Current: <b>${current_price:.4f}</b>")
    lines.append(f"B&amp;H (1y): <b>{buy_hold_return:+.1f}%</b>\n")

    # Top-3 strategies by total return
    ranked = sorted(strategies_results.items(),
                   key=lambda x: x[1].get('total_return_pct') or -9999,
                   reverse=True)
    lines.append("<b>Ranked strategies:</b>")
    for name, s in ranked:
        wr = f"{s['win_rate_pct']:.0f}%" if s.get('win_rate_pct') is not None else 'N/A'
        tot = f"{s['total_return_pct']:+.1f}%" if s.get('total_return_pct') is not None else 'N/A'
        n = s['n_trades']
        lines.append(f"  · {name}: WR {wr} · Total {tot} · N={n}")

    lines.append(f"\n<b>Regime (1y):</b>")
    for r_name, count in sorted(regime['regime_counts'].items(), key=lambda x: -x[1]):
        pct = count / regime['total_days'] * 100
        lines.append(f"  · {r_name}: {count}d ({pct:.0f}%)")

    lines.append(f"\n<b>Volatility:</b>")
    lines.append(f"  · Avg daily range: {regime['avg_daily_range_pct']:.2f}%")
    lines.append(f"  · Suggested stop: {regime['avg_daily_range_pct'] * 1.5:.1f}%")
    lines.append(f"  · Suggested take: {regime['avg_daily_range_pct'] * 3:.1f}%")

    lines.append(f"\n<i>Full HTML report — вложением.</i>")
    return "\n".join(lines)


# ============================================================
# TELEGRAM SEND
# ============================================================
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
            return bool(result.get('ok'))
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def send_telegram_document(file_path, caption=''):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
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
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read())
            return bool(result.get('ok'))
    except Exception as e:
        logger.error(f"Send doc failed: {e}")
        return False


# ============================================================
# MAIN
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("STRK BASELINE ANALYSIS")
    logger.info("=" * 60)

    force_refresh = os.environ.get('BASELINE_FORCE_REFRESH', '').lower() in ('1', 'true', 'yes')

    df, source_exchange = fetch_ohlcv(force_refresh=force_refresh)
    if df is None or len(df) < 100:
        logger.error(f"Not enough OHLCV data: {len(df) if df is not None else 0} bars (need 100+)")
        return 1

    # === Auto-detect resolution ===
    # Приблизительная длительность 1 бара из data
    if len(df) > 1:
        bar_duration_sec = (df.index[1] - df.index[0]).total_seconds()
    else:
        bar_duration_sec = 3600
    bars_per_day = int(round(86400 / bar_duration_sec)) if bar_duration_sec > 0 else 24
    resolution = 'daily' if bars_per_day <= 2 else ('hourly' if bars_per_day >= 20 else f'{bars_per_day}x/day')
    logger.info(f"Data resolution: {resolution} (~{bars_per_day} bars/day)")

    # Adjust windows based on resolution
    breakout_lookback_bars = 14 * bars_per_day
    vol_avg_bars = 30 * bars_per_day
    max_hold_bars = 3 * bars_per_day  # 72h = 3 days for hourly, or 3 bars for daily

    # Compute indicators
    logger.info("Computing indicators...")
    df['rsi_14'] = calc_rsi(df['close'], 14)
    df['high_14d'] = calc_rolling_high(df['high'], breakout_lookback_bars)
    df['vol_ratio'] = calc_vol_ratio(df['volume'], vol_avg_bars)

    current_price = float(df['close'].iloc[-1])
    buy_hold_return = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
    logger.info(f"Current price: ${current_price:.4f}")
    logger.info(f"Buy & Hold return: {buy_hold_return:+.1f}%")

    # Exit-hours для strategies конвертируем в реальные часы
    max_hold_hours = MAX_HOLD_HOURS if resolution == 'hourly' else 3 * 24  # 3d для daily

    # Run strategies
    strategies = [
        ('RSI<30 → +5%/-3% or 72h (long)',
         strategy_rsi_oversold(df), 'long'),
        ('Breakout 14d high → +5%/-3% or 72h (long)',
         strategy_breakout(df), 'long'),
        ('Vol 2x + RSI<40 → 72h (long capitulation)',
         strategy_volume_spike(df), 'long'),
        ('RSI>70 → +5%/-3% or 72h (short)',
         strategy_rsi_overbought_short(df), 'short'),
        ('Weekly DCA Monday 00:00 UTC (long, hold 7d)',
         strategy_dca_weekly(df), 'long'),
    ]

    results = {}
    for name, entries, direction in strategies:
        entry_count = int(entries.sum())
        logger.info(f"Testing: {name} · {entry_count} entry signals")
        if 'DCA' in name:
            trades = backtest_strategy(df, entries, exit_after_hours=24*7,
                                       take_profit_pct=999, stop_loss_pct=-999,
                                       direction=direction)
        else:
            trades = backtest_strategy(df, entries, exit_after_hours=max_hold_hours,
                                       direction=direction)
        stats = compute_stats(trades, buy_hold_return)
        results[name] = stats
        logger.info(f"  → trades={stats['n_trades']} · WR={stats['win_rate_pct']} · Total={stats['total_return_pct']}")

    # Regime analysis
    logger.info("Analyzing regimes...")
    regime = analyze_regimes(df)
    logger.info(f"Regime counts: {regime['regime_counts']}")

    # Save results JSON
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    results_path = REPORTS_DIR / 'strk_baseline_results.json'
    with open(results_path, 'w') as f:
        json.dump({
            'ts': datetime.now(timezone.utc).isoformat(),
            'symbol': SYMBOL,
            'exchange': source_exchange or 'unknown',
            'timeframe': TIMEFRAME,
            'period_days': (df.index[-1] - df.index[0]).days,
            'current_price': current_price,
            'buy_hold_return_pct': buy_hold_return,
            'strategies': results,
            'regime_counts': regime['regime_counts'],
            'avg_daily_range_pct': regime['avg_daily_range_pct'],
            'avg_daily_vol_pct': regime['avg_daily_vol_pct'],
            'total_days': regime['total_days'],
        }, f, indent=2, default=str)

    # Build HTML
    logger.info("Building HTML report...")
    html = build_html_report(df, results, regime, current_price, buy_hold_return, source_exchange or 'unknown')
    html_path = REPORTS_DIR / f'strk_baseline_{date_str}.html'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    latest_path = REPORTS_DIR / 'strk_baseline_latest.html'
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(html)
    logger.info(f"HTML saved: {html_path}")

    # Send Telegram summary + document
    summary = build_telegram_summary(results, regime, buy_hold_return, current_price)
    send_telegram(summary)
    send_telegram_document(html_path, caption=f"📊 STRK Baseline · {date_str}")

    logger.info("=" * 60)
    logger.info("Baseline analysis complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())
