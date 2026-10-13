"""Local real-handler preview. No external transport, lead, or manager delivery."""
import argparse
import itertools
import os
from pathlib import Path
import sys
import signal
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_app(database):
    import requests

    for name in ("MDT_ENABLED", "TUTU_ENABLED", "VK_MDT_RETRY_ENABLED", "MDT_RETRY_ENABLED",
                 "ADMIN_ERROR_ALERTS", "ALEAN_ENABLED", "ALEAN_READONLY_LIVE_ENABLED"):
        os.environ[name] = "false"
    for name in ("DIALOG_TIMEOUT_HOURS", "FOLLOWUP_DELAY_HOURS", "DATA_RETENTION_DAYS",
                 "PARTNER_ANALYTICS_RETENTION_DAYS", "FUNNEL_ANALYTICS_RETENTION_DAYS", "OPS_METRICS_RETENTION_DAYS"):
        os.environ[name] = "0"
    os.environ.update(ALEAN_DEMO_ONLY="true", BOT_TOKEN="offline-demo-token", ADMIN_ID="0",
                      DATABASE_PATH=str(database), BOT_MODE="webhook", DEMO_MODE="true",
                      BOT_DISPLAY_NAME="", BOT_DESCRIPTION="", BOT_SHORT_DESCRIPTION="")

    def offline(*args, **kwargs):
        raise RuntimeError("External transport disabled in local demo")

    requests.Session.request = offline
    import bot
    from flask import jsonify, request, send_from_directory

    chat_id = 990001  # Fictional local-only customer; never a Telegram recipient.
    messages = []
    ids = itertools.count(1)
    lock = threading.RLock()

    def capture(recipient, text, **kwargs):
        if recipient != chat_id:
            raise RuntimeError("Manager/customer delivery outside local preview is forbidden")
        messages.append({"role": "bot", "text": text,
                         "markup": __import__("json").loads(kwargs.get("reply_markup") or "{}")})

    bot.send_message = capture
    bot.answer_callback = lambda *args, **kwargs: None
    bot.clear_inline_keyboard = lambda *args, **kwargs: None
    bot._notify_admin = bot.save_lead = bot.send_lead_to_mdt = offline

    @bot.app.get("/demo/telegram")
    def preview():
        return send_from_directory(ROOT / "tools", "alean_demo_chat.html")

    @bot.app.post("/demo/telegram/update")
    def update():
        body = request.get_json() or {}
        with lock:
            session = bot._alean_dialog.sessions.get(chat_id)
            if session:
                session["scenario"] = body.get("scenario", "success")
            person = {"id": chat_id, "first_name": "Демо"}
            if body.get("callback"):
                data = {"callback_query": {"id": str(next(ids)), "data": body["callback"],
                        "from": person, "message": {"message_id": 1, "chat": {"id": chat_id}}}}
            else:
                text = str(body.get("text", ""))[:200]
                messages.append({"role": "user", "text": text})
                data = {"message": {"text": text, "chat": {"id": chat_id}, "from": person}}
            data["update_id"] = next(ids)
            bot.dispatch_update(data, strict=True)
            return jsonify(messages=messages, leads=bot.count_leads(), mock=True)

    return bot.app


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="alean-demo-") as directory:
        app = build_app(Path(directory) / "demo.sqlite")
        # Local-only preview, never an externally accessible Telegram webhook.
        app.run(host="127.0.0.1", port=options.port, debug=False, use_reloader=False)
