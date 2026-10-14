"""Separate read-only Telegram conversation; never enters the lead FSM."""
import json
import re
import secrets
import threading
from datetime import date
from functools import wraps

from shared import alean_readonly as provider


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class AleanDialog:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.RLock()

    @_serialized
    def active(self, chat_id):
        return chat_id in self.sessions

    @_serialized
    def start(self, chat_id, send, *, mode="demo"):
        with self.lock:
            self.sessions[chat_id] = {"token": secrets.token_hex(4), "index": 0,
                                      "query": {}, "mode": mode, "scenario": "success"}
        send(chat_id, (provider.DEMO_NOTICE if mode == "demo" else "Алеан: живой поиск, наличие не подтверждено.")
             + "\n" + provider.NOTICE)
        self.prompt(chat_id, send)

    def steps(self, session):
        fields = ["kind", "destination", "arrival", "departure", "adults", "children_ages", "budget"]
        if session["query"].get("kind") == "tour":
            fields.append("origin")
        return fields

    def keyboard(self, session, rows):
        token = session["token"]
        return json.dumps({"inline_keyboard": [[{"text": text, "callback_data": f"as:{token}:{action}"}
                    for text, action in row] for row in rows]}, ensure_ascii=False)

    @_serialized
    def prompt(self, chat_id, send):
        session = self.sessions.get(chat_id)
        if not session:
            return
        session["token"] = secrets.token_hex(4)
        session["searching"] = False
        steps = self.steps(session)
        index = session["index"]
        nav = [[("← Назад", "back"), ("Отмена", "cancel")]]
        if index >= len(steps):
            lines = ["Алеан — проверьте параметры:"]
            labels = dict(kind="Поиск", destination="Направление", arrival="Заезд", departure="Выезд",
                          adults="Взрослых", children_ages="Возраст детей", budget="Бюджет на всех, ₽", origin="Вылет")
            for field in steps:
                value = session["query"].get(field)
                if field == "kind":
                    value = "Отель" if value == "hotel" else "Тур с перелётом"
                if field == "children_ages":
                    value = ", ".join(map(str, value)) or "нет"
                lines.append(f"{labels[field]}: {value}")
            lines.append(provider.NOTICE)
            send(chat_id, "\n".join(lines), reply_markup=self.keyboard(session, [[("Искать", "search")]] + nav))
            return
        field = steps[index]
        prompts = dict(kind="Что ищем в России?", destination="Направление: Сочи, Адлер, Анапа, Геленджик, Кисловодск или Россия.",
            arrival="Дата заезда (ГГГГ-ММ-ДД):", departure="Дата выезда (ГГГГ-ММ-ДД), через 1–28 ночей:",
            adults="Сколько взрослых (1–8)?", children_ages="Возраст каждого ребёнка (0–17) через запятую, или «нет»:",
            budget="Бюджет на всех в рублях (целое число, 0 — без ограничения):", origin="Город вылета, например Москва:")
        rows = []
        if field == "kind":
            rows.append([("Отель", "hotel"), ("Тур с перелётом", "tour")])
        if field == "destination":
            rows.append([("Сочи", "sochi"), ("Адлер", "adler")])
        text = "Алеан • " + prompts[field]
        if field in session["query"]:
            text += "\nСохранённый ответ: " + str(session["query"][field])
            rows.append([("Оставить ответ", "keep")])
        send(chat_id, text, reply_markup=self.keyboard(session, rows + nav))

    @_serialized
    def answer(self, chat_id, text, send):
        session = self.sessions[chat_id]
        steps = self.steps(session)
        if session["index"] >= len(steps):
            send(chat_id, "Нажмите «Искать», «Назад» или «Отмена».")
            return
        field = steps[session["index"]]
        try:
            value = text.strip()
            if field == "kind":
                value = {"отель": "hotel", "тур": "tour", "тур с перелётом": "tour"}.get(value.casefold(), value)
                if value not in ("hotel", "tour"):
                    raise ValueError("Выберите отель или тур.")
            elif field == "destination":
                value = next((d for d in provider.DESTINATIONS if d.casefold() == value.casefold()), None)
                if value is None:
                    raise ValueError("Выберите направление из списка в России.")
            elif field in ("arrival", "departure"):
                day = date.fromisoformat(value)
                if day < date.today():
                    raise ValueError("Дата не может быть в прошлом.")
                if field == "departure" and not 1 <= (day - date.fromisoformat(session["query"]["arrival"])).days <= 28:
                    raise ValueError("Выезд через 1–28 ночей после заезда.")
                value = day.isoformat()
            elif field == "adults":
                value = provider._integer(value, 1, 8, "Взрослые")
            elif field == "budget":
                value = provider._integer(value.replace(" ", ""), 0, 10000000, "Бюджет")
            elif field == "children_ages":
                value = [] if value.casefold() in ("нет", "без детей", "-") else re.split(r"[,\s]+", value)
                if len(value) > 6:
                    raise ValueError("Не более шести детей.")
                value = [provider._integer(age, 0, 17, "Возраст") for age in value]
            elif field == "origin" and not 2 <= len(value) <= 100:
                raise ValueError("Город вылета: 2–100 символов.")
        except ValueError as exc:
            # Never include unexpected exception/credential text in customer messages.
            send(chat_id, "Проверьте ответ. Даты: ГГГГ-ММ-ДД; числа и возрасты — целые. "
                 + (str(exc) if isinstance(exc, provider.ValidationError) else ""))
            self.prompt(chat_id, send)
            return
        session["query"][field] = value
        if field == "arrival" and session["query"].get("departure", "") <= value:
            session["query"].pop("departure", None)
        session["index"] += 1
        self.prompt(chat_id, send)

    @_serialized
    def text(self, chat_id, text, send):
        command = text.split("@", 1)[0] if text.startswith("/") else text
        if command in ("/alean", "/alean_live"):
            self.start(chat_id, send, mode="live" if command == "/alean_live" else "demo")
            return True
        if not self.active(chat_id):
            return False
        if command == "/start":
            self.start(chat_id, send)
        elif command in ("/cancel", "Отмена"):
            self.sessions.pop(chat_id, None)
            send(chat_id, "Поиск Алеан отменён. Ничего не отправлено. Новый демо-поиск: /alean.")
        elif command in ("/back", "Назад", "← Назад"):
            session = self.sessions[chat_id]
            session["index"] = max(0, session["index"] - 1)
            self.prompt(chat_id, send)
        elif command.startswith("/"):
            send(chat_id, "В поиске Алеан: /back — назад, /cancel — отмена. Отправка заявок отключена.")
        else:
            self.answer(chat_id, text, send)
        return True

    @_serialized
    def callback(self, chat_id, data, send):
        if data == "as:open":
            self.start(chat_id, send)
            return True
        if not data.startswith("as:"):
            if self.active(chat_id):
                send(chat_id, "Это кнопка другой заявки. В поиске Алеан отправка заявок отключена.")
                return True
            return False
        session = self.sessions.get(chat_id)
        parts = data.split(":")
        if not session or len(parts) != 3 or parts[1] != session["token"]:
            send(chat_id, "Кнопка устарела. Продолжите текущий поиск или /alean.")
            return True
        action = parts[2]
        if action in ("back", "cancel"):
            self.text(chat_id, "/" + action, send)
        elif action in ("hotel", "tour", "sochi", "adler"):
            expected = "kind" if action in ("hotel", "tour") else "destination"
            steps = self.steps(session)
            if session["index"] < len(steps) and steps[session["index"]] == expected:
                self.answer(chat_id, {"sochi": "Сочи", "adler": "Адлер"}.get(action, action), send)
        elif action == "keep":
            steps = self.steps(session)
            if session["index"] < len(steps):
                value = session["query"].get(steps[session["index"]])
                if value is not None:
                    text = (",".join(map(str, value)) or "нет") if isinstance(value, list) else str(value)
                    self.answer(chat_id, text, send)
        elif action == "edit":
            session["index"] = 0
            self.prompt(chat_id, send)
        elif action == "search" and session["index"] >= len(self.steps(session)):
            self.run_search(chat_id, session, send)
        return True

    @_serialized
    def run_search(self, chat_id, session, send):
        if session.get("searching"):
            return
        session["searching"] = True
        token = session["token"]
        query = dict(session["query"])
        send(chat_id, "Алеан: ищем варианты…", reply_markup=self.keyboard(session, [[("Отмена", "cancel")]]))

        def work():
            try:
                result = provider.search(query, mode=session["mode"], scenario=session["scenario"])
            except provider.ValidationError as exc:
                result = {"offers": [], "error": str(exc), "state": "validation"}
            except Exception:
                result = {"offers": [], "error": "Алеан временно недоступен.", "state": "error"}
            with self.lock:
                if self.sessions.get(chat_id) is not session or session["token"] != token:
                    return
                session["searching"] = False
                lines = [provider.DEMO_NOTICE if session["mode"] == "demo" else "Алеан: цена и наличие не подтверждены."]
                if result.get("error"):
                    lines.append(result["error"])
                elif not result["offers"]:
                    lines.append("Вариантов не найдено. Измените даты или бюджет.")
                for offer in result["offers"][:3]:
                    lines.append(f"\n{offer['name']} • {offer['location']}\n{offer['arrival']} → {offer['departure']}\n"
                                 f"{offer['price']:,.2f} {offer['currency']}\n{offer['availability']}")
                lines.append("\n" + provider.NOTICE)
                send(chat_id, "\n".join(lines), reply_markup=self.keyboard(session,
                     [[("Изменить параметры", "edit"), ("Повторить поиск", "search")], [("Отмена", "cancel")]]))

        if session["mode"] == "live":
            threading.Thread(target=work, daemon=True, name="alean-readonly").start()
        else:
            work()
