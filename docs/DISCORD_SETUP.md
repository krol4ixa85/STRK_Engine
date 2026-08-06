# Как настроить Discord bot для чтения alerts

Discord API требует **bot token** — без него мы не можем читать сообщения. Это правило Discord, не наше ограничение.

Есть **два варианта**:

## Вариант A · У тебя уже есть бот в проекте (проверить)

Если ты в предыдущих сессиях уже создавала Discord-бота для проекта:

1. Открой https://discord.com/developers/applications
2. Войди с тем же аккаунтом
3. Проверь список Applications — если есть что-то с названием «STRK» или «Nansen» — это твой существующий бот
4. Открой его → Bot → «Reset Token» → скопируй
5. Впиши в `config/config.env` как `DISCORD_BOT_TOKEN=...`

## Вариант B · Создать нового бота (5 минут)

### Шаг 1 · Application

1. Открой https://discord.com/developers/applications
2. Нажми **"New Application"**
3. Название: `STRK Engine Reader` (любое)
4. Согласись с ToS, нажми **"Create"**

### Шаг 2 · Bot

1. Слева выбери **"Bot"**
2. Внизу — **"Add Bot"** → **"Yes, do it!"**
3. Появится страница бота

### Шаг 3 · Копировать Token

1. В разделе **TOKEN** нажми **"Reset Token"** → **"Yes, do it!"**
2. Скопируй token (выглядит как `MTIz...abc.XYZ...`)
3. **ВАЖНО:** Token показывается ОДИН РАЗ. Сохрани в `config/config.env`:

```
DISCORD_BOT_TOKEN=MTIz...твой_токен...
DISCORD_CHANNEL_ID=1502225814714978374
```

### Шаг 4 · Privileged Intents (обязательно!)

Ещё на странице Bot, **включи галочку**:
- ☑ **MESSAGE CONTENT INTENT**

Без этого бот **не сможет читать** содержимое сообщений.

Нажми **"Save Changes"** внизу.

### Шаг 5 · Пригласить бота в сервер

1. Слева выбери **"OAuth2"** → **"URL Generator"**
2. **Scopes**: отметь ☑ **bot**
3. **Bot Permissions** (появятся ниже): отметь ☑ **Read Messages/View Channels**, ☑ **Read Message History**
4. Внизу — скопируй сгенерированную **URL**
5. Открой этот URL в браузере
6. Выбери сервер где висят Nansen alerts
7. Нажми **"Authorize"**

Готово. Бот теперь в сервере.

### Шаг 6 · Найти Channel ID

1. В Discord Settings → Advanced → включи **"Developer Mode"**
2. Правый клик на канал с alerts → **"Copy Channel ID"**
3. Впиши в `config/config.env` как `DISCORD_CHANNEL_ID=1234567890...`

## Тестирование

Запусти в терминале (в папке STRK_Engine):

```bash
# Linux/Mac
export $(cat config/config.env | xargs)
python3 scripts/collectors/discord_monitor.py --test

# Windows
run_discord_test.bat
```

**Ожидаемый ответ:**
```
Testing Discord connection to channel 1502225814714978374...
Fetched 1 messages
✓ Connected. Last message: 12345678...
```

**Если ошибка 401 · Unauthorized:**
- Token неверный. Перевыпусти через "Reset Token".

**Если ошибка 403 · Forbidden:**
- Бот не имеет permission на канал. Дай ему READ_MESSAGES.

**Если ошибка 404 · Not Found:**
- Бот не в сервере, где канал. Переприглашай через OAuth URL.

## Как это работает после настройки

- Каждые **30 минут** GitHub Actions запускает `discord_monitor.py`
- Он смотрит новые сообщения в канале (после последнего прочитанного)
- Парсит: amount, addresses, route, direction hint
- Если whale event >5M STRK — форвардит в Telegram

Плюс, эти события учитываются в **composite detector** как ещё один голосующий модуль.

## Безопасность

- ❌ **Никогда** не коммить `config.env` в git — там token
- ✅ Файл `.gitignore` уже это учитывает
- ✅ Бот в GitHub Actions использует Secret, не файл
- Если token засветился — сразу перевыпусти на developer portal
