"""One-account Telegram integration test. Not a production bot entrypoint.

No bot.py, CRM, VK, bookings, supplier clients, webhook changes or public server.
One dedicated bot + one private user chat. Real notices go to that same user,
playing the manager role. API acceptance and human /ack are separate evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from types import SimpleNamespace

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.ai_chat import generate_ai_chat_reply
from shared.ai_guardrails import redact_external_ai_text
from shared.ai_provider import build_selection_provider

CONTEXT = "СИНТЕТИЧЕСКИЙ ТЕСТ. Турция, ноябрь, 2 взрослых, ребёнок 9 лет, бюджет 180000 на всех."


@dataclass(frozen=True)
class Settings:
    token: str
    username: str
    user_id: int
    production_bot_id: int

    @classmethod
    def load(cls, values):
        result = cls(values.get("TEST_TELEGRAM_BOT_TOKEN", ""),
                     values.get("TEST_BOT_USERNAME", "").lstrip("@"),
                     int(values.get("TEST_USER_ID", "0")),
                     int(values.get("FORBIDDEN_PRODUCTION_BOT_ID", "0")))
        prefix, separator, _ = result.token.partition(":")
        if (not separator or not prefix.isdigit() or not result.username
                or result.user_id <= 0 or result.production_bot_id <= 0
                or int(prefix) == result.production_bot_id):
            raise ValueError("closed test config invalid or production token rejected")
        return result


class Telegram:
    METHODS = {"getMe", "getWebhookInfo", "getUpdates", "sendMessage"}

    def __init__(self, settings, proxy=""):
        self.settings = settings
        self.session = requests.Session()
        self.session.trust_env = False
        if proxy:
            self.session.proxies = {"https": proxy}

    def call(self, method, **payload):
        if method not in self.METHODS:
            raise ValueError("method not permitted in closed test")
        if method == "sendMessage" and payload.get("chat_id") != self.settings.user_id:
            raise ValueError("recipient outside closed allowlist")
        response = self.session.post(
            f"https://api.telegram.org/bot{self.settings.token}/{method}",
            json=payload, timeout=35 if method == "getUpdates" else 15,
        )
        data = response.json()
        if response.status_code != 200 or data.get("ok") is not True:
            # Never propagate raw API descriptions or token-bearing request URLs.
            raise RuntimeError("Telegram request not accepted")
        return data["result"]

    def preflight(self):
        identity = self.call("getMe")
        if (identity.get("id") == self.settings.production_bot_id
                or identity.get("username", "").lower() != self.settings.username.lower()
                or not identity.get("is_bot")):
            raise ValueError("dedicated bot identity mismatch")
        if self.call("getWebhookInfo").get("url"):
            raise ValueError("existing webhook: refuse to change or consume it")
        return identity

    def send(self, text):
        result = self.call("sendMessage", chat_id=self.settings.user_id, text=text[:3900])
        if result.get("chat", {}).get("id") != self.settings.user_id or not result.get("message_id"):
            raise RuntimeError("missing matching message receipt")
        return result["message_id"]


class ClosedTest:
    def __init__(self, settings, telegram, state_path, provider=None, max_ai_calls=10):
        self.settings, self.telegram = settings, telegram
        self.path = Path(state_path)
        self.provider = provider
        self.max_ai_calls = min(20, max(1, max_ai_calls))
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "last_update": -1, "ai_calls": 0, "tokens": 0, "handoffs": {}, "ignored": 0,
            "binding": f"{settings.token.split(':', 1)[0]}:{settings.user_id}",
        }
        if self.state.get("binding") != f"{settings.token.split(':', 1)[0]}:{settings.user_id}":
            raise ValueError("state belongs to a different bot or tester")

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
            json.dump(self.state, file)
        os.replace(tmp, self.path)
        self.path.chmod(0o600)

    def summary(self):
        notices = list(self.state["handoffs"].values())
        return {"ai_calls": self.state["ai_calls"], "tokens": self.state["tokens"],
                "api_accepted": sum(bool(n.get("api_accepted")) for n in notices),
                "human_acknowledged": sum(bool(n.get("human_acknowledged")) for n in notices),
                "ignored_updates": self.state["ignored"]}

    def model_client(self):
        if not self.provider or not self.provider.ready:
            return None
        def create(**kwargs):
            if self.state["ai_calls"] >= self.max_ai_calls:
                raise RuntimeError("closed test inference limit reached")
            self.state["ai_calls"] += 1
            self.save()  # Count before network call, including failures.
            response = self.provider.client.chat.completions.create(**kwargs)
            if response.usage:
                self.state["tokens"] += response.usage.total_tokens or 0
                self.save()
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def handle(self, update):
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or update_id <= self.state["last_update"]:
            return
        # At-most-once: persist BEFORE any AI/send call. Uncertain failures are
        # never retried automatically and are not claimed as delivered.
        self.state["last_update"] = update_id
        self.save()
        message = update.get("message", {})
        if (message.get("from", {}).get("id") != self.settings.user_id
                or message.get("from", {}).get("is_bot")
                or message.get("chat", {}).get("id") != self.settings.user_id
                or message.get("chat", {}).get("type") != "private"):
            self.state["ignored"] += 1
            self.save()
            return
        text = str(message.get("text", "")).strip()
        if text == "/start":
            self.telegram.send(
                "Закрытый синтетический тест TurBot. Доступ только вашему аккаунту. "
                "Заявки, поиск туров, бронь и оплата отсутствуют.\n"
                "Контекст: Турция, ноябрь, 2 взрослых и ребёнок 9 лет, бюджет 180000.\n"
                "Напишите /ask Что взять в поездку? Затем /manager для теста передачи. "
                "Уведомление менеджеру придёт вам же; подтвердите его командой /ack из уведомления."
            )
            return
        if text == "/status":
            self.telegram.send("ТЕСТ: " + json.dumps(self.summary(), ensure_ascii=False))
            return
        if text.startswith("/ack "):
            test_id = text[5:].strip()
            event = self.state["handoffs"].get(test_id)
            if event and event.get("api_accepted"):
                event["human_acknowledged"] = True
                event["ack_at"] = int(time.time())
                self.save()
                self.telegram.send("ТЕСТ: получение уведомления подтверждено вами. " + test_id)
            else:
                self.telegram.send("ТЕСТ: нет подтверждённой API-отправки с таким ID.")
            return
        if text == "/manager":
            question = "Хочу менеджера"
        elif text.startswith("/ask "):
            question = text[5:].strip()
        elif text.casefold().startswith("ии:"):
            question = text.split(":", 1)[1].strip()
        elif not text.startswith("/") and text:
            question = text
        else:
            self.telegram.send("ТЕСТ: команды /ask, /manager, /ack ID, /status.")
            return
        if len(question) > 800:
            self.telegram.send("ТЕСТ: максимум 800 символов; используйте только синтетические данные.")
            return
        reply = generate_ai_chat_reply(
            question, enabled=True, groq_client=self.model_client(),
            groq_model=self.provider.model if self.provider else "",
            verified_context=CONTEXT, timeout=10,
        )
        try:
            self.telegram.send("ТЕСТ · " + reply.text)
        except Exception as exc:
            self.state["client_send_error"] = type(exc).__name__
            self.save()
        if reply.handoff_required or reply.reason in {"provider_error", "provider_unavailable"}:
            if len(self.state["handoffs"]) >= 10:
                self.telegram.send("ТЕСТ: лимит уведомлений исчерпан.")
                return
            test_id = secrets.token_hex(6)
            event = {"reason": reply.reason, "api_accepted": False,
                     "human_acknowledged": False, "created_at": int(time.time())}
            self.state["handoffs"][test_id] = event
            self.save()
            notice = ("🧪 ТЕСТ — уведомление менеджеру; реальной заявки нет.\n"
                      f"ID: {test_id}\nПричина: {reply.reason}\n"
                      f"Вопрос: {redact_external_ai_text(question)[:500]}\n"
                      f"Для подтверждения получения: /ack {test_id}")
            try:
                event["message_id"] = self.telegram.send(notice)
                event["api_accepted"] = True
            except Exception as exc:
                event["error_class"] = type(exc).__name__
            self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ai-env", required=True, type=Path)
    parser.add_argument("--proxy-env", type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--duration", type=int, default=3600)
    args = parser.parse_args()
    from dotenv import dotenv_values
    logging.disable(logging.CRITICAL)
    provider = telegram = None
    try:
        settings = Settings.load(dotenv_values(args.config, interpolate=False))
        ai = dotenv_values(args.ai_env, interpolate=False)
        proxy = os.getenv("TELEGRAM_PROXY_URL", "")
        if args.proxy_env:
            proxy = dotenv_values(args.proxy_env, interpolate=False).get("TELEGRAM_PROXY_URL", "") or proxy
        telegram = Telegram(settings, proxy)
        identity = telegram.preflight()  # Only read-only API calls.
        if not args.run:
            print(json.dumps({"preflight_ok": True, "test_bot": identity["username"], "run": False}))
            return 0
        # Linux runtime lock prevents competing pollers and state-file writers.
        import fcntl
        with args.state.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            provider = build_selection_provider("yandex", yandex_api_key=ai.get("YANDEX_AI_API_KEY", ""),
                                                yandex_model=ai.get("YANDEX_AI_MODEL", ""))
            if not provider.ready:
                raise ValueError("AI not configured")
            test = ClosedTest(settings, telegram, args.state, provider)
            deadline = time.monotonic() + min(3600, max(1, args.duration))
            print(json.dumps({"running": True, "test_bot": identity["username"]}), flush=True)
            while time.monotonic() < deadline:
                updates = telegram.call("getUpdates", offset=test.state["last_update"] + 1,
                                        timeout=20, allowed_updates=["message"])
                for update in updates:
                    test.handle(update)
            print(json.dumps(test.summary()), flush=True)
    except Exception as exc:
        print(json.dumps({"error_class": type(exc).__name__, "started_or_delivered": "not_implied"}))
        return 1
    finally:
        if provider and provider.client:
            provider.client.close()
        if telegram:
            telegram.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
