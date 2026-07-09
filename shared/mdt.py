"""MoiDokumenti-Turism (MDT) CRM integration shared by Telegram and VK bots."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from shared.dates import parse_russian_dates

logger = logging.getLogger("turbot.shared.mdt")

RequestFn = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]


@dataclass
class MDTSettings:
    """Runtime MDT configuration (read live from bot env globals)."""

    enabled: bool = False
    account: str = ""
    api_key: str = ""
    source: str = "Bot"
    base_url: str = ""
    mode: str = "lead"  # lead | preorder | both
    notify_managers: bool = False
    manager_ids: List[int] = field(default_factory=list)
    reminder_enabled: bool = True
    reminder_days: int = 1
    reminder_text: str = "Позвонить по заявке"
    timeout: int = 15
    # Platform-specific labels
    name_prefix: str = "Client"  # "Telegram" / "VK"
    tourist_tags: str = "Bot"
    push_title: str = "Новая заявка с бота"


def base_url(settings: MDTSettings) -> str:
    if settings.base_url:
        return settings.base_url.rstrip("/")
    if settings.account:
        return f"https://{settings.account}.moidokumenti.ru"
    return ""


def http_request(
    settings: MDTSettings,
    session: requests.Session,
    method: str,
    params: Dict[str, Any],
    log: Optional[logging.Logger] = None,
) -> Optional[Dict[str, Any]]:
    """POST to MDT API. Returns parsed JSON or None on failure."""
    log = log or logger
    base = base_url(settings)
    if not base or not settings.api_key:
        log.warning("MDT is not configured: missing base URL or API key")
        return None
    url = f"{base}/api/{method}"
    payload = {
        "params": json.dumps(params, ensure_ascii=False),
        "key": settings.api_key,
    }
    try:
        resp = session.post(url, data=payload, timeout=settings.timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("MDT request %s failed: %s", method, exc)
        return None


def parse_country_list(result: Any) -> Dict[str, int]:
    """Parse get-country-list response into lowercase name → id."""
    cache: Dict[str, int] = {}
    if result is None:
        return cache
    data = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(data, dict):
        for key, value in data.items():
            try:
                cid = int(key)
            except (ValueError, TypeError):
                continue
            name = (
                value
                if isinstance(value, str)
                else (value.get("name", "") if isinstance(value, dict) else "")
            )
            if name:
                cache[name.strip().lower()] = cid
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                cid = item.get("id")
                name = item.get("name", "")
                if cid is not None and name:
                    cache[name.strip().lower()] = int(cid)
    return cache


def match_country_id(cache: Dict[str, int], destination: str) -> int:
    """Match destination text to an MDT country ID. Returns 0 if none."""
    if not cache:
        return 0
    dest_lower = destination.strip().lower()
    if not dest_lower:
        return 0
    if dest_lower in cache:
        return cache[dest_lower]
    for cached_name, cid in cache.items():
        if cached_name in dest_lower or dest_lower in cached_name:
            return cid
    return 0


def extract_id(result: Any, *keys: str) -> Optional[int]:
    """Pull a numeric id from various MDT response shapes."""
    if result is None:
        return None
    data = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(data, dict):
        for key in keys or ("id",):
            val = data.get(key)
            if val is not None:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    continue
    if isinstance(data, (int, str)):
        try:
            return int(data)
        except (ValueError, TypeError):
            pass
    return None


def _parse_persons(people: Any) -> int:
    cleaned = re.sub(r"[^\d]", "", str(people or ""))
    return int(cleaned) if cleaned else 0


def _parse_budget(budget: Any) -> int:
    if isinstance(budget, (int, float)):
        return int(budget)
    try:
        return int(re.sub(r"[^\d]", "", str(budget)))
    except (ValueError, TypeError):
        return 0


def add_tourist_temp(
    settings: MDTSettings,
    name: str,
    phone: str,
    request_fn: RequestFn,
    log: Optional[logging.Logger] = None,
) -> Optional[int]:
    log = log or logger
    result = request_fn(
        "add-tourist-temp",
        {"name": name, "tel": phone, "tags": settings.tourist_tags},
    )
    tid = extract_id(result, "id", "tourist_id")
    if tid is None and result is not None:
        log.warning("Could not extract tourist ID from add-tourist-temp: %s", result)
    return tid


def create_preorder(
    settings: MDTSettings,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    country_cache: Dict[str, int],
    request_fn: RequestFn,
    log: Optional[logging.Logger] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """Create temp tourist + preorder. Returns (preorder_id, tourist_id)."""
    log = log or logger
    name = client_name or f"{settings.name_prefix} {chat_id}"
    tourist_id = add_tourist_temp(settings, name, phone, request_fn, log=log)
    if tourist_id is None:
        log.warning("Failed to create temp tourist in MDT for chat %s", chat_id)
        return None, None

    country_id = match_country_id(country_cache, info.get("destination", "") or "")
    date_from, date_to = parse_russian_dates(info.get("dates", "") or "")
    persons = _parse_persons(info.get("people"))
    budget = _parse_budget(info.get("budget", 0))

    comment_parts = []
    if info.get("destination"):
        comment_parts.append(f"Направление: {info['destination']}")
    if info.get("dates"):
        comment_parts.append(f"Даты: {info['dates']}")
    if info.get("people"):
        comment_parts.append(f"Человек: {info['people']}")
    if budget:
        comment_parts.append(f"Бюджет: {budget}₽")

    params: Dict[str, Any] = {
        "tourist_type": "tourist_temp",
        "tourist_id": tourist_id,
        "country_id1": country_id,
        "country_id2": 0,
        "country_id3": 0,
        "persons": persons,
        "children": 0,
        "children_ages": [],
        "price_from": 0,
        "price_to": budget,
        "comment": " | ".join(comment_parts),
        "wait_for_hot": 0,
    }
    if date_from:
        params["flightdate_from"] = date_from
    if date_to:
        params["flightdate_to"] = date_to

    result = request_fn("create-preorder", params)
    if result is None:
        log.warning("Failed to create preorder in MDT for chat %s", chat_id)
        return None, None

    preorder_id = extract_id(result, "id", "preorder_id")
    log.info(
        "Preorder created in MDT for chat %s (ID: %s, tourist: %s)",
        chat_id,
        preorder_id,
        tourist_id,
    )
    return preorder_id, tourist_id


def create_lead(
    settings: MDTSettings,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    request_fn: RequestFn,
    log: Optional[logging.Logger] = None,
) -> bool:
    log = log or logger
    fields = []
    if info.get("destination"):
        fields.append({"name": "Направление", "values": [info["destination"]]})
    if info.get("dates"):
        fields.append({"name": "Даты", "values": [info["dates"]]})
    if info.get("people"):
        fields.append({"name": "Количество человек", "values": [str(info["people"])]})
    if info.get("budget"):
        fields.append({"name": "Бюджет", "values": [str(info["budget"])]})

    params = {
        "name": client_name or f"{settings.name_prefix} {chat_id}",
        "phone": phone,
        "email": "",
        "source": settings.source,
        "fields": fields,
    }
    result = request_fn("add-lead", params)
    if result is not None:
        log.info("Lead sent to MDT for chat %s", chat_id)
        return True
    log.warning("Failed to send lead to MDT for chat %s", chat_id)
    return False


def notify_managers(
    settings: MDTSettings,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    request_fn: RequestFn,
    log: Optional[logging.Logger] = None,
) -> None:
    log = log or logger
    if not settings.manager_ids:
        log.warning("MDT_NOTIFY_MANAGERS is on but MDT_MANAGER_IDS is empty")
        return

    text_parts = []
    if client_name:
        text_parts.append(f"Клиент: {client_name}")
    if info.get("destination"):
        text_parts.append(f"Направление: {info['destination']}")
    if info.get("dates"):
        text_parts.append(f"Даты: {info['dates']}")
    if info.get("people"):
        text_parts.append(f"Человек: {info['people']}")
    if info.get("budget"):
        text_parts.append(f"Бюджет: {info['budget']}₽")
    text_parts.append(f"Телефон: {phone}")

    result = request_fn(
        "send-push",
        {
            "manager_ids": settings.manager_ids,
            "title": settings.push_title,
            "text": "\n".join(text_parts),
        },
    )
    if result is not None:
        log.info("Push notification sent to MDT managers for chat %s", chat_id)
    else:
        log.warning("Failed to send push to MDT managers for chat %s", chat_id)


def add_reminder(
    settings: MDTSettings,
    preorder_id: int,
    tourist_id: int,
    manager_id: int,
    reminder_date: str,
    request_fn: RequestFn,
    reminder_time: str = "10:00:00",
    log: Optional[logging.Logger] = None,
) -> bool:
    log = log or logger
    result = request_fn(
        "add-reminder",
        {
            "date": reminder_date,
            "time": reminder_time,
            "text": settings.reminder_text,
            "tourist_type": "tourist_temp",
            "tourist_id": tourist_id,
            "manager_id": manager_id,
            "preorder_id": preorder_id,
            "only_one_manager": False,
        },
    )
    if result is not None:
        log.info(
            "MDT reminder created for manager %s (preorder %s)",
            manager_id,
            preorder_id,
        )
        return True
    log.warning(
        "Failed to create MDT reminder for manager %s (preorder %s)",
        manager_id,
        preorder_id,
    )
    return False


def create_reminders_for_preorder(
    settings: MDTSettings,
    chat_id: int,
    preorder_id: Optional[int],
    tourist_id: Optional[int],
    request_fn: RequestFn,
    log: Optional[logging.Logger] = None,
) -> None:
    log = log or logger
    if not settings.reminder_enabled:
        return
    if not (preorder_id and tourist_id):
        log.debug("Skipping MDT reminder: missing preorder_id or tourist_id")
        return
    if not settings.manager_ids:
        log.warning("MDT_REMINDER_ENABLED is on but MDT_MANAGER_IDS is empty")
        return

    reminder_date = (
        datetime.now() + timedelta(days=settings.reminder_days)
    ).strftime("%Y-%m-%d")
    for manager_id in settings.manager_ids:
        add_reminder(
            settings,
            preorder_id,
            tourist_id,
            manager_id,
            reminder_date,
            request_fn,
            log=log,
        )


def dispatch_lead(
    settings: MDTSettings,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    country_cache: Dict[str, int],
    request_fn: RequestFn,
    log: Optional[logging.Logger] = None,
) -> None:
    """Send completed bot request to MDT according to settings.mode."""
    if not settings.enabled:
        return

    success = False
    mode = settings.mode if settings.mode in ("lead", "preorder", "both") else "lead"

    if mode in ("lead", "both"):
        success = create_lead(
            settings, chat_id, info, phone, client_name, request_fn, log=log
        ) or success

    preorder_id: Optional[int] = None
    tourist_id: Optional[int] = None

    if mode in ("preorder", "both"):
        preorder_id, tourist_id = create_preorder(
            settings,
            chat_id,
            info,
            phone,
            client_name,
            country_cache,
            request_fn,
            log=log,
        )
        success = (preorder_id is not None) or success
        create_reminders_for_preorder(
            settings, chat_id, preorder_id, tourist_id, request_fn, log=log
        )

    if success and settings.notify_managers:
        notify_managers(
            settings, chat_id, info, phone, client_name, request_fn, log=log
        )
