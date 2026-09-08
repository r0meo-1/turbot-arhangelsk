# TurBot Mini App backend

## Local run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set BOT_TOKEN=...
set MANAGER_CHAT_ID=...
uvicorn main:app --reload --port 8000
```

Health check:

```text
GET http://127.0.0.1:8000/health
```

Production endpoint used by the Telegram Mini App:

```text
POST https://YOUR-DOMAIN/api/trip-request
```

Set the same URL in `miniapp/index.html` as `API_URL`.

Keep `BOT_TOKEN` only in server environment variables. Never put it into the frontend.
