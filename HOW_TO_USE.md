# STRK Engine · Полная инструкция как работать

Прочитай один раз. Это твой manual на каждый день.

---

## Что у тебя работает

**6 автоматических модулей + композитный детектор:**

| Модуль | Что делает | Частота | Триггерит Telegram? |
|---|---|---|---|
| **composite_detector_v2** | Полный анализ 14-дневного flow | 6 часов | ✅ non-neutral |
| **whale_monitor** | Real-time крупные транзакции | 30 мин | ✅ >5M STRK |
| **discord_monitor** | Читает Nansen alerts из Discord | 30 мин | ✅ >5M STRK |
| **funding_history** | OKX + Bitget funding | внутри composite | (часть composite) |
| **unlock_calendar** | STRK vesting pressure | внутри composite | (часть composite) |
| **BTC cycle** | dist200 + slope30 | внутри composite | (часть composite) |

**Все 6 голосуют.** Итоговый сигнал — по confluence:
- 4-6 модулей за одно направление → HIGH confidence
- 2-3 совпадают → MEDIUM
- Разброс → MIXED/NEUTRAL (не алертит)

---

## Что придёт тебе в Telegram

### Тип 1 · Composite signal (каждые 6 часов если non-neutral)

```
🔴🔴 STRK · Strong Bearish Setup

Confidence: HIGH (multiple modules aligned)
Bullish votes: 0 · Bearish: 4

Signals breakdown:
  · distribution: BEARISH
  · btc_cycle: DOWN
  · funding: NOISE_flipflop
  · unlock: MEDIUM_pressure
  · whales_self: QUIET
  · discord_nansen: QUIET

Key numbers:
  · LARGE receivers (14d): 8
  · Distribution ratio: 0.08
  · BTC $63,929 · dist200 -9.8%
  · Funding +4.3% ann · avg7d -0.1%
  · Unlock pressure: MEDIUM
  · Next cliff: 2026-10-15 (71d, 200M)

Recommended action:
REDUCE EXPOSURE. Multiple modules aligned bearish.
Timeframe: Downside risk near-term
```

**Что делать:** открой Claude, скажи «Сделай LIQ на composite_signal_v2».

### Тип 2 · Whale alert (real-time, >5M STRK)

```
🐋🐋 STRK Whale Alert

Amount: 25.30M STRK
Class: DISTRIBUTION_CUSTODY_CEX
Route: Custody Endpoint 1 → Binance 14

Interpretation:
CRITICAL: custody sending to CEX = pre-sell.

View on Etherscan
```

**Что делать:** если CRITICAL — LIQ немедленно через Claude.

### Тип 3 · Discord forwarded alert (Nansen)

```
📡 Discord Alert (from NansenBot)

Amount: 15.20M STRK
Route: Foundation Multisig → Binance
Hint: bearish

Original message:
Whale Alert: 15.2M STRK from...
```

**Что делать:** аналогично whale — если крупный, LIQ.

### Тип 4 · Тишина

Норма. Ничего не делать.

---

## Deployment · пошагово

### Шаг 0 · Подготовь ключи

**Обязательные:**
- Etherscan API key: https://etherscan.io/apis
- Starkscan API key: https://starkscan.co/api
- Telegram bot token: пиши @BotFather в Telegram, `/newbot`
- Telegram chat_id: пиши @userinfobot, он ответит

**Опционально (для Discord):**
- Discord bot token: см. `docs/DISCORD_SETUP.md` (10 минут)

### Шаг 1 · Локальный setup

1. Распакуй `STRK_Engine_complete.tar.gz` в удобное место
2. Открой папку `config/`
3. Скопируй `config.env.example` → `config.env`
4. Впиши все ключи в `config.env`

### Шаг 2 · Проверка

Двойной клик по `check_config.bat`. Должно быть 5 [OK].

### Шаг 3 · Локальный тест каждого модуля

Запускай по одному, смотри что нет ошибок:

1. `run_orchestrator.bat` — сбор flow map
2. `run_composite_signal.bat` — главный детектор
3. `run_whale_monitor.bat` — whale check
4. `run_discord_test.bat` — если настроил Discord
5. `run_discord_monitor.bat` — если Discord работает

