"""Existing-booking support workflow for the production VK bot.

The tour-selection FSM is intentionally not reused here. A customer who already
has a booking needs a tiny, durable service flow: identify the booking, identify
the tourist, describe the correction, and hand it to the manager.

Passport scans and passport numbers are deliberately rejected in VK chat. The
bot records only enough information to locate the booking and understand what
needs changing; sensitive document values belong in the tour operator's
protected agent cabinet.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional


SESSION_TABLE = "vk_booking_support_sessions"
REQUEST_TABLE = "booking_support_requests"
BOOKING_BUTTON_TEXT = "🧾 Моя бронь"

_TRIGGER_TEXTS = {
    "моя бронь",
    "🧾 моя бронь",
    "бронь",
    "существующая бронь",
    "изменить данные",
    "исправить данные",
    "исправить паспорт",
    "коррекция паспортных данных",
}
_CANCEL_TEXTS = {"отмена", "отменить", "❌ отмена"}
_CONFIRM_TEXTS = {"подтвердить", "✅ подтвердить", "да", "верно"}
_STATUS_RE = re.compile(
    r"^(?:статус\s+брони|бронь\s+статус|статус\s+заявки)\s+([A-Za-zА-Яа-я0-9._/-]{4,40})$",
    re.IGNORECASE,
)
_BOOKING_RE = re.compile(r"^[A-Za-zА-Яа-я0-9._/-]{4,40}$")
_SENSITIVE_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_OTI_CASE_RE = re.compile(r"\b(REQ-[A-ZА-Я0-9-]+(?:\.\d+)?)\b", re.IGNORECASE)
_OTI_BOOKING_RE = re.compile(
    r"Номер\s+заявки\s*[:\-]?\s*([A-Za-zА-Яа-я0-9._/-]{4,40})",
    re.IGNORECASE,
)
_OTI_STATUS_RE = re.compile(
    r"Статус\s*[:\-]?\s*(Закрыто|Закрыт|Открыто|Открыт|В работе|На рассмотрении|Отменено|Отменен|Отменён)",
    re.IGNORECASE,
)


def ensure_schema(bot: Any) -> None:
    """Create durable support tables in the VK SQLite database."""
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SESSION_TABLE} (
                user_id INTEGER PRIMARY KEY,
                stage TEXT NOT NULL,
                booking_no TEXT,
                tourist_name TEXT,
                change_text TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {REQUEST_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'vk',
                booking_no TEXT NOT NULL,
                tourist_name TEXT NOT NULL,
                change_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                external_case_no TEXT,
                operator_status TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUEST_TABLE}_user_booking "
            f"ON {REQUEST_TABLE}(user_id, booking_no, id)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUEST_TABLE}_booking "
            f"ON {REQUEST_TABLE}(booking_no, id)"
        )


def _extract_event(event: dict[str, Any]) -> tuple[int, str, list[Any]]:
    msg = event.get("object", {}).get("message", event.get("message", {}))
    user_id = int(msg.get("from_id") or msg.get("peer_id") or 0)
    text = str(msg.get("text") or "").strip()
    attachments = list(msg.get("attachments") or [])
    return user_id, text, attachments


def _get_session(bot: Any, user_id: int) -> Optional[dict[str, Any]]:
    with bot._db_cursor() as cur:
        row = cur.execute(
            f"SELECT * FROM {SESSION_TABLE} WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _upsert_session(bot: Any, user_id: int, **fields: Any) -> None:
    current = _get_session(bot, user_id) or {}
    now = int(time.time())
    data = {
        "stage": fields.get("stage", current.get("stage", "booking")),
        "booking_no": fields.get("booking_no", current.get("booking_no")),
        "tourist_name": fields.get("tourist_name", current.get("tourist_name")),
        "change_text": fields.get("change_text", current.get("change_text")),
    }
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT INTO {SESSION_TABLE}
                (user_id, stage, booking_no, tourist_name, change_text, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                stage=excluded.stage,
                booking_no=excluded.booking_no,
                tourist_name=excluded.tourist_name,
                change_text=excluded.change_text,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                data["stage"],
                data["booking_no"],
                data["tourist_name"],
                data["change_text"],
                int(current.get("created_at") or now),
                now,
            ),
        )


def _clear_session(bot: Any, user_id: int) -> None:
    with bot._db_cursor(commit=True) as cur:
        cur.execute(f"DELETE FROM {SESSION_TABLE} WHERE user_id = ?", (user_id,))


def _keyboard(bot: Any, *, confirm: bool = False) -> Optional[str]:
    try:
        rows = []
        if confirm:
            rows.append([bot._btn("✅ Подтвердить", "positive")])
        rows.append([bot._btn("Отмена", "negative")])
        return bot._keyboard(rows)
    except Exception:
        return None


def _send(bot: Any, user_id: int, text: str, *, confirm: bool = False) -> None:
    keyboard = _keyboard(bot, confirm=confirm)
    if keyboard is None:
        bot.send_message(user_id, text)
    else:
        bot.send_message(user_id, text, keyboard=keyboard)


def _stop_tour_selection(bot: Any, user_id: int) -> bool:
    """Remove any unfinished tour-selection session so it cannot follow-up."""
    with bot._lock:
        had_selection = user_id in bot.user_data
        bot.user_data.pop(user_id, None)
    # Delete unconditionally: after a process restart SQLite may still have a
    # session even if the in-memory cache has not been restored yet.
    try:
        if bot.get_session(user_id) is not None:
            had_selection = True
        bot.delete_session(user_id)
    except Exception:
        bot.logger.exception("Could not delete VK tour session before booking support")
    return had_selection


def start(bot: Any, user_id: int) -> None:
    had_selection = _stop_tour_selection(bot, user_id)
    _clear_session(bot, user_id)
    _upsert_session(bot, user_id, stage="booking")
    prefix = (
        "Текущий подбор тура остановлен, чтобы не смешивать две заявки.\n\n"
        if had_selection
        else ""
    )
    _send(
        bot,
        user_id,
        prefix
        + "🧾 Помогу передать изменение по уже существующей брони.\n\n"
        "1/3. Напишите номер заявки туроператора. Например: 12115906.\n\n"
        "🔐 Важно: не отправляйте сюда фото паспорта, серию или номер документа. "
        "Такие данные менеджер вносит только в защищённом кабинете туроператора.",
    )


def _safe_booking_no(text: str) -> Optional[str]:
    value = re.sub(r"\s+", "", text.strip())
    return value.upper() if _BOOKING_RE.fullmatch(value) else None


def _looks_sensitive(text: str, attachments: list[Any]) -> bool:
    return bool(attachments or _SENSITIVE_NUMBER_RE.search(text))


def _status_label(status: str) -> str:
    return {
        "new": "Принято менеджером, ждёт обработки",
        "operator_pending": "Передано туроператору, ждём ответ",
        "closed": "Туроператор закрыл обращение",
        "cancelled": "Обращение отменено",
    }.get(status, status or "Неизвестно")


def show_status(bot: Any, user_id: int, booking_no: str) -> None:
    booking_no = booking_no.upper()
    with bot._db_cursor() as cur:
        row = cur.execute(
            f"""
            SELECT id, booking_no, status, external_case_no, operator_status, updated_at
            FROM {REQUEST_TABLE}
            WHERE user_id = ? AND UPPER(booking_no) = ?
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, booking_no),
        ).fetchone()
    if row is None:
        bot.send_message(
            user_id,
            f"По брони {booking_no} у меня пока нет обращения. "
            "Чтобы создать его, напишите «Моя бронь».",
        )
        return
    text = f"🧾 Бронь {row['booking_no']}\nСтатус: {_status_label(str(row['status']))}"
    if row["external_case_no"]:
        text += f"\nОбращение туроператора: {row['external_case_no']}"
    if row["operator_status"]:
        text += f"\nСтатус туроператора: {row['operator_status']}"
    bot.send_message(user_id, text)


