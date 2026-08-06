# START HERE · Как запустить STRK Engine с нуля

**Читать по порядку. Не пропускать шаги. Каждый шаг занимает 5-15 минут.**

Если что-то не получается — не паникуй, вернись в чат Claude и покажи ошибку.

---

## Что мы строим и зачем

**Простыми словами:**

Раньше: каждый раз ты пишешь Claude «сделай LIQ» → он вручную собирает данные → пишет отчёт.

Теперь: скрипт-робот на сервере **сам** каждые X часов собирает данные (кто куда переводил STRK, funding, TVL, регим). Пишет всё в файл `agent_input.json`. Ты сама видишь состояние без Claude в петле.

**НЕ автоматизируем:** торговлю, DECISION, вход/выход. Только **сбор фактов**.

**Что получишь в итоге:**
- Каждые 6 часов (или на любом интервале) на сервере запускается скрипт
- Он смотрит: цена, funding, регим, поток на бирже, куда мосты бриджат, стейкинг
- Если что-то важное происходит — присылает Telegram-алерт: **«Порог пробит, стоит сделать LIQ»**
- Ты открываешь Claude, копируешь ему свежий `agent_input.json`, он делает полный LIQ отчёт

Claude всё ещё нужен для **самого отчёта LIQ/RUN**. Робот только освобождает от «постоянно быть за компьютером».

---

## Шаг 1 · Что тебе нужно (проверь заранее)

### API-ключи (все бесплатные)

- [ ] **Etherscan API key** — https://etherscan.io/apis (регистрация → My API Keys → Add)
- [ ] **Starkscan API key** — https://starkscan.co/api (регистрация → API section)
- [ ] **Telegram Bot Token** — открой в Telegram @BotFather → `/newbot` → следуй инструкциям → получишь токен вида `1234567890:ABC...`
- [ ] **Telegram Chat ID** — открой в Telegram @userinfobot → `/start` → он покажет твой Chat ID (числовое)

### Место где запускать

**Вариант A · Твой домашний компьютер** (простой, если комп всегда включён)
Ничего не платишь. Минус — если компьютер выключен, робот не работает.

**Вариант B · GitHub Actions** (бесплатно, но с ограничениями)
GitHub бесплатно запускает скрипты по расписанию. Идеально для нашей задачи (несколько раз в день). Не нужен свой сервер.

**Вариант C · Дешёвый VPS** ($5/мес)
Например Hetzner CX11, DigitalOcean, Contabo. Полный контроль. Работает 24/7.

**Моя рекомендация для тебя:** Вариант B (GitHub Actions). Настраивается один раз, дальше работает сам, ничего не платишь.

---

## Шаг 2 · Скачать проект

```bash
# Если ещё нет клонированного репо:
git clone https://github.com/krol4ixa85/STRK-ENGINE.git
cd STRK-ENGINE/STRK-Engine

# Если уже клонирован — просто обнови:
git pull
```

---

## Шаг 3 · Проверь что все файлы на месте

Должно быть так:

```
STRK-Engine/
├── README.md
├── START_HERE.md                       ← этот файл
├── config/
│   ├── config.env.example              ← шаблон, ты копируешь и заполняешь
│   └── tokens.json                     ← STRK контракты (не менять)
├── data/
│   └── seeds/
│       └── flow_seeds.json             ← список адресов для мониторинга
├── scripts/
│   ├── orchestrator.py                 ← главный скрипт, запускает всё
│   ├── classify_flow.py
│   └── collectors/
│       ├── flow_eth.py
│       ├── flow_starknet.py
│       └── watcher_thresholds.py
└── docs/
    └── ...
```

Если чего-то нет — вернись в чат Claude, скажи «загрузи заново файл X».

---

## Шаг 4 · Создать config.env

Это файл с твоими секретами. **НИКОГДА не коммитить в git.**

```bash
cd config
cp config.env.example config.env
nano config.env    # или любой редактор
```

Впиши свои значения (заменяй TWO_XXX на твои):

```bash
# Обязательные
STARKSCAN_API_KEY=TWO_ЗАМЕНИ_НА_КЛЮЧ_ОТ_STARKSCAN
ETHERSCAN_API_KEY=TWO_ЗАМЕНИ_НА_КЛЮЧ_ОТ_ETHERSCAN

# Для алертов в Telegram
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjkl...
TELEGRAM_CHAT_ID=987654321

# Не менять
STARKNET_RPC_URL=https://rpc.starknet.lava.build
STRICT_NO_TRADING=true
FLOW_LOOKBACK_DAYS=7
```

Сохрани и закрой (в nano: Ctrl+O, Enter, Ctrl+X).

**Убедись что config.env НЕ попадёт в git:**

```bash
cd ..
echo "config/config.env" >> .gitignore
echo "*.log" >> .gitignore
echo "data/cache/*" >> .gitignore
```

---

## Шаг 5 · Первый запуск ВРУЧНУЮ (проверка)

Проверь что всё работает **до** автоматизации:

```bash
# Загрузи переменные из config.env
export $(cat config/config.env | grep -v '^#' | xargs)

# Запусти оркестратор
python3 scripts/orchestrator.py
```

**Что должно произойти:**
- Скрипт запустит flow_eth → скажет «29 edges» или подобное
- Запустит flow_starknet → скажет «X transfers»
- Запустит classify_flow → покажет aggregate class (например `BRIDGE_IN_DOMINANT`)
- Создаст файл `data/cache/agent_input.json`

