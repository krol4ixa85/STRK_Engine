# Бриф для агента: STRK Engine Automation

**Скопируй этот файл и используй как system prompt / первое сообщение любому агенту.**

---

## Контекст проекта

STRK Engine — систематический аналитический фреймворк для STRK (Starknet).
Философия: **анализ-only, не советник, не исполнитель**.

Ты работаешь над **автоматизацией СБОРА данных**, не над принятием решений.

## Абсолютные запреты

1. **Торговля и NEW_ENTRY в скриптах ЗАПРЕЩЕНЫ.**
   DECISION живёт только в `decision_contract.txt` (skills) и в LIQ/RUN отчётах.
   Скрипты пишут файлы, агент читает JSON в MUST #6.

2. **Не путать SEED и TOKEN:**
   - SEED = адрес (кошелёк/инфраструктура), который мы МОНИТОРИМ
   - TOKEN = контракт STRK, используется как ФИЛЬТР transfers
   - Bridge `0xce5485…` это SEED (мы мониторим этот кошелёк), НЕ TOKEN
   - STRK L1 token = `0xCa14007Eff0dB1f8135f4C25B34De49AB0d42766`
   - STRK L2 token = `0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d`

3. **Классификация flow — только по маршруту (из flow_playbook):**
   REBALANCE | INTERNAL | DISTRIBUTION | ACCUMULATION | UNKNOWN | NOT_CHECKED
   
   **Запрещено:** `net outflow → accumulation` без анализа маршрута.

4. **Ключи только через env**, никогда в коде или в git:
   `STARKSCAN_API_KEY`, `ETHERSCAN_API_KEY`, `NANSEN_API_KEY`

5. **Глубина по умолчанию — 1 hop от seeds.**
   BFS depth ≥ 2 только через флаг `--deep`, только REVIEW mode.
   NansenRedString — только вручную на REVIEW, не в daily.

## Правильные источники API

### НЕ ИСПОЛЬЗОВАТЬ как инструкцию API

- ❌ `github.com/starkscan/starkscan-verifier` — это verifier ABI, не transfers API
- ❌ `github.com/ethereum/go-ethereum` — это полная нода, слишком тяжело

### Starknet L2 — Starkscan Docs

- Docs: https://starkscan.co/docs
- Base URL: `https://api.starkscan.co/api/v1/SN_MAIN/`
- Header: `X-Starkscan-Api-Key: $STARKSCAN_API_KEY`
- Transfers по адресу: `GET /address/{address}/transfers`
- Balance: `GET /token/{token}/balance-of/{address}`

### Ethereum L1 — Etherscan API

```
https://api.etherscan.io/api
  ?module=account
  &action=tokentx
  &contractaddress=0xCa14007Eff0dB1f8135f4C25B34De49AB0d42766
  &address=<SEED>
  &startblock=0&endblock=99999999
  &page=1&offset=1000&sort=desc
  &apikey=$ETHERSCAN_API_KEY
```

## Архитектура — 4 сервиса, не «бот на блок»

| Сервис | Что делает | Частота |
|---|---|---|
| A · Market pulse | цена, funding, OI, regime | 15-60 мин |
| B · On-chain flow | transfers, balances, 1-hop | по событию / 1-2×/сутки |
| C · Structure | VAL/POC/VAH, PHASE | 1×/сутки |
| D · Decision pack | склеивает JSON для LIQ | только при LIQ/RUN |

## Структура проекта

```
STRK_Engine/
├── protocol/       # MASTER, templates
├── skills/         # текстовые эвристики
├── data/
│   ├── seeds/flow_seeds.json    # SEED-адреса (мониторим)
│   └── cache/                   # выходы collectors
├── scripts/
│   ├── collectors/
│   │   ├── flow_eth.py          ← ЭТАП 1
│   │   ├── flow_starknet.py     ← ЭТАП 2
│   │   ├── market_ohlcv.py
│   │   ├── derivatives.py
│   │   └── stake.py
│   ├── classify_flow.py         ← ЭТАП 3
│   └── orchestrator.py          ← ЭТАП 4
├── config/
│   ├── config.env.example
│   └── tokens.json              # TOKEN контракты (не seeds!)
```

## Порядок работы — этап за этапом

### Этап 1 · Ethereum flow collector (сейчас)

Файл: `scripts/collectors/flow_eth.py`

