# TurBot Arhangelsk — Travelata Partner Bot

A Telegram bot that collects travel requests from clients, guides them through a
short tour-selection dialog, generates an AI-assisted tour blurb with a
Travelata affiliate link, and pushes each request as a lead into U-ON CRM.

Built as a real lead-generation tool for a travel agency in Arkhangelsk, Russia.
The bot runs as a Flask webhook service (designed for free-tier hosting such as
Render.com) and notifies an admin in Telegram about every new request.

> RU: Телеграм-бот для подбора туров. Ведёт клиента по диалогу (направление,
> даты, люди, бюджет, телефон), формирует подборку с партнёрской ссылкой
> Travelata и создаёт лид в U-ON CRM. Админ получает уведомление о каждой заявке.

## Features

- **Guided request flow** — a finite-state dialog collects destination, dates,
  number of travellers, budget, and phone number.
- **AI tour suggestions** — uses the Groq API (LLaMA) to generate a short,
  friendly tour description; gracefully falls back to a static message when no
  Groq key is configured.
- **Travelata affiliate links** — builds partner search URLs with the
  configured partner ID.
- **U-ON CRM integration** — automatically creates a lead for every completed
  request.
- **Admin tools** — `/help`, `/users`, `/send <chat_id> <text>`, and
  `/broadcast <text>` for managing users and forwarding tour selections (HTML
  formatting supported).
- **Admin notifications** — the admin receives a Telegram message the moment a
  new request is submitted.

## Tech stack

- Python 3
- [Flask](https://flask.palletsprojects.com/) — webhook HTTP server
- [Telegram Bot API](https://core.telegram.org/bots/api) — via `requests`
- [Groq](https://groq.com/) — AI tour-blurb generation
- [U-ON CRM API](https://api.u-on.ru/) — lead creation

## How it works

1. Telegram delivers updates to the bot's `POST /webhook` endpoint.
2. A client sends `/start` and is walked through the request flow:
   `destination → dates → people → budget → phone`. Per-user progress is kept
   in an in-memory state machine.
3. On completion the bot:
   - confirms the request to the client,
   - notifies the admin (`ADMIN_ID`),
   - generates an AI tour suggestion with a Travelata affiliate link,
   - creates a lead in U-ON CRM.
4. The admin can reply to or broadcast messages to users via admin commands.

`GET /` returns a simple health-check string.

> Note: state and the user list are stored in memory, so they reset on restart.
> This keeps the service simple for small-scale, single-agency use.

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

| Variable               | Required | Description                                                        |
| ---------------------- | -------- | ------------------------------------------------------------------ |
| `BOT_TOKEN`            | yes      | Telegram Bot API token from [@BotFather](https://t.me/BotFather).  |
| `ADMIN_ID`             | yes      | Telegram `chat_id` of the admin who receives leads.                |
| `UON_API_KEY`          | no       | U-ON CRM API key (lead creation is skipped if empty).              |
| `TRAVELATA_PARTNER_ID` | no       | Travelata affiliate ID appended to generated search links.         |
| `GROQ_API_KEY`         | no       | Groq API key for AI blurbs (falls back to a static message if empty). |
| `PORT`                 | no       | Port for the Flask server (default `5000`).                        |

The application reads configuration from the environment. Load `.env` with your
process manager / host, or export the variables before running.

### 3. Run

```bash
python bot.py
```

The Flask server listens on `0.0.0.0:$PORT`. Expose it over HTTPS and register
the webhook with Telegram:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=https://YOUR_DOMAIN/webhook"
```

For production, run behind a WSGI server (e.g. `gunicorn bot:app`).

## License

[MIT](LICENSE) © 2026 r0meo-1
