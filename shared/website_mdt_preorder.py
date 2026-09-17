"""Durable Website -> MDT preorder delivery.

The public website stores a lead locally first. MDT's ``add-lead`` endpoint is
useful for generic lead fields, but the CRM grid only gets structured country,
travel dates and manager data through ``create-preorder``. That operation is
multi-step (temporary tourist + preorder), so this module checkpoints each MDT
ID locally before moving to the next step. A retry therefore reuses work already
accepted by MDT instead of creating another tourist or preorder.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from shared import mdt as mdt_shared
from shared.dates import parse_russian_dates
from shared.mdt_live_compat import _live_result_is_success

logger = logging.getLogger("turbot.website.mdt_preorder")

_INSTALLED = False


def _rows(result: Any) -> List[Dict[str, Any]]:
    """Normalize common MDT list response shapes to dictionaries."""
    if result is None:
        return []
    data = result
    if isinstance(data, dict):
        if "data" in data:
            data = data.get("data")
        elif "result" in data and isinstance(data.get("result"), (dict, list)):
            data = data.get("result")
    if isinstance(data, list):
        return [dict(row) for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows: List[Dict[str, Any]] = []
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            row = dict(value)
            row.setdefault("id", key)
            rows.append(row)
        return rows
    return []


def _nested_id(result: Any, *keys: str) -> Optional[int]:
    """Extract an MDT ID without mistaking boolean ``result: true`` for ID 1."""
    direct = mdt_shared.extract_id(result, *keys)
    if direct is not None:
        return direct
    if not isinstance(result, dict) or "result" not in result:
        return None
    nested = result.get("result")
    if isinstance(nested, bool):
        return None
    return mdt_shared.extract_id(nested, *keys)


def _migrate_schema(website_app: Any) -> None:
    """Add durable MDT checkpoint columns without rebuilding the live table."""
    with website_app._bot._db_cursor(commit=True) as cur:
        cur.execute("PRAGMA table_info(website_leads)")
        columns = {str(row[1]) for row in cur.fetchall()}
        additions = {
            "mdt_tourist_id": "INTEGER",
            "mdt_preorder_id": "INTEGER",
            "mdt_manager_id": "INTEGER",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                cur.execute(f"ALTER TABLE website_leads ADD COLUMN {name} {sql_type}")


def _settings(website_app: Any) -> mdt_shared.MDTSettings:
    settings = website_app._mdt_settings()
    settings.mode = "preorder"
    settings.manager_ids = list(
        getattr(website_app._bot, "MDT_MANAGER_IDS", None) or []
    )
    return settings


def _country_cache(website_app: Any) -> Dict[str, int]:
    cache = getattr(website_app._bot, "_mdt_country_cache", None)
    if isinstance(cache, dict) and cache:
        return cache

    result = website_app._bot._mdt_request("get-country-list", {})
    parsed = mdt_shared.parse_country_list(result)
    if isinstance(cache, dict) and parsed:
        cache.clear()
        cache.update(parsed)
        return cache
    return parsed


def _extract_preorder_id(result: Any) -> Optional[int]:
    return _nested_id(result, "id", "preorder_id")


def _checkpoint(website_app: Any, lead_id: int, **values: Any) -> None:
    allowed = {"mdt_tourist_id", "mdt_preorder_id", "mdt_manager_id"}
    items = [(key, value) for key, value in values.items() if key in allowed]
    if not items:
        return
    sql = ", ".join(f"{key}=?" for key, _ in items)
    params = [value for _, value in items] + [int(lead_id)]
    with website_app._bot._db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE website_leads SET {sql} WHERE id=?", params)


def _mark_synced(website_app: Any, lead_id: int, attempts: int) -> None:
    now = int(time.time())
    with website_app._bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE website_leads
            SET mdt_status='synced', mdt_attempts=?, mdt_next_retry_at=NULL,
                mdt_synced_at=?
            WHERE id=?
            """,
            (int(attempts), now, int(lead_id)),
        )