def _notify_manager(bot: Any, request_id: int, user_id: int, session: dict[str, Any]) -> None:
    recipients = list(getattr(bot, "LEAD_NOTIFY_IDS", []) or [])
    token = os.getenv("BOT_TOKEN", "").strip()
    if not recipients or not token:
        bot.logger.warning(
            "VK booking support #%s stored but Telegram manager notification is unavailable",
            request_id,
        )
        return
    text = (
        "🧾 Изменение по существующей брони из VK\n\n"
        f"Внутренний запрос: #{request_id}\n"
        f"VK: https://vk.com/id{user_id}\n"
        f"Номер брони: {session['booking_no']}\n"
        f"Турист: {session['tourist_name']}\n"
        f"Что изменить: {session['change_text']}\n\n"
        "⚠️ Паспортные номера в VK не собирались. Если нужны реквизиты документа, "
        "запросите их у клиента по защищённому каналу и внесите в агентском кабинете."
    )
    for recipient in recipients:
        try:
            response = bot.http_session.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": recipient, "text": text, "disable_web_page_preview": True},
                timeout=getattr(bot, "HTTP_TIMEOUT", 15),
            )
            if getattr(response, "status_code", 0) != 200:
                bot.logger.warning(
                    "VK booking support #%s Telegram notify failed: HTTP %s",
                    request_id,
                    getattr(response, "status_code", "?"),
                )
        except Exception:
            bot.logger.exception("VK booking support #%s Telegram notify failed", request_id)


