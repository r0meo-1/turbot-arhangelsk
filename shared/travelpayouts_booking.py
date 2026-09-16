"""Booking.com search links monetized through Travelpayouts.

The VK Mini App already knows destination, dates and party composition.  This
module converts those values into a normal Booking.com search URL and then
asks Travelpayouts to wrap it in the project's affiliate link.

Secrets stay server-side.  Only ``TRAVELPAYOUTS_API_TOKEN`` is sensitive;
marker/project ids are configurable because staging and production may use
separate Travelpayouts projects.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Dict
from urllib.parse import urlencode

import requests


PARTNER_LINKS_URL = "https://api.travelpayouts.com/links/v1/create"
DEFAULT_MARKER = 778488
DEFAULT_TRS = 574782


class BookingLinkError(RuntimeError):
    """Raised when a monetized Booking.com link cannot be created."""


class BookingLinkNotConfigured(BookingLinkError):
    """Raised when the server does not have Travelpayouts credentials."""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise BookingLinkNotConfigured(f"{name} is invalid") from exc


def build_booking_search_url(info: Dict[str, Any]) -> str:
    """Build a Booking.com search URL from a validated trip-request dict."""
    destination = str(info.get("destination", "")).strip()
    if not destination:
        raise BookingLinkError("destination is missing")

    try:
        checkin = date.fromisoformat(str(info.get("dates", "")))
        nights = int(info.get("nights", 0))
        adults = int(info.get("people", 0))
    except (TypeError, ValueError) as exc:
        raise BookingLinkError("trip dates or party are invalid") from exc

    if nights < 1 or adults < 1:
        raise BookingLinkError("trip dates or party are invalid")
    checkout = checkin + timedelta(days=nights)

    children = info.get("kids_ages") or []
    if not isinstance(children, list):
        children = []

    params = [
        ("ss", destination),
        ("checkin", checkin.isoformat()),
        ("checkout", checkout.isoformat()),
        ("group_adults", str(adults)),
        ("group_children", str(len(children))),
        ("no_rooms", "1"),
        ("selected_currency", "RUB"),
        ("lang", "ru"),
    ]
    for age in children:
        try:
            parsed_age = max(0, min(17, int(age)))
        except (TypeError, ValueError):
            continue
        params.append(("age", str(parsed_age)))

    return "https://www.booking.com/searchresults.html?" + urlencode(params)


def create_booking_partner_link(info: Dict[str, Any], *, timeout: int = 12) -> str:
    """Return a Travelpayouts affiliate link for the Booking.com search."""
    token = os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip()
    if not token:
        raise BookingLinkNotConfigured("TRAVELPAYOUTS_API_TOKEN is missing")

    marker = _env_int("TRAVELPAYOUTS_MARKER", DEFAULT_MARKER)
    trs = _env_int("TRAVELPAYOUTS_TRS", DEFAULT_TRS)
    if marker <= 0 or trs <= 0:
        raise BookingLinkNotConfigured("Travelpayouts project ids are invalid")

    destination_url = build_booking_search_url(info)
    body = {
        "trs": trs,
        "marker": marker,
        "shorten": True,
        "links": [
            {
                "url": destination_url,
                "sub_id": "vk_booking_search",
            }
        ],
    }
    headers = {
        "X-Access-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(PARTNER_LINKS_URL, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise BookingLinkError("Travelpayouts is unavailable") from exc

    if response.status_code == 401:
        raise BookingLinkNotConfigured("Travelpayouts API token is invalid")
    if response.status_code >= 400:
        raise BookingLinkError(f"Travelpayouts returned HTTP {response.status_code}")

    try:
        payload = response.json()
        item = payload["result"]["links"][0]
        partner_url = str(item.get("partner_url", "")).strip()
        code = str(item.get("code", "")).strip().lower()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BookingLinkError("Travelpayouts returned an invalid response") from exc

    if code != "success" or not partner_url.startswith("https://"):
        message = str(item.get("message", "")).strip()
        if "not subscribed" in message.lower():
            raise BookingLinkNotConfigured("Booking.com program is not enabled for this project")
        raise BookingLinkError(message or "Travelpayouts could not create a Booking.com link")

    return partner_url
