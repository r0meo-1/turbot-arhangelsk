"""Offline main-router execution, followed by the closed harness transport.

bot.py never receives credentials or network access. Only /ask and ИИ: reach it.
The dedicated Telegram transport and SDK run outside the offline router scope.
"""
import os
import socket
import threading
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from tools.closed_telegram_ai import ClosedTest


class MainRouterTest(ClosedTest):
    def handle(self, update):
        text = str(update.get("message", {}).get("text", ""))
        self.route_prefix = "ИИ: " if text.casefold().startswith("ии:") else "/ask "
        return super().handle(update)

    def generate_reply(self, question):
        # Import with no .env loading, no workers and no network. All bot state
        # lives beside the test receipt, never in the production checkout.
        import dotenv
        database = str(self.path.parent / "router.sqlite")
        with ExitStack() as stack:
            stack.enter_context(patch.object(dotenv, "load_dotenv", return_value=False))
            stack.enter_context(patch.object(threading.Thread, "start", return_value=None))
            for name in ("connect", "connect_ex"):
                stack.enter_context(patch.object(socket.socket, name, side_effect=RuntimeError("offline router")))
            stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=RuntimeError("offline router")))
            env = {"BOT_TOKEN": "closed-router-dummy", "AI_MODE": "template",
                   "DATABASE_PATH": database, "VK_DATABASE_PATH": database + ".vk",
                   "ADMIN_ID": "999", "VK_ACCESS_TOKEN": "", "GROQ_API_KEY": "",
                   "REGCLOUD_API_KEY": "", "YANDEX_AI_API_KEY": "",
                   "DATA_RETENTION_DAYS": "0", "DIALOG_TIMEOUT_HOURS": "0",
                   "FOLLOWUP_DELAY_HOURS": "0", "ADMIN_ERROR_ALERTS": "false",
                   "MDT_ENABLED": "false", "WEBSITE_LEAD_WORKER_ENABLED": "false"}
            stack.enter_context(patch.dict(os.environ, env))
            import bot
            outputs, notices, model_requests = [], [], []

            # The offline SDK bridge records its request; inference is performed
            # separately, then the same main router is replayed with that result.
            def need_model(**kwargs):
                model_requests.append(kwargs)
                raise RuntimeError("offline model bridge")

            def run(create):
                outputs.clear(); notices.clear()
                bot.user_data[self.settings.user_id] = {
                    "state": bot.STATE_DESTINATION, "destination": "Турция",
                    "people": "2", "budget": 180000, "kids_ages": [9]}
                def send(cid, text, **kwargs):
                    if cid != self.settings.user_id:
                        raise ValueError("foreign router recipient")
                    (notices if text.startswith("🤖→") else outputs).append(text)
                    return SimpleNamespace(ok=True, status_code=200)
                with patch.multiple(bot, AI_LEAD_ASSIST_ENABLED=True,
                        ADMIN_ID=999, LEAD_NOTIFY_IDS=[self.settings.user_id],
                        selection_ai_provider=SimpleNamespace(ready=True, model=self.provider.model,
                            client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))),
                        send_message=send, send_typing=lambda *a: None,
                        send_lead_owner_vk=lambda *a: False,
                        record_funnel_event=lambda *a, **k: None,
                        _touch_user=lambda *a, **k: None):
                    bot._process_update({"message": {"chat": {"id": self.settings.user_id, "type": "private"},
                        "from": {"id": self.settings.user_id, "first_name": "Синтетический тест"},
                        "text": getattr(self, "route_prefix", "/ask ") + question}})
            run(need_model)
        if model_requests:
            # This is the sole network-capable model operation; no bot code runs
            # while its captured request is submitted to the bounded SDK client.
            try:
                response = self.model_client().chat.completions.create(**model_requests[0])
                create = lambda **kw: response
            except Exception:
                def create(**kw):
                    raise RuntimeError("provider bridge failed")
            with patch.object(socket.socket, "connect", side_effect=RuntimeError("offline router")), \
                 patch.object(socket.socket, "connect_ex", side_effect=RuntimeError("offline router")), \
                 patch.object(socket, "getaddrinfo", side_effect=RuntimeError("offline router")), \
                 patch.object(threading.Thread, "start", return_value=None):
                run(create)
        reason = "main_router_handoff" if notices else ""
        if notices:
            for line in notices[0].splitlines():
                if line.startswith("Причина: "):
                    reason = line[9:]
        self.state["main_router_checks"] = self.state.get("main_router_checks", 0) + 1
        self.save()
        return SimpleNamespace(text=outputs[0] if outputs else "Нет ответа обработчика.",
                               handoff_required=bool(notices), reason=reason)