def _submit(bot: Any, user_id: int, session: dict[str, Any]) -> int:
    now = int(time.time())
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT INTO {REQUEST_TABLE}
                (user_id, source, booking_no, tourist_name, change_text, status, created_at, updated_at)
            VALUES (?, 'vk', ?, ?, ?, 'new', ?, ?)
            """,
            (
                user_id,
                session["booking_no"],
                session["tourist_name"],
                session["change_text"],
                now,
                now,
            ),
        )
        request_id = int(cur.lastrowid)
    _clear_session(bot, user_id)
    if not bool(getattr(bot, "DEMO_MODE", False)):
        _notify_manager(bot, request_id, user_id, session)
    return request_id


def parse_oti_message(text: str) -> Optional[dict[str, str]]:
    """Extract the stable fields from an OTI Holding CRM status email/body."""
    compact = " ".join(str(text or "").replace("\xa0", " ").split())
    case_match = _OTI_CASE_RE.search(compact)
    booking_match = _OTI_BOOKING_RE.search(compact)
    status_match = _OTI_STATUS_RE.search(compact)
    if not booking_match or not status_match:
        return None
    return {
        "booking_no": booking_match.group(1).upper(),
        "case_no": case_match.group(1).upper() if case_match else "",
        "operator_status": status_match.group(1),
    }


def _internal_status_from_operator(operator_status: str) -> str:
    value = operator_status.casefold()
    if value.startswith("закрыт"):
        return "closed"
    if value.startswith("отмен"):
        return "cancelled"
    return "operator_pending"


def apply_operator_update(bot: Any, update: dict[str, str]) -> Optional[int]:
    booking_no = update["booking_no"].upper()
    now = int(time.time())
    with bot._db_cursor(commit=True) as cur:
        row = cur.execute(
            f"""
            SELECT id, user_id FROM {REQUEST_TABLE}
            WHERE UPPER(booking_no) = ?
            ORDER BY id DESC LIMIT 1
            """,
            (booking_no,),
        ).fetchone()
        if row is None:
            return None
        request_id = int(row["id"])
        user_id = int(row["user_id"])
        status = _internal_status_from_operator(update["operator_status"])
        cur.execute(
            f"""
            UPDATE {REQUEST_TABLE}
            SET status = ?, external_case_no = ?, operator_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                update.get("case_no") or None,
                update["operator_status"],
                now,
                request_id,
            ),
        )
    case = f" ({update['case_no']})" if update.get("case_no") else ""
    if status == "closed":
        client_text = (
            f"✅ Туроператор закрыл обращение{case} по брони {booking_no}.\n\n"
            "Менеджер проверит изменения в агентском кабинете. Если понадобится "
            "уточнение, мы свяжемся с вами."
        )
    elif status == "cancelled":
        client_text = f"⚠️ Обращение{case} по брони {booking_no} отменено туроператором."
    else:
        client_text = (
            f"⏳ Обращение{case} по брони {booking_no} зарегистрировано у туроператора. "
            f"Статус: {update['operator_status']}."
        )
    bot.send_message(user_id, client_text)
    return request_id


def _handle_admin_operator_message(bot: Any, user_id: int, text: str) -> bool:
    if user_id != int(getattr(bot, "ADMIN_ID", 0) or 0):
        return False
    if "номер заявки" not in text.casefold() or "статус" not in text.casefold():
        return False
    update = parse_oti_message(text)
    if update is None:
        return False
    request_id = apply_operator_update(bot, update)
    if request_id is None:
        bot.send_message(
            user_id,
            f"OTI: вижу бронь {update['booking_no']}, но связанного запроса TurBot нет.",
        )
    else:
        bot.send_message(
            user_id,
            f"✅ OTI обновлён: запрос #{request_id}, бронь {update['booking_no']}, "
            f"статус «{update['operator_status']}». Клиент уведомлён.",
        )
    return True