Если что-то падает — покажи мне ошибку.

### Шаг 4 · GitHub push

```bash
cd STRK_Engine
git add .
git commit -m "Full automation with Discord + funding + unlock"
git push
```

### Шаг 5 · GitHub Actions Secrets

GitHub → Settings → Secrets and variables → Actions → New repository secret

**Обязательные:**
- `ETHERSCAN_API_KEY`
- `STARKSCAN_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

**Опциональные (Discord):**
- `DISCORD_BOT_TOKEN`
- `DISCORD_CHANNEL_ID` (по умолчанию `1502225814714978374`)

### Шаг 6 · Включи Actions

GitHub → Actions → Enable workflows.

**Готово.** Первый composite сигнал через 6 часов, первые whale/discord через 30 минут.

---

## Твой ежедневный workflow

**Утро (5 минут):**
1. Проверь Telegram — есть алерты за ночь?
2. Если да → Claude → «LIQ на composite_signal_v2.json»
3. Если нет → норма, продолжай день

**Днём/вечером:**
Ничего не надо делать. Робот работает.

**Когда приходит Telegram:**
1. Не паникуй — HIGH ≠ обязан торговать
2. Прочитай breakdown — какие модули триггерят?
3. Открой Claude: **«Сделай LIQ, вот artefact:»** и приложи `composite_signal_v2.json`
4. Claude выдаст полный отчёт с DECISION-контрактом
5. Ты принимаешь окончательное решение

---

## Сценарии реакции на алерты

### BULLISH_STRONG

4-6 модулей за рост. Distribution smart-money, BTC UP, funding не crowded.

**Действие:**
1. Claude → LIQ
2. Если LIQ подтверждает — рассмотри LONG
3. Historical analog: Rally #1 (+135% за месяц)

### BEARISH_STRONG

4-6 модулей за падение. Whales грузят, BTC DOWN.

**Действие:**
1. Если есть STRK — **срочно LIQ**
2. Обычно = снижать exposure, готовиться к −20-50%
3. НЕ открывать LONG
4. Historical analog: Crash #1 (−86%)

### BULLISH (не strong)

Половина модулей bullish, есть risk-факторы.

**Действие:**
- Возможен короткий отскок но не sustained trend
- Rally #2 и Rally #3 в истории — короткие пампы +99-175% потом крах
- Play только с tight stop

### MIXED / NEUTRAL

**Действие:** ничего. Wait for clarity.

### Whale CRITICAL

Custody → CEX >20M STRK.

**Действие:**
1. Claude немедленно
2. LIQ с фокусом на PLAYBOOK_FLOW
3. Обычно = distribution перед sell

---

## Ограничения — читай раз в месяц

### Что валидировано

- Distribution shape на 9 событиях: **precision 66.7%, recall 66.7%**
- Quiet correctness: **100%** (0 ложных алертов в спокойные периоды)
- BTC cycle: правильно отличил sustained rally от brief bounce

### Что не валидировано полностью

- Composite v2 с funding+unlock+whales+Discord (только v1 базовое)
- Rally #2 и Crash #3 в истории были misclassified

### Реалистичные ожидания

- **2-4 composite алерта** в месяц
- **5-15 whale алертов** в месяц (в spike периоды больше)
- **0-10 Discord алертов** в месяц (зависит от Nansen активности)
- Спокойная неделя = 0 алертов (правильно)

Если 3+ недели тишина — проверь GitHub Actions.

---

## Что НЕ делать

- ❌ Не торговать без LIQ через Claude
- ❌ Не увеличивать позиции при MIXED
- ❌ Не игнорировать Whale CRITICAL
- ❌ Не менять пороги в детекторе без бэктеста
- ❌ Не коммитить `config.env` в git

---

## Где смотреть результаты

**Локально (после каждого запуска):**
- `data/cache/composite_signal_v2.json` — последний composite сигнал
- `data/cache/agent_input.json` — данные для Claude LIQ
- `data/cache/funding_signal.json`
- `data/cache/unlock_signal.json`
- `data/cache/whale_monitor_state.json` — история whale alerts
- `data/cache/discord_monitor_state.json` — история Discord alerts

**На GitHub Actions:**
- Actions → выбирай последний run → Artifacts → скачивай ZIP

**В Telegram:**
- Все сигналы автоматически

---

## Улучшения на будущее (по желанию)

1. **Больше исторических событий** → precision с 66.7% до 75%+
2. **Graph analysis (BFS depth 2)** для кластеризации накопителей
3. **Automated LIQ generation** — Claude сам пишет отчёт
4. **More funding sources** (KuCoin, Gate когда доступ появится)

Каждое — отдельный проект 3-8 часов.

---

## TL;DR

1. **Установи** — архив, config.env, check_config.bat
2. **Deploy на GitHub** — секреты, включи Actions
3. **Жди Telegram** — раз в несколько дней придёт
4. **При сигнале** — Claude → LIQ с composite_signal_v2.json
5. **Не торгуй сам** — Claude фильтрует ложные

Ты в петле только когда сигнал приходит. Робот делает остальное.

---

## 🆕 Управление списком отслеживаемых кошельков (loop)

**Реестр адресов теперь динамический.** Ты можешь добавлять/убирать кошельки в watchlist **3 способами**:

### Способ 1 · Прямо из Telegram (проще всего)

Пиши своему боту команды:

```
/list                                            → все кошельки
/list watchlist                                  → только watchlist
/status                                          → сколько в каждой категории
/add 0xa9d1e08c... smart_holder_1                → добавить в watchlist
/add 0xa9d1e08c... team_wallet team_and_foundation → в конкретную категорию
/remove smart_holder_1                           → убрать
/note 0xa9d1e08c... "новый холдер с 15M"        → добавить заметку
/search accumulator                              → найти
/help                                            → справка
```

Каждые 30 минут GitHub Actions читает новые команды и обрабатывает.
Изменения **автоматически коммитятся в git** и подхватываются watchlist.

### Способ 2 · Windows launcher

Двойной клик по `manage_wallets.bat` — интерактивное меню.

### Способ 3 · Командная строка

```bash
python3 scripts/wallet_registry.py add 0xa9d1e08c... my_wallet watchlist
python3 scripts/wallet_registry.py list --category watchlist
python3 scripts/wallet_registry.py remove my_wallet --yes
python3 scripts/wallet_registry.py export wallets.csv
python3 scripts/wallet_registry.py import wallets.csv
```

### Категории

| Категория | Использование |
|---|---|
| `l1_infrastructure` | L1 bridges, contracts |
| `custody_and_transit` | Custody wallets, transit bridgers |
| `l2_native` | Starknet L2 addresses |
| `cex_hot_wallets_known_dynamic` | Exchange hot wallets |
| `team_and_foundation` | Team multisig, Foundation |
| `watchlist` | Обычная слежка (по умолчанию) |

### Что произойдёт после добавления

1. **whale_monitor** автоматически подхватит адрес при следующем запуске
2. Watchlist имеет **сниженный порог** — алерт при **500k STRK** (вместо 5M)
3. При движении на/с watched адреса придёт алерт вида:
   ```
   🎯 Whale Watchlist Alert
   Amount: 800k STRK
   Route: Custody → smart_holder_1
   Interpretation: Watched wallet receiving — accumulation or top-up.
   ```

### Пример workflow

Ты видишь в Nansen что новый адрес начал собирать STRK. Пишешь боту:
```
/add 0xNEWADDRESS... nansen_smart_5
```

Бот отвечает `[OK] Added`. Через 30 минут GitHub Actions:
1. Прочитал команду
2. Добавил в flow_seeds.json
3. Закоммитил в git
4. Whale monitor теперь следит за этим адресом
5. Как только на нём движение >500k STRK — придёт 🎯 алерт

### Резервные копии

При каждом изменении в `data/seeds/backups/` создаётся timestamped snapshot. Если что-то сломалось — можно откатить.


---

## 🆕 Три новых модуля

### 1. Auto-discovery — робот сам предлагает адреса

Каждые 6 часов бот сканирует STRK Transfer events за последние 48 часов и находит:
- Non-CEX адреса, получившие >1M STRK
- С retention >70% в окне
- С паттерном ACCUMULATOR / PURE_HOLDER

Присылает в Telegram:

```
🔍 Discovery #1 · ACCUMULATOR

