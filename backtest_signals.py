#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_signals.py — бэктест сигналов свинг-трейдинга (шорты)
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

CONFIG = {
    'position_size': 1000,
    'stop_loss_pct': 0.10,
    'take_profit_pct': 0.15,
    'commission_pct': 0.001,
}


def load_from_json(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if isinstance(data, list):
        df = pd.DataFrame(data)
    elif isinstance(data, dict):
        rows = data.get('rows', [])
        if not rows:
            raise Exception("No data found")
        df = pd.DataFrame(rows)
    else:
        raise Exception("Unknown format")
    
    numeric_cols = ['transfers_4h', 'transfers_24h_avg', 'transfers_7d_avg', 
                   'pct_vs_24h', 'pct_24h_vs_7d', 'volume_momentum', 'price',
                   'confidence_score']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    if 'phase_signal' not in df.columns and 'signal' in df.columns:
        df['phase_signal'] = df['signal']
    
    if 'hour' in df.columns:
        df['hour'] = pd.to_datetime(df['hour'])
        df['day'] = df['hour']
    elif 'day' in df.columns:
        df['day'] = pd.to_datetime(df['day'])
    
    df.sort_values('day', inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    if 'price' not in df.columns:
        transfers = df['transfers_4h'].values if 'transfers_4h' in df.columns else np.arange(len(df))
        norm = (transfers - transfers.min()) / (transfers.max() - transfers.min() + 1)
        base_price = 0.020 + 0.010 * (1 - norm)
        df['price'] = base_price + np.random.normal(0, 0.0005, len(df))
        df['price'] = df['price'].clip(0.015, 0.035)
    
    return df


def generate_mock_data(n_days=60):
    np.random.seed(42)
    dates = pd.date_range(end=datetime.now(), periods=n_days)
    signals = ['BEARISH_BREAKDOWN', 'NEUTRAL_CONSOLIDATION', 'BULLISH_MOMENTUM', 'MIXED_SIGNAL']
    weights = [0.35, 0.30, 0.20, 0.15]
    
    price = 0.022
    prices = []
    for i in range(n_days):
        price *= 1 + np.random.normal(0, 0.015)
        price = max(price, 0.015)
        prices.append(price)
    
    df = pd.DataFrame({
        'day': dates,
        'phase_signal': np.random.choice(signals, n_days, p=weights),
        'transfers_4h': np.random.randint(5000, 20000, n_days),
        'volume_momentum': np.random.uniform(50, 150, n_days),
        'price': prices,
    })
    return df


def backtest_strategy(df, config):
    df = df.copy()
    df.sort_values('day', inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    trades = []
    capital = 10000
    cash = capital
    
    for i, row in df.iterrows():
        signal = row.get('phase_signal', row.get('signal', 'NEUTRAL_CONSOLIDATION'))
        price = row['price']
        day = row['day']
        
        # Входим в шорт по сигналу BEARISH_BREAKDOWN
        if signal == 'BEARISH_BREAKDOWN' and i < len(df) - 1:
            entry_price = price
            entry_day = day
            entry_signal = signal
            
            # Для шорта: стоп-лосс выше, тейк-профит ниже
            stop_loss = entry_price * (1 + config['stop_loss_pct'])
            take_profit = entry_price * (1 - config['take_profit_pct'])
            
            exit_price = None
            exit_day = None
            exit_result = None
            
            # Смотрим следующие строки
            for j in range(i + 1, len(df)):
                next_price = df.iloc[j]['price']
                next_day = df.iloc[j]['day']
                
                if next_price >= stop_loss:
                    exit_price = stop_loss
                    exit_day = next_day
                    exit_result = 'STOP_LOSS'
                    break
                elif next_price <= take_profit:
                    exit_price = take_profit
                    exit_day = next_day
                    exit_result = 'TAKE_PROFIT'
                    break
            
            if exit_price is None:
                exit_price = df.iloc[-1]['price']
                exit_day = df.iloc[-1]['day']
                exit_result = 'CLOSED_END'
            
            # Для шорта: прибыль = (entry - exit) / entry
            trade_result = (entry_price - exit_price) / entry_price
            profit = trade_result * config['position_size'] * (1 - config['commission_pct'])
            
            trades.append({
                'entry_day': entry_day,
                'exit_day': exit_day,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'signal': entry_signal,
                'exit_signal': df.iloc[j]['phase_signal'] if exit_result != 'CLOSED_END' else 'END',
                'type': 'SHORT',
                'result': exit_result,
                'profit_pct': trade_result * 100,
                'profit': profit,
                'duration_hours': (exit_day - entry_day).total_seconds() / 3600,
            })
            
            cash += profit + config['position_size']
    
    equity = [capital]
    for t in trades:
        equity.append(equity[-1] + t['profit'])
    
    return trades, equity


def calculate_metrics(trades, equity, initial_capital=10000):
    result = {
        'total_trades': 0,
        'winning_trades': 0,
        'losing_trades': 0,
        'win_rate': 0,
        'profit_factor': 0,
        'total_profit': 0,
        'gross_profit': 0,
        'gross_loss': 1,
        'max_drawdown_pct': 0,
        'sharpe_ratio': 0,
        'final_equity': initial_capital,
        'avg_profit_pct': 0,
        'avg_win_pct': 0,
        'avg_loss_pct': 0,
        'avg_duration_hours': 0,
    }
    
    if not trades:
        return result
    
    df_trades = pd.DataFrame(trades)
    total_trades = len(df_trades)
    winning = df_trades[df_trades['profit'] > 0]
    losing = df_trades[df_trades['profit'] < 0]
    
    result['total_trades'] = total_trades
    result['winning_trades'] = len(winning)
    result['losing_trades'] = len(losing)
    result['win_rate'] = len(winning) / total_trades * 100 if total_trades > 0 else 0
    result['gross_profit'] = winning['profit'].sum() if len(winning) > 0 else 0
    result['gross_loss'] = abs(losing['profit'].sum()) if len(losing) > 0 else 1
    result['profit_factor'] = result['gross_profit'] / result['gross_loss'] if result['gross_loss'] > 0 else 0
    result['total_profit'] = df_trades['profit'].sum()
    
    equity_series = pd.Series(equity)
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak
    result['max_drawdown_pct'] = drawdown.min() * 100 if len(drawdown) > 0 else 0
    
    returns = equity_series.pct_change().dropna()
    result['sharpe_ratio'] = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0
    result['final_equity'] = equity[-1] if equity else initial_capital
    result['avg_profit_pct'] = df_trades['profit_pct'].mean() if total_trades > 0 else 0
    result['avg_win_pct'] = winning['profit_pct'].mean() if len(winning) > 0 else 0
    result['avg_loss_pct'] = losing['profit_pct'].mean() if len(losing) > 0 else 0
    result['avg_duration_hours'] = df_trades['duration_hours'].mean() if 'duration_hours' in df_trades.columns else 0
    
    return result


def print_report(metrics, config, df):
    print("\n" + "=" * 60)
    print("📊 БЭКТЕСТ СИГНАЛОВ СВИНГ-ТРЕЙДИНГА (SHORT)")
    print("=" * 60)
    print(f"Период: {df['day'].min().date()} → {df['day'].max().date()}")
    print(f"Всего строк: {len(df)}")
    print(f"Размер позиции: ${config['position_size']:,.0f}")
    print(f"Стоп-лосс: {config['stop_loss_pct']*100:.0f}%")
    print(f"Тейк-профит: {config['take_profit_pct']*100:.0f}%")
    print("=" * 60)
    
    print("\n📈 ТОРГОВЫЕ МЕТРИКИ:")
    print(f"  Всего сделок:        {metrics['total_trades']}")
    if metrics['total_trades'] > 0:
        print(f"  Успешных:            {metrics['winning_trades']} ({metrics['win_rate']:.1f}%)")
        print(f"  Убыточных:           {metrics['losing_trades']}")
        print(f"  Суммарная прибыль:   ${metrics['total_profit']:,.2f}")
        print(f"  Profit Factor:       {metrics['profit_factor']:.2f}")
        print(f"  Макс. просадка:      {metrics['max_drawdown_pct']:.2f}%")
        print(f"  Sharpe Ratio:        {metrics['sharpe_ratio']:.2f}")
        print(f"  Итоговый капитал:    ${metrics['final_equity']:,.2f}")
        print(f"  Средняя длительность: {metrics['avg_duration_hours']:.1f} часов")
    else:
        print("  ⚠️ НЕТ СДЕЛОК — сигнал SHORT не появлялся в данных")
    
    print("\n🎯 АНАЛИЗ СИГНАЛОВ:")
    if 'phase_signal' in df.columns:
        signals = df['phase_signal'].value_counts()
        for sig, count in signals.items():
            pct = count / len(df) * 100
            print(f"  {sig}: {count} ({pct:.1f}%)")
    
    print("\n📊 ОЦЕНКА:")
    if metrics['total_trades'] == 0:
        print("  ⚠️ НЕТ ДАННЫХ ДЛЯ ОЦЕНКИ — стратегия требует SHORT сигналов")
    elif metrics['profit_factor'] > 1.5 and metrics['win_rate'] > 45:
        print("  ✅ СТРАТЕГИЯ РАБОТАЕТ — сигналы дают преимущество")
    elif metrics['profit_factor'] > 1.0:
        print("  🟡 УМЕРЕННЫЙ РЕЗУЛЬТАТ — нужна доработка")
    else:
        print("  🔴 СТРАТЕГИЯ НЕ РАБОТАЕТ — сигналы не дают преимущества")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=str, help='Path to JSON file')
    parser.add_argument('--mock', action='store_true', help='Generate mock data')
    parser.add_argument('--export', action='store_true', help='Export trades to CSV')
    args = parser.parse_args()
    
    config = CONFIG.copy()
    
    if args.file:
        df = load_from_json(args.file)
        print(f"✅ Loaded {len(df)} records from {args.file}")
    elif args.mock:
        df = generate_mock_data(60)
        print(f"✅ Generated {len(df)} mock records")
    else:
        default_file = Path('dune_data_8297685.json')
        if default_file.exists():
            df = load_from_json(default_file)
            print(f"✅ Loaded {len(df)} records from {default_file}")
        else:
            print("❌ Error: specify --file or --mock")
            print("   Example: python backtest_signals.py --mock")
            print("   Example: python backtest_signals.py --file dune_data_8297685.json")
            sys.exit(1)
    
    trades, equity = backtest_strategy(df, config)
    metrics = calculate_metrics(trades, equity, config['position_size'])
    print_report(metrics, config, df)
    
    if args.export and trades:
        pd.DataFrame(trades).to_csv('trades_export.csv', index=False)
        print(f"\n📁 Trades exported to trades_export.csv")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())