def handle_message(bot: Any, event: dict[str, Any]) -> bool:
    """Return True when the booking-support flow consumed the VK message."""
    user_id, text, attachments = _extract_event(event)
    if not user_id:
        return False

    if _handle_admin_operator_message(bot, user_id, text):
        return True

    lowered = text.casefold().strip()
    session = _get_session(bot, user_id)

    if session is None:
        status_match = _STATUS_RE.fullmatch(text.strip())
        if status_match:
            show_status(bot, user_id, status_match.group(1))
            return True
        if lowered in _TRIGGER_TEXTS:
            start(bot, user_id)
            return True
        return False

    if lowered in _CANCEL_TEXTS:
        _clear_session(bot, user_id)
        bot.send_message(
            user_id,
            "Отменил черновик изменения по брони. Чтобы начать заново — напишите «Моя бронь».",
        )
        return True

    stage = str(session["stage"])
    if stage == "booking":
        booking_no = _safe_booking_no(text)
        if booking_no is None:
            _send(bot, user_id, "Не похоже на номер заявки. Пришлите только номер брони, без пояснений.")
            return True
        _upsert_session(bot, user_id, stage="tourist", booking_no=booking_no)
        _send(
            bot,
            user_id,
            "2/3. Напишите имя и фамилию туриста так, как они сейчас указаны в бронировании. "
            "Для зарубежного тура лучше латиницей.",
        )
        return True

    if stage == "tourist":
        if attachments or len(text) < 3 or len(text) > 120 or _SENSITIVE_NUMBER_RE.search(text):
            _send(
                bot,
                user_id,
                "Нужны только имя и фамилия туриста текстом. Паспортные данные и документы сюда не отправляйте.",
            )
            return True
        _upsert_session(bot, user_id, stage="change", tourist_name=text.strip())
        _send(
            bot,
            user_id,
            "3/3. Коротко напишите, что нужно исправить. Например: «фамилию на KORELSKII», "
            "«дату рождения» или «срок действия паспорта».\n\n"
            "Не пишите серию/номер паспорта и не прикладывайте его фото.",
        )
        return True

    if stage == "change":
        if _looks_sensitive(text, attachments):
            _send(
                bot,
                user_id,
                "🔐 Похоже, сообщение содержит номер документа или вложение. Я это не сохраняю. "
                "Напишите только, что именно нужно исправить, без серии/номера паспорта.",
            )
            return True
        if len(text) < 3 or len(text) > 300:
            _send(bot, user_id, "Опишите изменение одной короткой фразой, до 300 символов.")
            return True
        _upsert_session(bot, user_id, stage="confirm", change_text=text.strip())
        session = _get_session(bot, user_id) or {}
        _send(
            bot,
            user_id,
            "Проверьте запрос:\n\n"
            f"Бронь: {session.get('booking_no')}\n"
            f"Турист: {session.get('tourist_name')}\n"
            f"Изменить: {session.get('change_text')}\n\n"
            "Подтвердить передачу менеджеру?",
            confirm=True,
        )
        return True

    if stage == "confirm":
        if lowered not in _CONFIRM_TEXTS:
            _send(
                bot,
                user_id,
                "Нажмите «Подтвердить» или напишите «Отмена».",
                confirm=True,
            )
            return True
        if bool(getattr(bot, "DEMO_MODE", False)):
            _clear_session(bot, user_id)
            bot.send_message(user_id, "Это демо-версия: реальное изменение по брони не отправлено.")
            return True
        request_id = _submit(bot, user_id, session)
        bot.send_message(
            user_id,
            "✅ Запрос передан менеджеру.\n\n"
            f"Номер запроса TurBot: #{request_id}\n"
            f"Бронь: {session['booking_no']}\n\n"
            f"Проверить позже: «Статус брони {session['booking_no']}».\n"
            "Если туроператор создаст отдельное обращение, его номер тоже появится в статусе.",
        )
        return True

    _clear_session(bot, user_id)
    return False


def _inject_start_button(bot: Any, raw_keyboard: str) -> str:
    try:
        payload = json.loads(raw_keyboard)
        rows = payload.get("buttons")
        if not isinstance(rows, list):
            return raw_keyboard
        for row in rows:
            for button in row if isinstance(row, list) else []:
                label = ((button or {}).get("action") or {}).get("label")
                if label == BOOKING_BUTTON_TEXT:
                    return raw_keyboard
        rows.append([bot._btn(BOOKING_BUTTON_TEXT, "secondary")])
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        bot.logger.exception("Could not add existing-booking button to VK keyboard")
        return raw_keyboard


def install(bot: Any) -> None:
    """Install the existing-booking flow into an imported ``vk_bot`` module."""
    if getattr(bot, "_booking_support_installed", False):
        return
    ensure_schema(bot)

    original_process = bot._process_message
    original_soft_keyboard = bot._soft_start_keyboard

    def process_message(event: dict[str, Any]) -> Any:
        try:
            if handle_message(bot, event):
                return None
        except Exception:
            bot.logger.exception("VK booking support failed; falling back to normal bot flow")
        return original_process(event)

    def soft_start_keyboard() -> str:
        return _inject_start_button(bot, original_soft_keyboard())

    bot._process_message = process_message
    bot._soft_start_keyboard = soft_start_keyboard
    if "Моя бронь" not in str(getattr(bot, "USER_HELP", "")):
        bot.USER_HELP = str(getattr(bot, "USER_HELP", "")) + (
            "\n\n🧾 Уже есть бронь? Напишите «Моя бронь» — передам менеджеру запрос "
            "на исправление данных без отправки паспортных номеров в VK."
        )
    bot._booking_support_installed = True
    bot.logger.info("VK existing-booking support installed")
