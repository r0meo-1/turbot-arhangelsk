Edge automation bot — Playwright + Telegram

Small example showing how to control Microsoft Edge (Playwright) from a Telegram bot.

Setup

1. Create venv and install:
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r edge_bot_requirements.txt
   playwright install

2. Configure env (or set system env):
   copy edge_bot.env.example edge_bot.env
   # edit edge_bot.env and set TELEGRAM_TOKEN

3. Run:
   python edge_bot_bot.py

Commands

/start — info
/search <query> — open Edge, search Bing, bot replies with page title

Notes
- Requires Edge installed for channel="msedge".
- Use HEADLESS=0 in edge_bot.env for visible browser during development.

## Docker
Build image:
`docker build -f docker/edge-bot/Dockerfile -t edge-bot .`nRun:
`docker run -e TELEGRAM_TOKEN=... -e HEADLESS=1 edge-bot`n