**Входы:**
- `data/seeds/flow_seeds.json` — список SEED-адресов L1
- `config/tokens.json` → `ethereum_l1.strk_erc20.address` (TOKEN фильтр)
- env: `ETHERSCAN_API_KEY`

**Логика:**
1. Читает L1 seeds из flow_seeds.json
2. Для каждого seed → Etherscan tokentx query с contractaddress = STRK L1 token
3. Фильтрует по lookback (default 7d)
4. Строит edges: from, to, amount_strk, tx, ts, chain=ethereum
5. Считает per-seed summary: vol_in, vol_out, net, unique counterparties

**Выходы:**
- `data/cache/flow_eth_edges.csv` — все edges
- `data/cache/flow_eth_summary.json` — агрегаты для orchestrator
  ```json
  {
    "as_of": "...",
    "chain": "ethereum",
    "token_contract": "0xCa14...",
    "lookback_days": 7,
    "seeds_processed": N,
    "total_edges": M,
    "not_checked": false,
    "flow_class": null,           # заполняет classify_flow.py
    "route": null,                 # заполняет classify_flow.py
    "new_addresses": [],           # заполняет classify_flow.py
    "seeds_summary": [...]
  }
  ```

**НЕ делает:** классификацию (это Этап 3).

### Этап 2 · Starknet flow collector

Файл: `scripts/collectors/flow_starknet.py`

Аналогично, но через Starkscan API. Использует `config/tokens.json` → `starknet_l2.strk_native.address`.

Base URL: `https://api.starkscan.co/api/v1/SN_MAIN/`
Header: `X-Starkscan-Api-Key: $STARKSCAN_API_KEY`

Endpoints:
- Transfers: `GET /address/{address}/transfers?direction=any`
- Опционально balance: `GET /token/{token_address}/balance-of/{address}`

Выходы: `flow_starknet_edges.csv`, `flow_starknet_summary.json` (тот же формат).

### Этап 3 · Classify flow

Файл: `scripts/classify_flow.py`

**Входы:**
- `flow_eth_summary.json` + `flow_starknet_summary.json`
- `skills/flow_playbook.txt` (эвристики)
- `data/seeds/flow_seeds.json` (метки CEX, bridges для распознавания)

**Логика (из flow_playbook):**
- `CEX_hot → CEX_hot` (same cluster) → REBALANCE / INTERNAL
- `many → one` + retention high → ACCUMULATION
- `one → many retail` → DISTRIBUTION
- `→ 0xce5485…` (L1 bridge) → BRIDGE_IN
- иначе → UNKNOWN

**Выход:**
- `data/cache/flow_map_summary.json` — итоговый JSON с `flow_class`, `route`,
  `new_addresses`, `as_of`, `not_checked` — вход для агента в MUST #6.

### Этап 4 · Orchestrator

Файл: `scripts/orchestrator.py`

Склеивает выходы всех collectors в единый JSON для агента при LIQ/RUN.
НЕ пишет DECISION. Только собирает поля.

## Как начать сегодня

1. `cd STRK_Engine`
2. Скопировать `config/config.env.example` в `config/config.env`, вписать ключи
3. Запустить `python3 scripts/collectors/flow_eth.py --dry-run` — проверить что seeds читаются
4. Заполнить недостающие адреса в `data/seeds/flow_seeds.json` (сейчас есть заглушки TBD)
5. Запустить без --dry-run — получить первый CSV + JSON
6. Проверить edges вручную (одну-две транзакции на Etherscan)

## Чек-лист перед коммитом любого скрипта

- [ ] Нет ключей в коде (только `os.environ.get(...)`)
- [ ] Нет `DECISION`, `NEW_ENTRY`, покупки/продажи в логике
- [ ] SEED и TOKEN разделены
- [ ] Классификация flow идёт через отдельный classify_flow.py, не в collector
- [ ] Выход — файлы (CSV/JSON), не print для агента
- [ ] Логи пишутся в `logs/`, не в консоль только
- [ ] Rate limits соблюдаются (Etherscan free: 5 req/sec, Starkscan: свой лимит)

## Что НЕ делать

- ❌ Не встраивать полный RUN в скрипт (BFS запрещён в RUN по MASTER)
- ❌ Не решать за агента: скрипт даёт факты, агент интерпретирует
- ❌ Не использовать `net outflow` как единственный признак accumulation
- ❌ Не использовать bridge адрес как TOKEN контракт
- ❌ Не запускать NansenRedString в daily LIQ (дорого, для REVIEW only)
