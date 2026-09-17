from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from shared import vk_booking_support as support


class FakeBot:
    ADMIN_ID = 999
    LEAD_NOTIFY_IDS: list[int] = []
    HTTP_TIMEOUT = 15
    DEMO_MODE = False
    USER_HELP = "Справка"

    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)
        self._db_lock = threading.Lock()
        self._lock = threading.Lock()
        self.user_data: dict[int, dict] = {}
        self.sent: list[tuple[int, str, dict]] = []
        self.deleted_sessions: list[int] = []
        self.original_calls: list[dict] = []
        self.logger = logging.getLogger("test-vk-booking-support")
        self.http_session = None
        self._booking_support_installed = False
        self._process_message = lambda event: self.original_calls.append(event)
        self._soft_start_keyboard = lambda: json.dumps(
            {"one_time": False, "inline": False, "buttons": [[self._btn("Начать", "positive")]]},
            ensure_ascii=False,
        )

    @contextmanager
    def _db_cursor(self, commit: bool = False):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with self._db_lock:
                cur = conn.cursor()
                yield cur
                if commit:
                    conn.commit()
        finally:
            conn.close()

    def send_message(self, user_id: int, text: str, **kwargs):
        self.sent.append((user_id, text, kwargs))
        return {"message_id": len(self.sent)}

    def delete_session(self, user_id: int) -> None:
        self.deleted_sessions.append(user_id)

    @staticmethod
    def _btn(label: str, color: str = "primary") -> dict:
        return {"action": {"type": "text", "label": label}, "color": color}

    @staticmethod
    def _keyboard(rows: list[list[dict]]) -> str:
        return json.dumps(
            {"one_time": False, "inline": False, "buttons": rows},
            ensure_ascii=False,
        )


def _event(user_id: int, text: str, *, attachments=None) -> dict:
    return {
        "object": {
            "message": {
                "from_id": user_id,
                "peer_id": user_id,
                "text": text,
                "attachments": attachments or [],
            }
        }
    }


def _submit_request(bot: FakeBot, user_id: int = 101) -> int:
    assert support.handle_message(bot, _event(user_id, "Моя бронь"))
    assert support.handle_message(bot, _event(user_id, "12115906"))
    assert support.handle_message(bot, _event(user_id, "Mikhail Korelskii"))
    assert support.handle_message(bot, _event(user_id, "исправить фамилию на KORELSKII"))
    assert support.handle_message(bot, _event(user_id, "Подтвердить"))
    with bot._db_cursor() as cur:
        row = cur.execute(
            f"SELECT * FROM {support.REQUEST_TABLE} WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    assert row is not None
    return int(row["id"])


def test_booking_support_collects_safe_request_and_persists_it(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    request_id = _submit_request(bot)

    with bot._db_cursor() as cur:
        row = cur.execute(
            f"SELECT * FROM {support.REQUEST_TABLE} WHERE id = ?", (request_id,)
        ).fetchone()
        session = cur.execute(
            f"SELECT * FROM {support.SESSION_TABLE} WHERE user_id = 101"
        ).fetchone()

    assert row["booking_no"] == "12115906"
    assert row["tourist_name"] == "Mikhail Korelskii"
    assert row["change_text"] == "исправить фамилию на KORELSKII"
    assert row["status"] == "new"
    assert session is None
    assert "Статус брони 12115906" in bot.sent[-1][1]


def test_support_rejects_passport_number_and_attachments(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    user_id = 202
    support.handle_message(bot, _event(user_id, "Моя бронь"))
    support.handle_message(bot, _event(user_id, "12115906"))
    support.handle_message(bot, _event(user_id, "Ivan Ivanov"))

    assert support.handle_message(bot, _event(user_id, "паспорт 1234567890"))
    session = support._get_session(bot, user_id)
    assert session["stage"] == "change"
    assert session["change_text"] is None
    assert "не сохраняю" in bot.sent[-1][1]

    assert support.handle_message(
        bot,
        _event(user_id, "исправить паспорт", attachments=[{"type": "photo"}]),
    )
    session = support._get_session(bot, user_id)
    assert session["stage"] == "change"


def test_status_command_returns_latest_request(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    _submit_request(bot, 303)

    assert support.handle_message(bot, _event(303, "Статус брони 12115906"))
    assert "Принято менеджером" in bot.sent[-1][1]


def test_parse_oti_closed_email_and_update_customer(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    request_id = _submit_request(bot, 404)
    body = """
    Обращение было закрыто № REQ-ARF-12115906-9.2669088
    Детали обращения приведены ниже:
    Номер обращения REQ-ARF-12115906-9.2669088
    Тема Бронирование
    Вопрос обращения Коррекция паспортных данных
    Статус Закрыто
    Описание Просим отредактировать данные туриста
    Номер заявки 12115906
    """

    parsed = support.parse_oti_message(body)
    assert parsed == {
        "booking_no": "12115906",
        "case_no": "REQ-ARF-12115906-9.2669088",
        "operator_status": "Закрыто",
    }

    assert support.apply_operator_update(bot, parsed) == request_id
    with bot._db_cursor() as cur:
        row = cur.execute(
            f"SELECT status, external_case_no, operator_status FROM {support.REQUEST_TABLE} WHERE id = ?",
            (request_id,),
        ).fetchone()
    assert row["status"] == "closed"
    assert row["external_case_no"] == "REQ-ARF-12115906-9.2669088"
    assert row["operator_status"] == "Закрыто"
    assert bot.sent[-1][0] == 404
    assert "Туроператор закрыл обращение" in bot.sent[-1][1]


def test_admin_can_paste_oti_email_and_customer_is_notified(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    _submit_request(bot, 505)
    body = (
        "Обращение было закрыто № REQ-ARF-12115906-9.2669088\n"
        "Статус Закрыто\nНомер заявки 12115906"
    )

    assert support.handle_message(bot, _event(bot.ADMIN_ID, body))
    assert any(uid == 505 and "закрыл обращение" in text for uid, text, _ in bot.sent)
    assert bot.sent[-1][0] == bot.ADMIN_ID
    assert "OTI обновлён" in bot.sent[-1][1]


def test_install_adds_existing_booking_button_and_intercepts_trigger(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    support.install(bot)

    assert bot._booking_support_installed is True
    keyboard = json.loads(bot._soft_start_keyboard())
    labels = [
        button["action"]["label"]
        for row in keyboard["buttons"]
        for button in row
    ]
    assert support.BOOKING_BUTTON_TEXT in labels
    assert "Моя бронь" in bot.USER_HELP

    bot._process_message(_event(606, "Моя бронь"))
    assert support._get_session(bot, 606)["stage"] == "booking"
    assert bot.original_calls == []

    normal = _event(607, "какой сегодня день")
    bot._process_message(normal)
    assert bot.original_calls == [normal]