def _mark_pending(website_app: Any, lead_id: int, attempts: int) -> None:
    now = int(time.time())
    with website_app._bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE website_leads
            SET mdt_status='pending', mdt_attempts=?, mdt_next_retry_at=?
            WHERE id=?
            """,
            (
                int(attempts),
                now + int(website_app._retry_delay(attempts)),
                int(lead_id),
            ),
        )


def _recover_tourist(
    website_app: Any,
    *,
    name: str,
    phone: str,
    delivery_key: str,
) -> Optional[int]:
    """Recover an ambiguously-created temp tourist using a unique delivery tag."""
    result = website_app._bot._mdt_request(
        "get-tourist-temp-list",
        {
            "count": 100,
            "offset": 0,
            "fields": ["id", "name", "tel", "tags"],
            "search": phone,
        },
    )
    for row in _rows(result):
        tags = str(row.get("tags") or "")
        if delivery_key not in tags:
            continue
        if str(row.get("name") or "").strip() != name.strip():
            continue
        try:
            tourist_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if tourist_id > 0:
            return tourist_id
    return None


def _ensure_tourist(
    website_app: Any,
    *,
    lead_id: int,
    name: str,
    phone: str,
    manager_id: Optional[int],
    checkpoint_id: Optional[int],
) -> Optional[int]:
    if checkpoint_id:
        return int(checkpoint_id)

    delivery_key = f"web-lead-{int(lead_id)}"
    recovered = _recover_tourist(
        website_app,
        name=name,
        phone=phone,
        delivery_key=delivery_key,
    )
    if recovered:
        _checkpoint(website_app, lead_id, mdt_tourist_id=recovered)
        logger.info("Recovered MDT tourist checkpoint for Website lead %s", lead_id)
        return recovered

    params: Dict[str, Any] = {
        "name": name,
        "tel": phone,
        "tags": f"Website {delivery_key}",
    }
    if manager_id is not None:
        params["manager_id"] = int(manager_id)
    result = website_app._bot._mdt_request("add-tourist-temp", params)
    tourist_id = _nested_id(result, "id", "tourist_id")
    if tourist_id is None:
        # Do not assume a result-only response is safe here. We need the ID for
        # create-preorder; a later retry can recover it via the unique tag.
        return None

    _checkpoint(website_app, lead_id, mdt_tourist_id=int(tourist_id))
    return int(tourist_id)


def _recover_preorder(
    website_app: Any,
    *,
    tourist_id: int,
    delivery_key: str,
) -> Optional[int]:
    """Find a preorder already created after an ambiguous network response."""
    result = website_app._bot._mdt_request(
        "get-preorder-list",
        {
            "count": 100,
            "offset": 0,
            "fields": ["id", "tourist_id", "comment"],
            "tourist_id": int(tourist_id),
            "tourist_type": "tourist_temp",
        },
    )
    for row in _rows(result):
        if delivery_key not in str(row.get("comment") or ""):
            continue
        try:
            preorder_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if preorder_id > 0:
            return preorder_id
    return None


def _resolve_manager(
    website_app: Any,
    *,
    settings: mdt_shared.MDTSettings,
    lead_id: int,
    checkpoint: Optional[int],
) -> Optional[int]:
    # 0 is a durable checkpoint meaning "intentionally unassigned". This avoids
    # asking MDT for the same multi-manager answer on every retry.
    if checkpoint is not None:
        return int(checkpoint) if int(checkpoint) > 0 else None

    manager_id = mdt_shared.resolve_manager_id(
        settings,
        website_app._bot._mdt_request,
        log=logger,
    )
    _checkpoint(
        website_app,
        lead_id,
        mdt_manager_id=int(manager_id) if manager_id is not None else 0,
    )
    return manager_id


def _preorder_params(
    website_app: Any,
    *,
    lead_id: int,
    payload: Dict[str, Any],
    tourist_id: int,
    manager_id: Optional[int],
) -> Dict[str, Any]:
    delivery_key = f"web-lead-{int(lead_id)}"
    country_id = mdt_shared.match_country_id(
        _country_cache(website_app),
        str(payload.get("destination") or ""),
    )
    date_from, date_to = parse_russian_dates(str(payload.get("dates") or ""))
    people_digits = re.sub(r"[^\d]", "", str(payload.get("people") or ""))
    persons = int(people_digits) if people_digits else 0
    try:
        budget = int(payload.get("budget") or 0)
    except (TypeError, ValueError):
        budget = 0

    comment = " | ".join(
        part
        for part in (
            f"Направление: {payload.get('destination')}",
            f"Вылет: {payload.get('origin')}",
            f"Даты: {payload.get('dates')}",
            f"Количество человек: {payload.get('people')}",
            f"Бюджет: {budget} ₽ на всю поездку" if budget else "",
            f"Источник: {website_app._WEBSITE_SOURCE}",
            f"ID заявки бота: {delivery_key}",
        )
        if part and not part.endswith(": None")
    )

    params: Dict[str, Any] = {
        "tourist_type": "tourist_temp",
        "tourist_id": int(tourist_id),
        "country_id1": int(country_id or 0),
        "country_id2": 0,
        "country_id3": 0,
        "persons": persons,
        "children": 0,
        "children_ages": [],
        "price_from": 0,
        "price_to": budget,
        "comment": comment,
        "wait_for_hot": 0,
    }
    if date_from:
        params["flightdate_from"] = date_from
    if date_to:
        params["flightdate_to"] = date_to
    if manager_id is not None:
        params["preorder_manager_id"] = int(manager_id)
    return params


def _deliver_structured_preorder(website_app: Any, lead_id: int) -> bool:
    """Deliver one locally stored website lead to structured MDT fields."""
    if website_app._bot.DEMO_MODE or not website_app._bot.MDT_ENABLED:
        return False

    with website_app._bot._db_cursor() as cur:
        cur.execute(
            """
            SELECT mdt_status, mdt_attempts, mdt_payload,
                   mdt_tourist_id, mdt_preorder_id, mdt_manager_id
            FROM website_leads WHERE id=?
            """,
            (int(lead_id),),
        )
        row = cur.fetchone()
    if not row:
        return False
    if row[0] == "synced":
        return True
    if row[0] == "hold":
        return False

    attempts = int(row[1] or 0) + 1
    try:
        payload = json.loads(row[2])
        settings = _settings(website_app)
        manager_id = _resolve_manager(
            website_app,
            settings=settings,
            lead_id=int(lead_id),
            checkpoint=row[5],
        )
        tourist_id = _ensure_tourist(
            website_app,
            lead_id=int(lead_id),
            name=str(payload["name"]),
            phone=str(payload["phone"]),
            manager_id=manager_id,
            checkpoint_id=row[3],
        )
        if tourist_id is None:
            raise RuntimeError("mdt_tourist_id_unavailable")

        delivery_key = f"web-lead-{int(lead_id)}"
        preorder_id = int(row[4]) if row[4] else None
        if preorder_id is None:
            preorder_id = _recover_preorder(
                website_app,
                tourist_id=int(tourist_id),
                delivery_key=delivery_key,
            )
            if preorder_id:
                _checkpoint(website_app, lead_id, mdt_preorder_id=int(preorder_id))
                logger.info("Recovered MDT preorder checkpoint for Website lead %s", lead_id)

        if preorder_id is None:
            result = website_app._bot._mdt_request(
                "create-preorder",
                _preorder_params(
                    website_app,
                    lead_id=int(lead_id),
                    payload=payload,
                    tourist_id=int(tourist_id),
                    manager_id=manager_id,
                ),
            )
            preorder_id = _extract_preorder_id(result)
            if preorder_id is not None:
                _checkpoint(website_app, lead_id, mdt_preorder_id=int(preorder_id))
            elif _live_result_is_success(result):
                # MDT sometimes acknowledges a successful write without an ID.
                # Retrying such a write is more dangerous than accepting the
                # acknowledgement because it can duplicate the preorder.
                logger.info(
                    "MDT create-preorder accepted Website lead %s via result-only response",
                    lead_id,
                )
            else:
                raise RuntimeError("mdt_preorder_not_confirmed")

        _mark_synced(website_app, lead_id, attempts)
        return True
    except Exception as exc:
        logger.warning(
            "Website structured MDT delivery failed for lead %s: %s",
            lead_id,
            type(exc).__name__,
        )
        _mark_pending(website_app, lead_id, attempts)
        return False


def install(website_app: Any) -> None:
    """Install the structured, checkpointed Website MDT delivery once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _migrate_schema(website_app)
    website_app._deliver_lead = lambda lead_id: _deliver_structured_preorder(
        website_app, int(lead_id)
    )
    _INSTALLED = True
    logger.info("Website structured MDT preorder delivery installed")
