import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from aiogram import Bot
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


BOT_TOKEN = os.environ["BOT_TOKEN"]
MANAGER_CHAT_ID = int(os.environ["MANAGER_CHAT_ID"])
MAX_INIT_DATA_AGE_SECONDS = int(os.getenv("MAX_INIT_DATA_AGE_SECONDS", "3600"))

bot = Bot(BOT_TOKEN)
app = FastAPI(title="TurBot API")

allowed_origins = [
    origin.strip()
    for origin in os.getenv("MINI_APP_ORIGINS", "").split(",")
    if origin.strip()
]
if not allowed_origins:
    allowed_origins = ["https://r0meo-1.github.io"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["POST"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
)


class TripRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=50)
    duration: str = Field(min_length=1, max_length=100)
    tourists: str = Field(min_length=1, max_length=100)
    budget: str = Field(min_length=1, max_length=100)
    start_param: str | None = Field(default=None, max_length=64)


ALLOWED_DESTINATIONS = {"Таиланд", "Вьетнам"}
ALLOWED_START_PARAMS = {"landing", "thailand", "vietnam"}


def validate_init_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram initData missing")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)

    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram hash missing")

    auth_date_raw = values.get("auth_date")
    if not auth_date_raw:
        raise HTTPException(status_code=401, detail="Telegram auth_date missing")

    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid auth_date")

    if abs(int(time.time()) - auth_date) > MAX_INIT_DATA_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Telegram initData expired")

    data_check_string = "\n".join(
        f"{key}={values[key]}"
        for key in sorted(values)
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")

    if "user" in values:
        try:
            values["user"] = json.loads(values["user"])
        except json.JSONDecodeError:
            raise HTTPException(status_code=401, detail="Invalid Telegram user payload")

    return values


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/trip-request")
async def create_trip_request(
    request: TripRequest,
    x_telegram_init_data: str = Header(...),
):
    tg_data = validate_init_data(x_telegram_init_data)

    if request.destination not in ALLOWED_DESTINATIONS:
        raise HTTPException(status_code=422, detail="Unsupported destination")

    start_param = request.start_param or "landing"
    if start_param not in ALLOWED_START_PARAMS:
        start_param = "landing"

    user = tg_data.get("user") or {}
    user_id = user.get("id")
    first_name = user.get("first_name") or "—"
    username = user.get("username")

    username_line = f"🔗 Username: @{username}\n" if username else ""

    message = (
        "📥 <b>Новая заявка TurBot</b>\n\n"
        f"🌍 Направление: {request.destination}\n"
        f"🌙 Когда / длительность: {request.duration}\n"
        f"👥 Туристы: {request.tourists}\n"
        f"💰 Бюджет: {request.budget}\n"
        f"📊 Источник: {start_param}\n\n"
        f"👤 Клиент: {first_name}\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        f"{username_line}"
    )

    await bot.send_message(
        chat_id=MANAGER_CHAT_ID,
        text=message,
        parse_mode="HTML",
    )

    return {"ok": True}
