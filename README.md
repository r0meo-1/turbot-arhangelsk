# TurBot — Travel Agency Bot

A Telegram bot that collects travel requests from clients, guides them through a
short tour-selection dialog, generates an AI-assisted tour blurb, and pushes each
request.

Built as a lead-generation tool for travel agency «АПРЕЛЬ тур».
The bot runs as a Flask webhook service (designed for free-tier hosting such as
Render.com) and notifies an admin in Telegram about every new request.

> RU: Телеграм-бот для подбора туров. Ведёт клиента по диалогу (направление,
> даты, люди, бюджет, телефон), формирует AI-подборку.
> Админ получает уведомление о каждой заявке.

## Features

- **Guided request flow** — a finite-state dialog collects destination, dates,
  number of travellers, budget, and phone number. Reply-keyboard buttons for
  popular destinations and people counts make input faster.
- **Input validation** — phone numbers, people counts, and budgets are
  validated before acceptance; the user is prompted to re-enter on error.
- **AI tour suggestions** — uses the Groq API (LLaMA) by default, or a
  template-based generator that requires no external API and works from Russia
  without proxies. Template mode is selected with `AI_MODE=template`. A typing
  indicator is shown while generating.
- **State persistence** — user data and the user registry are saved to a
  SQLite database so they survive restarts. A one-time migration from the
  legacy JSON state file is performed automatically on first start.
- **Admin tools** — `/help`, `/users`, `/stats`, `/restart`, `/send <chat_id>
  <text>`, and `/broadcast <text>` for managing users and forwarding tour
  selections (HTML formatting supported, rate-limited broadcast).