**Если получил `agent_input.json` — ВСЁ РАБОТАЕТ.** Открой его — там все данные для Claude.

**Если ошибка:** скопируй её в чат, я помогу починить.

---

## Шаг 6 · Настройка Telegram-алертов

Watcher_thresholds.py уже настроен слать алерты в Telegram, если что-то важное происходит. Тебе нужно только **проверить связь с ботом**:

```bash
# Отправь тестовое сообщение
python3 -c "
import urllib.request, json, os
os.environ['STRICT_NO_TRADING']='true'
from scripts.collectors.watcher_thresholds import send_telegram
send_telegram('🧪 Тест из STRK Engine · всё работает')
"
```

Если получила сообщение в Telegram — связь работает.

**Что триггерит алерты** (уже настроено):
- Цена приближается к liq-зоне (менее 8% cushion)
- Цена пересекла VAL/VAH (структурный сдвиг)
- Funding вышел за ±25%
- TVL упал более чем на 5% за 7 дней
- Стейкинг разворот
- Крупный whale-transfer >5M STRK
- L2 fees ниже нормы

Каждый алерт говорит: **«Стоит сделать LIQ»** или **«RUN рекомендован»**.

---

## Шаг 7 · Автоматизация (выбери свой вариант)

### Вариант A · На своём компьютере (cron на Linux/Mac)

```bash
crontab -e
```

Добавь:

```
# Каждые 6 часов запускать orchestrator
0 */6 * * * cd /path/to/STRK-Engine && export $(cat config/config.env | xargs) && python3 scripts/orchestrator.py >> logs/cron.log 2>&1

# Каждые 30 минут запускать watcher (алерты)
*/30 * * * * cd /path/to/STRK-Engine && export $(cat config/config.env | xargs) && python3 scripts/collectors/watcher_thresholds.py --once >> logs/watcher.log 2>&1
```

### Вариант B · GitHub Actions (бесплатно) ← РЕКОМЕНДУЮ

Создай файл `.github/workflows/strk_engine.yml`:

```yaml
name: STRK Engine

on:
  schedule:
    - cron: '0 */6 * * *'      # Orchestrator каждые 6 часов
    - cron: '*/30 * * * *'     # Watcher каждые 30 минут
  workflow_dispatch:           # можно запустить вручную

jobs:
  orchestrator:
    if: github.event.schedule == '0 */6 * * *' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Run orchestrator
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
          STARKSCAN_API_KEY: ${{ secrets.STARKSCAN_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STRICT_NO_TRADING: 'true'
        run: python3 scripts/orchestrator.py
      - name: Upload cache
        uses: actions/upload-artifact@v4
        with:
          name: agent-input-${{ github.run_id }}
          path: data/cache/agent_input.json

  watcher:
    if: github.event.schedule == '*/30 * * * *'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Run watcher
        env:
          ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
          STARKSCAN_API_KEY: ${{ secrets.STARKSCAN_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          STRICT_NO_TRADING: 'true'
        run: python3 scripts/collectors/watcher_thresholds.py --once
```

**Затем добавь секреты в GitHub:**
1. Открой репо на GitHub
2. Settings → Secrets and variables → Actions → New repository secret
3. Добавь по одному:
   - `ETHERSCAN_API_KEY`
   - `STARKSCAN_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

**Готово.** GitHub Actions начнёт запускать сам через несколько минут.

---

## Шаг 8 · Как жить дальше

**Ежедневная рутина:**

1. Робот на GitHub Actions сам работает каждые 30 мин / 6 часов
2. Приходит Telegram-алерт: **«Цена пересекла VAL, LIQ рекомендован»**
3. Ты открываешь Claude, говоришь: **«Сделай LIQ, вот свежий agent_input»**
4. Копируешь текст из последнего `agent_input.json` (из GitHub Actions artifacts или локально)
5. Claude делает полный LIQ с DECISION-контрактом

**Если алертов нет** — можно раз в день просто скачивать свежий `agent_input.json` и давать Claude команду «RUN».

**Что НЕ делать:**
- Не менять пороги в watcher без обсуждения (могут начать шуметь)
- Не коммитить `config.env` в git
- Не использовать API-ключи из этой инструкции если они уже засветились — перевыпустить

---

## Что делать если сломалось

1. **Скрипт не запускается**: проверь что `python3 --version` показывает 3.10+
2. **API ошибка 401/403**: неправильный ключ, перевыпусти
3. **Etherscan V1 deprecated**: скачай свежий flow_eth.py (там уже V2)
4. **Starkscan 0 transfers на явно активный адрес**: проблема с нормализацией — обнови flow_starknet.py
5. **Telegram молчит**: проверь `TELEGRAM_CHAT_ID` (должен быть числом, не username)

Если ничего не помогает — открой Claude, скажи «STRK Engine сломался», покажи логи из `logs/`.

---

## Куда дальше

Когда ты убедишься что работает — можно расширять:

- **market_ohlcv.py** — цена + regime + structure (заменит части ручных LIQ)
- **derivatives.py** — funding + OI history
- **stake.py** — stake tracker с отдельными триггерами
- **notify_liq_ready.py** — автоматически формировать промпт для Claude из agent_input.json

Но сначала стабильно запусти базу.
