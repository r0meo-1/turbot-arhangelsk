"""Drive the real bot through a full funnel and capture what it actually sends.

Not a mockup: this imports bot.py, posts real Telegram-shaped updates to the
real Flask webhook, and records every outgoing sendMessage payload. The only
thing stubbed is the Telegram transport itself — the dialog logic, the SQLite
writes and the Tutu MCP search all run for real against the live server.
"""

import json
import os
import tempfile

os.environ.setdefault("BOT_TOKEN", "demo:token")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("CONSENT_MODE", "soft")
os.environ.setdefault("AI_MODE", "template")
os.environ.setdefault("TELEGRAM_SECRET_TOKEN", "demo-secret")
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".sqlite")
os.environ["STATE_FILE"] = tempfile.mktemp(suffix=".json")
# Run MDT/Tutu/AI inline so the capture is deterministic and ordered.
os.environ["SYNC_COMPLETION"] = "true"
os.environ["TUTU_ENABLED"] = "true"
os.environ["TUTU_SHOW_CLIENT"] = "true"
os.environ["TUTU_SHOW_ADMIN"] = "true"

import bot  # noqa: E402

CAPTURED = []
CLIENT_ID = 424242
ADMIN_ID = 999


class _Resp:
    status_code = 200

    def json(self):
        return {"ok": True, "result": {}}


def _capture(chat_id, text, parse_mode=None, reply_markup=None):
    buttons = []
    if reply_markup:
        try:
            markup = json.loads(reply_markup)
            for row in markup.get("inline_keyboard", []):
                buttons.append([b.get("text", "") for b in row])
            for row in markup.get("keyboard", []):
                buttons.append([
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in row
                ])
        except Exception:
            pass
    CAPTURED.append({
        "to": "admin" if chat_id == ADMIN_ID else "client",
        "chat_id": chat_id,
        "text": text,
        "html": parse_mode == "HTML",
        "buttons": buttons,
    })
    return _Resp()


bot.send_message = _capture
bot.send_typing = lambda *a, **k: None
bot.LEAD_NOTIFY_IDS = [ADMIN_ID]
bot.set_bot_commands = lambda *a, **k: None

app = bot.app.test_client()
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "demo-secret"}
_uid = [1000]


def say(text=None, callback=None, contact=None):
    """Post one Telegram update, recording the user's own turn first."""
    _uid[0] += 1
    if callback:
        CAPTURED.append({"to": "user_tap", "text": callback["label"]})
        update = {
            "update_id": _uid[0],
            "callback_query": {
                "id": str(_uid[0]),
                "from": {"id": CLIENT_ID, "first_name": "Роман"},
                "message": {"chat": {"id": CLIENT_ID}, "message_id": _uid[0]},
                "data": callback["data"],
            },
        }
    else:
        CAPTURED.append({"to": "user", "text": contact or text})
        message = {
            "chat": {"id": CLIENT_ID},
            "from": {"id": CLIENT_ID, "first_name": "Роман", "username": "roman"},
        }
        if contact:
            message["contact"] = {"phone_number": contact, "user_id": CLIENT_ID}
        else:
            message["text"] = text
        update = {"update_id": _uid[0], "message": message}
    resp = app.post("/webhook", json=update, headers=HEADERS)
    assert resp.status_code == 200, resp.status_code


# --- the funnel, exactly as a real client walks it -------------------------
say("/start")
say(callback={"data": bot.CB_START, "label": "🚀 Начать подбор"})
say(callback={"data": f"{bot.CB_DEST_PREFIX}0", "label": bot.POPULAR_DESTINATIONS[0]})
say(callback={"data": f"{bot.CB_ORIGIN_PREFIX}1", "label": bot.ORIGIN_OPTIONS[1]})
say("15-22 сентября 2026")
say(callback={"data": f"{bot.CB_PEOPLE_PREFIX}2", "label": "2"})
say("до года, 7")
say(callback={"data": f"{bot.CB_BUDGET_PREFIX}3", "label": bot.BUDGET_PRESETS[3][0]})
say(callback={"data": bot.CB_CONTACT_PHONE, "label": "📱 Телефон"})
say(contact="+79211234567")

with open("demo_capture.json", "w", encoding="utf-8") as fh:
    json.dump(CAPTURED, fh, ensure_ascii=False, indent=1)

leads = bot.count_leads()
print(f"captured {len(CAPTURED)} events, leads in SQLite: {leads}")
for item in CAPTURED:
    tag = item["to"]
    preview = item["text"].replace("\n", " ⏎ ")[:110]
    print(f"  [{tag:9}] {preview}")