- **Admin notifications** — the admin receives a Telegram message (with the
  client's name and ID) the moment a new request is submitted.
- **User commands** — `/start`, `/help`, `/cancel` for a smooth client
  experience.
- **Robust HTTP** — all outbound requests have timeouts to prevent hangs, and
  Telegram API calls are retried on network errors / rate-limits (`429`).
- **Webhook security** — optional `TELEGRAM_SECRET_TOKEN` verification so only
  Telegram can call the webhook.
- **`.env` auto-loading** — environment variables are read from `.env`
  automatically during local development (via `python-dotenv`).
- **Health endpoint** — `GET /health` returns basic status and configuration
  checks for uptime monitoring.
- **Stale-dialog cleanup** — unfinished requests are automatically cancelled
  after a configurable period of inactivity (`DIALOG_TIMEOUT_HOURS`).
- **MDT CRM integration** — completed leads can be automatically sent to
  «МоиДокументы-Туризм» via `/api/add-lead` (`MDT_ENABLED=true`).

## Tech stack

- Python 3.8+
- [Flask](https://flask.palletsprojects.com/) — webhook HTTP server
- [Telegram Bot API](https://core.telegram.org/bots/api) — via `requests`
- [Groq](https://groq.com/) — AI tour-blurb generation
- [gunicorn](https://gunicorn.org/) — production WSGI server

## How it works

1. Telegram delivers updates to the bot's `POST /webhook` endpoint.
2. A client sends `/start` and is walked through the request flow:
   `destination → dates → people → budget → phone`. Reply-keyboard buttons
   let the user go back, cancel, or share their contact. Per-user progress is
   kept in an in-memory state machine and persisted to SQLite.
3. On completion the bot:
   - confirms the request to the client,
   - notifies the admin (`ADMIN_ID`) with the client's name and chat ID,
   - shows a typing indicator and generates an AI tour suggestion.
4. The admin can reply to or broadcast messages to users via admin commands.

`GET /` returns a simple health-check string.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/r0meo-1/turbot-arhangelsk.git
cd turbot-arhangelsk
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in the values:

| Variable               | Required | Description                                                          |
| ---------------------- | -------- | -------------------------------------------------------------------- |
| `BOT_TOKEN`            | yes      | Telegram Bot API token from [@BotFather](https://t.me/BotFather).    |
| `ADMIN_ID`             | yes      | Telegram `chat_id` of the admin who receives leads.                  |
| `AI_MODE`              | no       | `groq` (default) or `template` (no external AI, works in Russia).    |
| `GROQ_API_KEY`         | no       | Groq API key for AI blurbs (falls back to template if empty).        |
| `GROQ_MODEL`           | no       | Groq model name (default `llama-3.1-70b-versatile`).                 |
| `DATABASE_PATH`        | no       | Path for SQLite state persistence (default `bot_state.sqlite`).      |
| `STATE_FILE`           | no       | Legacy JSON state file — auto-migrated to SQLite on first start.    |
| `PORT`                 | no       | Port for the Flask server (default `5000`).                          |
| `TELEGRAM_SECRET_TOKEN`| no       | Long random string for webhook request verification.                 |
| `DIALOG_TIMEOUT_HOURS` | no       | Auto-cancel inactive dialogs after N hours (default `6`).            |
| `MDT_ENABLED`          | no       | Send completed leads to MDT CRM (`true`/`false`, default `false`).   |
| `MDT_ACCOUNT`          | no       | MDT account subdomain (e.g. `apreltur`).                             |
| `MDT_API_KEY`          | no       | API key from your MDT account.                                       |
| `MDT_SOURCE`           | no       | Lead source label in MDT (default `Telegram Bot`).                   |
| `MDT_BASE_URL`         | no       | Optional override of the MDT API base URL.                           |
| `MDT_MODE`             | no       | `lead` (default), `preorder`, or `both`.                              |
| `MDT_NOTIFY_MANAGERS`  | no       | Send push notifications to managers (`true`/`false`, default `false`).|
| `MDT_MANAGER_IDS`      | no       | Comma-separated MDT manager IDs for push/reminder.                   |
| `MDT_REMINDER_ENABLED` | no       | Create a follow-up reminder in MDT after preorder (`true`/`false`).  |
| `MDT_REMINDER_DAYS`    | no       | Days from now for the MDT reminder (default `1`).                    |
| `MDT_REMINDER_TEXT`    | no       | Text of the MDT reminder (default `Позвонить по заявке...`).         |

The application reads configuration from the environment. For local development
variables are loaded automatically from `.env`. In production, export the
variables or configure them through your process manager / host.

> 💡 **Groq недоступен из вашего региона?** Установите `AI_MODE=template` —
> подборка туров будет генерироваться локально из шаблонов без обращений к
> внешним AI-сервисам.

### 3. Run

```bash
python bot.py
```

The Flask server listens on `0.0.0.0:$PORT`. Expose it over HTTPS and register
the webhook with Telegram:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://YOUR_DOMAIN/webhook"
```

### 4. Production (gunicorn)

```bash
gunicorn bot:app --bind 0.0.0.0:$PORT --workers 1 --threads 2
```

Use `--workers 1` to avoid SQLite write contention between workers. If you
need more workers, set `DATABASE_PATH` to a shared network path or migrate to
PostgreSQL.

### 5. Tests

Install dev dependencies and run the test suite:

```bash
pip install -r requirements-dev.txt
pytest
```

### 6. MDT CRM integration

> Full structured API reference is available in [`docs/mdt_api.md`](docs/mdt_api.md).

To send completed leads to «МоиДокументы-Туризм»:

1. Enable the integration in `.env`:

```env
MDT_ENABLED=true
MDT_ACCOUNT=your-subdomain
MDT_API_KEY=your-mdt-api-key
MDT_MODE=lead
```

2. Choose the operation mode with `MDT_MODE`:

   - `lead` (default) — calls `/api/add-lead` with the client's name, phone
     and tour parameters.
   - `preorder` — creates a temp tourist via `/api/add-tourist-temp` and then
     an preorder via `/api/create-preorder` with parsed dates, country and budget.
   - `both` — creates both a lead and a preorder.

3. Optional manager notifications:

   ```env
   MDT_NOTIFY_MANAGERS=true
   MDT_MANAGER_IDS=1,2,3
   ```

   When `MDT_MODE` is `preorder` or `both`, the bot can also create a follow-up
   reminder/task in MDT for each configured manager (`/api/add-reminder`).
   Set `MDT_REMINDER_ENABLED=false` to disable this.

## Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

1. Fork this repository
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your forked repo
4. Set environment variables: BOT_TOKEN, ADMIN_ID
5. Set AI_MODE=template (works without VPN from Russia)
6. Deploy — then register webhook:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://YOUR_DOMAIN/webhook"
```

## Commands

### User commands

| Command   | Description                          |
| --------- | ------------------------------------ |
| `/start`   | Begin the tour-selection dialog.     |
| `/cancel`  | Abort the current request flow.      |
| `/privacy` | Show the personal-data privacy notice. |
| `/delete`  | Erase the user's personal data and withdraw consent. |
| `/help`    | Show help text.                      |

### Admin commands

| Command                    | Description                                      |
| -------------------------- | ------------------------------------------------ |
| `/send {chat_id} {text}`   | Send a message to a specific user (HTML OK).     |
| `/broadcast {text}`        | Send a message to all users (rate-limited).      |
| `/users`                   | List all known users with names and IDs.         |
| `/stats`                   | Show user count and active sessions.             |
| `/restart`                 | Clear all active dialog sessions.                |
| `/help`                    | Show admin help.                                 |

## Personal data & Russian law (152-ФЗ)

This bot collects **personal data** of Russian citizens (name, phone number,
Telegram ID). Operating it in Russia requires compliance with Federal Law
№152-ФЗ «Oперсональных данных». The application implements the parts that
live in code; the rest is the operator's responsibility.

**Implemented in the bot:**

- **Consent before collection** — `/start` shows a personal-data processing
  consent prompt (operator name + optional policy link) and only proceeds
  after the user taps «✅ Согласен». The consent timestamp is stored.
- **Right to erasure / consent withdrawal** — `/delete` erases the user's
  session, registry row, and consent immediately.
- **Privacy notice** — `/privacy` shows what is collected, why, for how long,
  and the operator's details.
- **Data minimisation / retention** — a background job erases a client's
  personal data after `DATA_RETENTION_DAYS` (default 180) of inactivity.

**Operator responsibilities (NOT handled by code) — ❗ required:**

1. **Data localisation (ст. 18 ч. 5).** Personal data of RF citizens must be
   collected and stored in a database **physically located in Russia**. Do
   **not** deploy the database on foreign hosting such as Render.com. Use a
   Russian provider (Yandex Cloud, VK Cloud, Selectel, Timeweb, Reg.ru, …).
   The included `render.yaml` is for reference only and is **not** compliant
   as-is.
2. **Roskomnadzor notification.** The operator (ИП Замятина М.А.) must file a
   personal-data processing notification with РКН.
3. **Published policy.** Host a Privacy Policy / consent document and set its
   URL in `PRIVACY_POLICY_URL`. A draft is provided in `docs/privacy_policy.md`
   — it must be reviewed by a lawyer before publication.
4. **Security measures (ст. 19).** Keep `TELEGRAM_SECRET_TOKEN` set, restrict
   database access, and encrypt backups.

> ⚠️ This section is engineering guidance, not legal advice. Have a
> data-protection specialist confirm compliance for your specific setup.

## License

[MIT](LICENSE) © 2026 r0meo-1