Address:
0xa4b9569b...

Received: 2.34M STRK
Retention: 95.2%
Sources: 4 unique senders
Current balance: 8.15M STRK
Score: 12.34

Pattern: Multiple sources → holds (accumulation pattern)

Add to watchlist?
/accept 0xa4b9569b...
/reject 0xa4b9569b...

Etherscan
```

**Что делать:**
- `/accept <addr>` — добавляет в watchlist автоматически
- `/reject <addr>` — не предлагает больше
- Игнорируешь — предложит ещё раз позже (макс. 3 раза)

**Ручной запуск сканирования:** `/discover` в Telegram.

### 2. Graph analysis — depth-1 граф для watched адресов

Команда: `/graph <addr>`

Строит граф за последние 90 дней:
- **Top 10 funders** (кто отправлял STRK этому адресу)
- **Top 10 destinations** (кому этот адрес отправлял)
- Метки CEX/BRIDGE/CUSTODY/EOA
- CEX inflow share % (retail vs whale funding)
- CEX outflow share % (distribution vs staking)

Присылает в Telegram summary + сохраняет JSON в `data/graphs/`.

**Пример полезного случая:**
```
🕸 Graph · smart_accumulator_1

30-day flow:
  Inflow: 47.5M
  Outflow: 2.1M
  Retention: 95.6%

