#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_dune_data.py — выгрузка данных из Dune через прямые API запросы
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.parse
import pandas as pd

DUNE_API_BASE = 'https://api.dune.com/api/v1'


def dune_request(path, method='GET', body=None, api_key=''):
    url = f"{DUNE_API_BASE}{path}"
    headers = {
        'X-Dune-API-Key': api_key,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    data = json.dumps(body).encode('utf-8') if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            response = r.read().decode('utf-8')
            return json.loads(response)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')[:500] if e.fp else ''
        print(f"HTTP {e.code}: {error_body}")
        raise
    except Exception as e:
        print(f"Request failed: {e}")
        raise


def execute_query(query_id, api_key):
    print(f"  Executing query {query_id}...")
    resp = dune_request(f'/query/{query_id}/execute', method='POST', api_key=api_key)
    execution_id = resp.get('execution_id')
    if not execution_id:
        raise Exception(f"No execution_id: {resp}")
    print(f"  Started: {execution_id}")
    return execution_id


def poll_execution(execution_id, api_key):
    for attempt in range(60):
        try:
            status = dune_request(f'/execution/{execution_id}/status', api_key=api_key)
            state = status.get('state', '')
            if state == 'QUERY_STATE_COMPLETED':
                print(f"  Completed after {attempt*3}s")
                return dune_request(f'/execution/{execution_id}/results', api_key=api_key)
            elif state in ('QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED'):
                raise Exception(f"Query failed: {state}")
            else:
                if attempt % 5 == 0:
                    print(f"  ...still running ({attempt*3}s)")
                time.sleep(3)
        except Exception as e:
            print(f"  Poll error: {e}")
            time.sleep(5)
    raise Exception("Timeout")


def get_latest_result(query_id, api_key):
    print(f"📊 Загрузка query_id: {query_id}...")
    
    # Проверяем, есть ли кешированный результат
    try:
        result = dune_request(f'/query/{query_id}/results', api_key=api_key)
        if result.get('result') and result['result'].get('rows'):
            print("  Using cached result")
            return result
    except Exception as e:
        print(f"  No cached result or error: {e}")
    
    # Выполняем запрос
    print("  No cached result, executing...")
    exec_id = execute_query(query_id, api_key)
    return poll_execution(exec_id, api_key)


def export_to_json(df, filename):
    # Преобразуем datetime в строки
    for col in df.columns:
        if 'datetime' in str(df[col].dtype) or 'timestamp' in str(df[col].dtype):
            df[col] = df[col].astype(str)
    
    df = df.where(pd.notnull(df), None)
    data = df.to_dict(orient='records')
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"✅ Сохранён: {filename} ({len(data)} записей)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--query-id', type=int, required=True)
    parser.add_argument('--format', type=str, choices=['json', 'csv'], default='json')
    parser.add_argument('--output', type=str, default=None)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()

    api_key = os.environ.get('DUNE_API_KEY')
    if not api_key:
        print("❌ DUNE_API_KEY не найден")
        print("   Установи: set DUNE_API_KEY=dqu_твой_ключ")
        sys.exit(1)

    try:
        result = get_latest_result(args.query_id, api_key)
        if not result or not result.get('result'):
            print("❌ Нет данных")
            sys.exit(1)
        
        result_data = result['result']
        rows = result_data.get('rows', [])
        
        if not rows:
            print("❌ Нет строк")
            sys.exit(1)
        
        df = pd.DataFrame(rows)
        
        print(f"✅ Загружено {len(df)} записей")
        print(f"📋 Колонки: {', '.join(df.columns.tolist())}")
        
        if args.preview:
            print("\n📊 ПРЕВЬЮ (первые 5 строк):")
            print(df.head().to_string())
            print(f"\nВсего: {len(df)} строк, {len(df.columns)} колонок\n")
        
        filename = args.output or f"dune_data_{args.query_id}.{args.format}"
        if args.format == 'json':
            export_to_json(df, filename)
        else:
            df.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"✅ Сохранён: {filename} ({len(df)} записей)")
        
        print(f"\n📋 Для бэктеста: python backtest_signals.py --file {filename}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)


if __name__ == '__main__':
    sys.exit(main())