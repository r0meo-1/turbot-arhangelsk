"""One-account webhook integration; no public listener or production webhook.

Bot code runs with sockets and workers disabled. Its real Telegram sender uses
an intercepted HTTP session; only the exact scoped test transport may do I/O.
"""
from contextlib import contextmanager, ExitStack
from collections import OrderedDict
import copy
import json
import os
import secrets
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import requests
from tools.closed_telegram_ai import ClosedTest, Telegram


class WebhookTelegram(Telegram):
    METHODS = Telegram.METHODS | {"answerCallbackQuery", "editMessageReplyMarkup"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callback_ids = set()

    def call(self, method, **payload):
        if method == "editMessageReplyMarkup" and payload.get("chat_id") != self.settings.user_id:
            raise ValueError("foreign callback chat")
        if method == "answerCallbackQuery" and payload.get("callback_query_id") not in self.callback_ids:
            raise ValueError("unobserved callback")
        return super().call(method, **payload)


class ClosedWebhookTest(ClosedTest):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_connect = socket.socket.connect
        self.original_connect_ex = socket.socket.connect_ex
        self.original_dns = socket.getaddrinfo

    def allowed(self, update):
        query = update.get("callback_query")
        message = (query or {}).get("message", {}) if query else update.get("message", {})
        sender = (query or message).get("from", {})
        return (sender.get("id") == self.settings.user_id and not sender.get("is_bot")
                and message.get("chat", {}).get("id") == self.settings.user_id
                and message.get("chat", {}).get("type") == "private"
                and (not query or (message.get("from", {}).get("id") == int(self.settings.token.split(":")[0])
                     and message.get("message_id") in self.state.get("owned_messages", []))))

    @contextmanager
    def offline(self, probe=False):
        import dotenv
        with ExitStack() as stack:
            stack.enter_context(patch.object(dotenv, "load_dotenv", return_value=False))
            stack.enter_context(patch.object(threading.Thread, "start", return_value=None))
            stack.enter_context(patch.object(socket.socket, "connect", side_effect=RuntimeError("offline bot")))
            stack.enter_context(patch.object(socket.socket, "connect_ex", side_effect=RuntimeError("offline bot")))
            stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=RuntimeError("offline bot")))
            database = str(self.path.parent / ("probe.sqlite" if probe else "webhook.sqlite"))
            env = {"BOT_TOKEN":"closed-webhook-dummy", "DATABASE_PATH":database,
                   "VK_DATABASE_PATH":database+'.vk', "AI_MODE":"template", "ADMIN_ID":"999",
                   "VK_ACCESS_TOKEN":"", "REGCLOUD_API_KEY":"", "GROQ_API_KEY":"",
                   "YANDEX_AI_API_KEY":"", "DATA_RETENTION_DAYS":"0", "FOLLOWUP_DELAY_HOURS":"0",
                   "DIALOG_TIMEOUT_HOURS":"0", "MDT_ENABLED":"false", "ADMIN_ERROR_ALERTS":"false",
                   "WEBSITE_LEAD_WORKER_ENABLED":"false"}
            stack.enter_context(patch.dict(os.environ, env))
            import bot
            stack.enter_context(patch.multiple(bot, DATABASE_PATH=database,
                BOT_TOKEN="closed-webhook-dummy", TELEGRAM_SECRET_TOKEN="closed-local-secret",
                ADMIN_ERROR_ALERTS=False, VK_ACCESS_TOKEN="", LEAD_OWNER_VK_ID=0,
                LEAD_NOTIFY_IDS=[self.settings.user_id], ADMIN_ID=999,
                AI_LEAD_ASSIST_ENABLED=True, user_data={}, all_users={},
                _dirty_sessions=set(), _dirty_users=set(), _seen_update_ids=OrderedDict()))
            bot.init_db()
            bot.load_state()
            if self.settings.user_id not in bot.user_data:
                bot.user_data[self.settings.user_id] = {"state":bot.STATE_DESTINATION,
                    "destination":"Турция", "people":"2", "kids_ages":[9],
                    "budget":180000, "budget_scope":"total", "dates":"ноябрь"}
                bot.set_session(self.settings.user_id, bot.user_data[self.settings.user_id])
            yield bot

    def handle(self, update):
        number = update.get("update_id")
        if type(number) is not int or number <= self.state["last_update"]:
            return
        if not self.allowed(update):
            self.state["last_update"] = number
            self.state["ignored"] += 1
            self.save()
            return
        self.current_update = copy.deepcopy(update)
        if "callback_query" in update:
            self.state["last_update"] = number
            self.save()
            if not str(update["callback_query"].get("data", "")).startswith("aiq:"):
                return
            self.telegram.callback_ids.add(update["callback_query"]["id"])
            self.run_webhook(update)
        elif update["message"].get("text") == "/start":
            self.state["last_update"] = number
            self.save()
            with self.offline() as bot:
                markup = bot.kb_ai_lead_assist()
            result = self.telegram.call("sendMessage", chat_id=self.settings.user_id,
                text="🧪 Закрытый тест webhook TurBot. Только синтетические данные. Выберите AI-вопрос или /ask Хочу менеджера. Заявки не создаются.",
                reply_markup=json.loads(markup))
            self.remember_message(result)
        else:
            # Keep acknowledgement/control commands local; only the explicit
            # AI surface reaches the production dispatcher, never admin/funnel.
            text = str(update["message"].get("text", ""))
            if not (text.startswith("/ask") or text.casefold().startswith("ии:")
                    or text in {"/manager", "/status"} or text.startswith("/ack ")):
                self.state["last_update"] = number
                self.save()
                return
            super().handle(update)

    def remember_message(self, result):
        if (result.get("chat", {}).get("id") != self.settings.user_id
                or type(result.get("message_id")) is not int):
            raise RuntimeError("no matching test receipt")
        self.state.setdefault("owned_messages", []).append(result["message_id"])
        self.save()

    def generate_reply(self, question):
        update = copy.deepcopy(self.current_update)
        if update["message"].get("text") == "/manager":
            update["message"]["text"] = "/ask Хочу менеджера"
        self.run_webhook(update)

    def deliver_reply(self, question, reply):
        pass  # The real sender already executed inside the final webhook pass.

    def run_webhook(self, update):
        captured = []
        def capture(**kw):
            captured.append(kw)
            raise RuntimeError("offline model capture")

        def execute(create, probe):
            with self.offline(probe) as bot:
                payload = copy.deepcopy(update)
                if probe:
                    payload.pop("update_id", None)
                def post(url, **kw):
                    method = url.rsplit('/',1)[-1]
                    if not url.startswith("https://api.telegram.org/botclosed-webhook-dummy/"):
                        raise ValueError("non Telegram request blocked")
                    data = dict(kw.get("json") or {})
                    if method not in {"sendMessage","sendChatAction","answerCallbackQuery","editMessageReplyMarkup"}:
                        raise ValueError("method blocked")
                    if method != "answerCallbackQuery" and data.get("chat_id") != self.settings.user_id:
                        raise ValueError("foreign recipient blocked")
                    if probe or method == "sendChatAction":
                        result = {"chat":{"id":self.settings.user_id},"message_id":1}
                    else:
                        event = None
                        if method == "sendMessage" and str(data.get("text","")).startswith("🤖→"):
                            if len(self.state["handoffs"]) >= 10:
                                raise RuntimeError("notice quota reached")
                            key = secrets.token_hex(6)
                            event = {"reason":"webhook_handoff", "api_accepted":False,
                                     "human_acknowledged":False,"created_at":int(time.time())}
                            self.state["handoffs"][key] = event
                            self.save()
                            data["text"] = "🧪 ТЕСТ — реальной заявки нет.\n" + data["text"] + f"\nПодтвердите получение: /ack {key}"
                        elif method == "sendMessage":
                            data["text"] = "🧪 ТЕСТ · " + data.get("text","")
                        # Only this validated transport executes with sockets
                        # restored. No bot code/worker runs during its I/O.
                        with patch.object(socket.socket,"connect",self.original_connect), \
                             patch.object(socket.socket,"connect_ex",self.original_connect_ex), \
                             patch.object(socket,"getaddrinfo",self.original_dns):
                            result = self.telegram.call(method, **data)
                        if method == "sendMessage":
                            self.remember_message(result)
                        if event is not None:
                            event.update(api_accepted=True,message_id=result["message_id"])
                            self.save()
                    response = requests.Response()
                    response.status_code=200
                    response._content=json.dumps({"ok":True,"result":result}).encode()
                    return response
                provider = SimpleNamespace(ready=True,model=self.provider.model,
                    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
                with patch.multiple(bot, telegram_session=SimpleNamespace(post=post),
                    selection_ai_provider=provider, send_lead_owner_vk=lambda *a:False):
                    response = bot.app.test_client().post('/webhook',json=payload,
                        headers={"X-Telegram-Bot-Api-Secret-Token":"closed-local-secret"})
                if response.status_code != 200:
                    raise RuntimeError("webhook incomplete; do not replay automatically")
        execute(capture,True)
        response = None
        if captured:
            try:
                response = self.model_client().chat.completions.create(**captured[0])
            except Exception:
                pass
        def replay(**kw):
            if response is None:
                raise RuntimeError("provider unavailable")
            return response
        execute(replay,False)
        self.state["webhook_updates"] = self.state.get("webhook_updates",0)+1
        self.save()