Top funders:
  · [BRIDGE] StarkGate L1: 12M (25%)
  · [EOA] 0xabc...: 8.5M (18%)
  · [EOA] 0xdef...: 6.2M (13%)
  · [CEX] Binance 14: 4.1M (9%)

Interpretation:
  ✓ Only 9% inflow from CEX — non-retail funding source
  ✓ Very high retention (96%) — long-term holder pattern
```

**Cluster detection:** Если несколько watched адресов имеют общего funder, это признак координированного накопления. Приходит специальный алерт `🔗 Cluster Alert`.

### 3. Per-wallet HTML dashboard

Команда: `/dashboard <addr>`

Генерирует полный HTML отчёт:
- Balance now + windows 7d/30d/90d/180d
- Interpretation flags (accumulation/distribution/CEX pattern)
- Top 10 funders/destinations с ссылками на Etherscan
- Recent 20 transactions
- Стилизованный тёмный дизайн

Сохраняется в `data/dashboards/<addr>_<timestamp>.html`.
Ссылка приходит в Telegram, файл в GitHub Actions artifacts.

**Открой в браузере** — красивый одностраничный отчёт.

---

## Полный список Telegram команд

```
📋 УПРАВЛЕНИЕ РЕЕСТРОМ
/list [category]              — все / по категории
/add <addr> <name> [cat]      — добавить
/remove <addr_or_name>        — убрать
/note <addr> <text>           — заметка
/search <text>                — найти
/status                       — статистика

🔍 AUTO-DISCOVERY
/discover                     — запустить сканирование сейчас
/accept <addr> [name]         — принять кандидата
/reject <addr>                — отклонить

📊 АНАЛИЗ
/graph <addr>                 — depth-1 funders/dests
/dashboard <addr>             — полный HTML отчёт

/help                         — справка
```

---

## Twoi новый workflow

**Раз в 6 часов** робот делает:
1. Composite signal (BEARISH/BULLISH алерт если non-neutral)
2. **Auto-discovery** (top-3 candidates для watchlist)

**Каждые 30 минут:**
3. Whale monitor (>5M STRK или >500k если watchlist involved)
4. Discord monitor (если настроен bot token)
5. Telegram bot commands (обработка твоих команд)

**По запросу:**
- `/graph <addr>` → depth-1 анализ
- `/dashboard <addr>` → HTML отчёт
- `/discover` → форсированное сканирование

**Ты в петле только когда:**
- Пришёл composite алерт (2-4 раза в месяц)
- Пришёл whale/discord алерт (5-15 в месяц)
- Пришло discovery предложение → принять или отклонить (1-3 в неделю)

