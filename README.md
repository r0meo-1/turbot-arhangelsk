# TurBot — бот для турагентства «АПРЕЛЬ тур»

Telegram- и VK-бот для сбора заявок на подбор тура: диалог с клиентом, согласие на обработку ПДн (152-ФЗ), AI/шаблонная подборка, уведомление менеджеру и опциональная отправка в CRM «МоиДокументы-Туризм».

Работает как **Flask webhook** (gunicorn + SQLite). Подходит для продакшена на VPS в РФ и для демо (Render и аналоги — только без реальных ПДн).

**Репозиторий:** [github.com/r0meo-1/turbot-arhangelsk](https://github.com/r0meo-1/turbot-arhangelsk)

---

## Возможности

- **Диалог заявки** — конечный автомат: согласие → направление → даты → люди → бюджет → телефон  
- **Кнопки** — популярные направления, число туристов, «Назад», «Отмена», share contact (Telegram)
- **Валидация** — телефон (РФ), число человек, бюджет
- **Подборка туров** — Groq (LLaMA) или локальные шаблоны (`AI_MODE=template`, без VPN)
- **SQLite** — сессии, пользователи, **история заявок (leads)**; инкрементальное сохранение
- **Админка** — `/send`, `/broadcast`, `/users`, `/stats`, `/analytics`, `/export`, `/followup`, `/mdt`, …
- **Уведомление админу** в момент заявки (имя, ID, параметры, телефон)
- **MDT CRM** — lead / preorder / both, push менеджерам, напоминания
- **VK-версия** — отдельный процесс (`vk_bot.py`), Callback API
- **152-ФЗ** — согласие, `/privacy`, `/delete`, авто-retention, удаление leads
- **Надёжность** — retries HTTP, secret token webhook, dedup update_id, graceful shutdown, алерты админу
- **Общий код** — пакет `shared/` (валидация, даты, AI, MDT, privacy) для TG и VK
- **Тесты** — `pytest` (60+ тестов)
- **Деплой** — Docker Compose, systemd, nginx, install-скрипт для РФ VPS

---

## Стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.8+ |
| HTTP | Flask + gunicorn |
| Мессенджеры | Telegram Bot API, VK Callback API |
| AI | Groq (опционально) или шаблоны |
| БД | SQLite (WAL) |
| CRM | MDT (МоиДокументы-Туризм) |

---

## Как это работает

```
Клиент → /start → согласие (152-ФЗ)
       → направление → даты → люди → бюджет → телефон
       → подтверждение + уведомление админу
       → (фон) MDT CRM + AI-подборка
       → заявка в таблице leads
```

1. Telegram/VK шлёт update на webhook.
2. FSM ведёт диалог; состояние в памяти + SQLite.
3. После телефона: lead в БД, confirm клиенту, сообщение админу; **MDT и AI — в фоне**, чтобы webhook отвечал быстро.
4. Админ отвечает `/send` или делает `/broadcast`.

Эндпоинты:

| Метод | Путь | Назначение |
|-------|------|------------|
| `GET` | `/` | «жив» |
| `GET` | `/health` | JSON-статус (токены, leads, MDT, …) |
| `POST` | `/webhook` | Telegram |
| `POST` | `/vk/webhook` | VK Callback |

---

## Структура проекта

```
bot.py              # Telegram-бот
vk_bot.py           # VK-бот
shared/             # общая логика (validation, ai, mdt, privacy, dates, …)
tests/              # pytest
deploy/             # systemd, nginx, install.sh
docs/               # политика ПДн (черновик), MDT API
Dockerfile
docker-compose.yml
DEPLOY.md           # деплой на VPS в России
```

---

## Быстрый старт (локально)

```bash
git clone https://github.com/r0meo-1/turbot-arhangelsk.git
cd turbot-arhangelsk
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # Linux/macOS
```

Минимум в `.env`:

```env
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=ваш_telegram_id
AI_MODE=template
TELEGRAM_SECRET_TOKEN=длинная_случайная_строка
```

`ADMIN_ID` узнать у [@userinfobot](https://t.me/userinfobot).

Запуск:

```bash
python bot.py
```

Для локального HTTPS-webhook удобны Cloudflare Tunnel / ngrok, затем:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://ВАШ_ДОМЕН/webhook&secret_token=$TELEGRAM_SECRET_TOKEN"
```

### Продакшен (gunicorn)

```bash
gunicorn bot:app --bind 0.0.0.0:$PORT --workers 1 --threads 2
```

**Всегда `--workers 1`**: состояние в памяти + SQLite. Несколько worker'ов без общей БД-стратегии — рассинхрон.

### Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Переменные окружения

Полный список — в [`.env.example`](.env.example).

### Обязательные (Telegram)

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен от [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | `chat_id` админа |

### Часто используемые

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `AI_MODE` | `groq` | `groq` или `template` (без внешнего API) |
| `GROQ_API_KEY` | — | Ключ Groq; иначе fallback на шаблон |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Модель Groq |
| `DATABASE_PATH` | `bot_state.sqlite` | Путь к SQLite |
| `PORT` | `5000` | Порт Flask |
| `TELEGRAM_SECRET_TOKEN` | — | Проверка заголовка webhook |
| `DIALOG_TIMEOUT_HOURS` | `6` | Таймаут незавершённого диалога (`0` = выкл.) |
| `FOLLOWUP_DELAY_HOURS` | `3` | Напоминание «не закончили заявку» |
| `ADMIN_ERROR_ALERTS` | `true` | Алерты админу при критических ошибках |
| `PRIVACY_POLICY_URL` | — | Ссылка на политику ПДн |
| `DATA_OPERATOR_NAME` | ИП … АПРЕЛЬ тур | Оператор в тексте согласия |
| `DATA_RETENTION_DAYS` | `180` | Автоудаление неактивных (`0` = выкл.) |

### MDT CRM

| Переменная | Описание |
|------------|----------|
| `MDT_ENABLED` | `true` / `false` |
| `MDT_ACCOUNT` | Поддомен (например `apreltur`) |
| `MDT_API_KEY` | API-ключ |
| `MDT_MODE` | `lead` \| `preorder` \| `both` |
| `MDT_SOURCE` | Метка источника |
| `MDT_NOTIFY_MANAGERS` | Push менеджерам |
| `MDT_MANAGER_IDS` | ID через запятую |
| `MDT_REMINDER_*` | Напоминания после preorder |

Подробнее: [`docs/mdt_api.md`](docs/mdt_api.md).

### VK (опционально)

| Переменная | Описание |
|------------|----------|
| `VK_ACCESS_TOKEN` | Токен группы |
| `VK_GROUP_ID` | ID группы |
| `VK_CONFIRMATION` | Строка подтверждения Callback API |
| `VK_SECRET_KEY` | Секрет Callback (опционально) |
| `VK_PORT` / `VK_DATABASE_PATH` | Порт и БД отдельного процесса |

```bash
gunicorn vk_bot:app --bind 0.0.0.0:5100 --workers 1 --threads 4
```

---

## Команды

### Пользователь (Telegram)

| Команда | Действие |
|---------|----------|
| `/start` | Начать подбор (сначала согласие, если ещё не дано) |
| `/cancel` | Отменить текущую заявку |
| `/privacy` | Краткая политика ПДн |
| `/delete` | Удалить данные и отозвать согласие |
| `/help` | Справка |

В VK — текстовые команды: «Начать», «Отмена», «Политика», «Удалить», «Помощь».

### Админ (только `ADMIN_ID`)

| Команда | Действие |
|---------|----------|
| `/send {chat_id} {текст}` | Сообщение пользователю (HTML) |
| `/broadcast {текст}` | Рассылка всем (в фоне) |
| `/broadcast {направление} {текст}` | Рассылка по направлению |
| `/users` | Список пользователей |
| `/stats` | Пользователи, активные диалоги, заявки |
| `/analytics` | Конверсия, 7/30 дней, популярные направления |
| `/export` | Последние заявки с телефонами |
| `/followup` | Ручной follow-up незавершившим |
| `/restart` | Сбросить активные сессии |
| `/mdt [test\|reload]` | Статус / тест / обновить страны MDT |
| `/help` | Справка админа |

---

## Деплой

### VPS в России (рекомендуется для реальных ПДн)

См. подробный гайд: **[DEPLOY.md](DEPLOY.md)**  
Скрипт: `deploy/install.sh` (systemd + nginx + Let's Encrypt).

### Docker

```bash
docker compose up -d
```

Образы копируют `bot.py`, `vk_bot.py` и пакет **`shared/`**.

### Render / зарубежный free-tier (только демо)

В репозитории есть `render.yaml`.  
**Не используйте для реальных телефонов клиентов** — см. раздел 152-ФЗ ниже.

```text
AI_MODE=template
MDT_ENABLED=false
BOT_TOKEN=...
ADMIN_ID=...
TELEGRAM_SECRET_TOKEN=...
```

После деплоя:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://ВАШ.onrender.com/webhook&secret_token=$TELEGRAM_SECRET_TOKEN"
```

Проверка: `GET https://ВАШ.onrender.com/health`

---

## Персональные данные и 152-ФЗ

Бот обрабатывает **имя, телефон, ID мессенджера** граждан РФ.

### Реализовано в коде

- согласие до сбора данных («✅ Согласен»);
- `/privacy` и `/delete` (стирание session, user, leads, consent);
- срок хранения `DATA_RETENTION_DAYS` (по умолчанию 180);
- имя оператора в текстах согласия.

### Обязанности оператора (не код)

1. **Локализация (ст. 18 ч. 5)** — хранить ПДн на серверах в РФ (не Render для боя).
2. **Уведомление РКН** — подать как оператор ПДн.
3. **Опубликованная политика** — выложить документ, указать `PRIVACY_POLICY_URL`  
   (черновик: [`docs/privacy_policy.md`](docs/privacy_policy.md) — нужна юридическая вычитка).
4. **Защита** — secret token webhook, доступ к БД, бэкапы (`scripts/backup.sh`).

> Это инженерные рекомендации, не юридическая консультация.

---

## Лицензия

[MIT](LICENSE) © 2026 [r0meo-1](https://github.com/r0meo-1)
