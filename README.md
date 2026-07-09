# TurBot — Telegram-бот, который не теряет заявки

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-webhook-000)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

> Клиент хочет в Египет. Менеджер в чате. Заявка «где-то в переписке».  
> **TurBot** — конечный автомат, который вежливо выпытывает направление, даты, людей, бюджет и телефон,  
> кладёт lead в SQLite, орёт админу в Telegram и (по желанию) пихает всё в CRM.  
> AI-подборка — бонус. Шаблон без VPN — режим выжившего из 2024.

Lead-бот для турагентства **«АПРЕЛЬ тур»**.  
Flask webhook · gunicorn · SQLite · пакет `shared/` с VK-версией · тесты · Docker · деплой на РФ VPS.

**Репозиторий:** [github.com/r0meo-1/turbot-arhangelsk](https://github.com/r0meo-1/turbot-arhangelsk)

---

## Что умеет (спойлер: не только `/start`)

- **Диалог** — согласие (152-ФЗ) → направление → даты → люди → бюджет → телефон  
- **Кнопки** — популярные страны, «Назад», «Отмена», share contact  
- **Валидация** — телефон по-русски, люди 1–50, бюджет > 0 (ноль рублей — это не тур, это медитация)  
- **AI / template** — Groq или локальные шаблоны (`AI_MODE=template`, без «а у нас VPN лёг»)  
- **SQLite** — сессии, юзеры, **история leads** (да, `/export` теперь не пустой — мы тоже удивились)  
- **Админка** — `/send`, `/broadcast` (в фоне, webhook не стонет), `/stats`, `/analytics`, `/mdt`…  
- **MDT CRM** — lead / preorder / both, push, напоминания  
- **VK-бот** — `vk_bot.py`, тот же мозг в `shared/`  
- **152-ФЗ** — согласие, `/privacy`, `/delete`, retention, стирание leads  
- **Прод-мелочи** — secret token, retries, dedup `update_id`, graceful shutdown, алерты админу  

---

## Как это работает

```
Клиент ──/start──► согласие ──► FSM ──► телефон
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              lead в SQLite    «спасибо» клиенту   пинг админу
                    │
                    └── (фон) MDT + AI-подборка
```

MDT и AI **не блокируют** ответ Telegram. Потому что ждать LLaMA на webhook — это хобби, а не сервис.

| Метод | Путь | Зачем |
|-------|------|--------|
| GET | `/` | «Я жив» |
| GET | `/health` | JSON для uptime (и чтобы было что смотреть в 3 ночи) |
| POST | `/webhook` | Telegram |
| POST | `/vk/webhook` | VK |

---

## Структура

```
bot.py / vk_bot.py   — мессенджер-специфика
shared/              — validation, AI, MDT, privacy, dates…
tests/               — pytest (гоняйте, не верьте README на слово)
deploy/              — systemd, nginx, install.sh
docs/                — политика ПДн (черновик), MDT API
```

---

## Быстрый старт

```bash
git clone https://github.com/r0meo-1/turbot-arhangelsk.git
cd turbot-arhangelsk
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Windows: copy .env.example .env
```

Минимум в `.env`:

```env
BOT_TOKEN=от_BotFather
ADMIN_ID=узнать_у_@userinfobot
AI_MODE=template
TELEGRAM_SECRET_TOKEN=длинная_случайная_строка_пожалуйста
```

```bash
python bot.py
# или
gunicorn bot:app --bind 0.0.0.0:$PORT --workers 1 --threads 2
```

**`--workers 1`**. Несколько воркеров + in-memory state = «почему у клиента пропал диалог».  
Это не баг gunicorn. Это физика.

Webhook:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://ВАШ_ДОМЕН/webhook&secret_token=$TELEGRAM_SECRET_TOKEN"
```

### Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Переменные

Полный список — [`.env.example`](.env.example).

| Переменная | Нужно? | Суть |
|------------|--------|------|
| `BOT_TOKEN` | да | Токен бота |
| `ADMIN_ID` | да | Кто получает заявки и админ-команды |
| `AI_MODE` | нет | `groq` / `template` |
| `GROQ_API_KEY` | нет | Без ключа — шаблоны, жизнь продолжается |
| `TELEGRAM_SECRET_TOKEN` | очень желательно | Иначе webhook — общественный туалет |
| `MDT_*` | нет | CRM, когда надо по-взрослому |
| `DATA_RETENTION_DAYS` | нет | 180 по умолчанию — 152-ФЗ кивает |

---

## Команды

### Клиент

| Команда | Что делает |
|---------|------------|
| `/start` | Начать (сначала согласие, если вы не старый друг) |
| `/cancel` | «Я передумал» |
| `/privacy` | Коротко про ПДн |
| `/delete` | Стереть данные. Серьёзно. |
| `/help` | Справка |

### Админ (`ADMIN_ID`)

| Команда | Что делает |
|---------|------------|
| `/send {id} {текст}` | Личное (HTML ок) |
| `/broadcast …` | Всем или по направлению (фон) |
| `/users` `/stats` `/analytics` `/export` | Метрики и телефоны |
| `/followup` `/restart` `/mdt` | Напоминания, сброс, CRM |

---

## Деплой

- **РФ VPS** (для реальных ПДн) — [DEPLOY.md](DEPLOY.md), `deploy/install.sh`  
- **Docker** — `docker compose up -d` (не забудьте `shared/` — Dockerfile уже в курсе)  
- **Render / free-tier** — ок для **демо**. Реальные телефоны граждан РФ на US-хостинге — это уже не «авось», это 152-ФЗ.

---

## 152-ФЗ (коротко и без юрфакультета)

В коде: согласие, `/privacy`, `/delete`, retention.  
На операторе: сервер в РФ, уведомление РКН, опубликованная политика (`PRIVACY_POLICY_URL`), здравый смысл.

Черновик: [`docs/privacy_policy.md`](docs/privacy_policy.md) — юрист всё равно перепишет. Так и должно быть.

> Это не юридическая консультация. Это README с чувством самосохранения.

---

## Лицензия

[MIT](LICENSE) © 2026 [r0meo-1](https://github.com/r0meo-1)

*P.S. Если бот спросил согласие раньше телефона — значит, кто-то читал закон. Редкость. Цените.*
