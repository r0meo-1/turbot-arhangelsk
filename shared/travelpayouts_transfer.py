"""Kiwitaxi transfer links monetized through Travelpayouts.

Telegram Mini App sends only a destination name. This module maps common
country/resort names to Kiwitaxi country pages and asks Travelpayouts to wrap
that destination in the project's affiliate link.

The API token never leaves the server. If Kiwitaxi is not enabled for the
Travelpayouts project, callers can fall back to the direct destination URL.
"""
from __future__ import annotations

import os
from typing import Dict

import requests


PARTNER_LINKS_URL = "https://api.travelpayouts.com/links/v1/create"
DEFAULT_MARKER = 778488
DEFAULT_TRS = 574782
TRANSFER_SUB_ID = "tg_transfer"


class TransferLinkError(RuntimeError):
    """Raised when a monetized Kiwitaxi link cannot be created."""


class TransferLinkNotConfigured(TransferLinkError):
    """Raised when Travelpayouts credentials/program configuration is missing."""


def _normalize(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").strip().split())


_DESTINATION_TO_COUNTRY: Dict[str, str] = {
    "таиланд": "thailand",
    "thailand": "thailand",
    "вьетнам": "vietnam",
    "vietnam": "vietnam",
    "шри-ланка": "sri-lanka",
    "шри ланка": "sri-lanka",
    "sri lanka": "sri-lanka",
    "sri-lanka": "sri-lanka",
    "египет": "egypt",
    "egypt": "egypt",
    "оаэ": "uae",
    "uae": "uae",
    "турция": "turkey",
    "turkey": "turkey",
    "индонезия": "indonesia",
    "indonesia": "indonesia",
    "китай": "china",
    "china": "china",
    "куба": "cuba",
    "cuba": "cuba",
    "танзания": "tanzania",
    "tanzania": "tanzania",
    "индия": "india",
    "india": "india",
    "греция": "greece",
    "greece": "greece",
    "кипр": "cyprus",
    "cyprus": "cyprus",
    "тунис": "tunisia",
    "tunisia": "tunisia",
    "доминикана": "dominican-republic",
    "доминиканская республика": "dominican-republic",
    "dominican republic": "dominican-republic",
    "пхукет": "thailand",
    "паттайя": "thailand",
    "бангкок": "thailand",
    "самуи": "thailand",
    "нячанг": "vietnam",
    "ня чанг": "vietnam",
    "фукуок": "vietnam",
    "фу куок": "vietnam",
    "дананг": "vietnam",
    "хургада": "egypt",
    "шарм-эль-шейх": "egypt",
    "шарм эль шейх": "egypt",
    "дубай": "uae",
    "абу-даби": "uae",
    "абу даби": "uae",
    "анталья": "turkey",
    "аланья": "turkey",
    "кемер": "turkey",
    "сиде": "turkey",
    "стамбул": "turkey",
    "бали": "indonesia",
    "хайнань": "china",
    "санья": "china",
    "гоа": "india",
    "занзибар": "tanzania",
    "пунта-кана": "dominican-republic",
    "пунта кана": "dominican-republic",
    "коломбо": "sri-lanka",
}


def build_kiwitaxi_url(destination: str) -> str:
    """Build a direct Kiwitaxi country URL, or the safe generic landing page."""
    key = _normalize(destination)
    slug = _DESTINATION_TO_COUNTRY.get(key)
    if slug:
        return f"https://kiwitaxi.com/en/{slug}"
    return "https://kiwitaxi.com/en/"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise TransferLinkNotConfigured(f"{name} is invalid") from exc


def create_transfer_partner_link(destination: str, *, timeout: int = 12) -> str:
    """Return a Travelpayouts affiliate Kiwitaxi link for a destination."""
    token = os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip()
    if not token:
        raise TransferLinkNotConfigured("TRAVELPAYOUTS_API_TOKEN is missing")

    marker = _env_int("TRAVELPAYOUTS_MARKER", DEFAULT_MARKER)
    trs = _env_int("TRAVELPAYOUTS_TRS", DEFAULT_TRS)
    if marker <= 0 or trs <= 0:
        raise TransferLinkNotConfigured("Travelpayouts project ids are invalid")

    destination_url = build_kiwitaxi_url(destination)
    body = {
        "trs": trs,
        "marker": marker,
        "shorten": True,
        "links": [{
            "url": destination_url,
            "sub_id": TRANSFER_SUB_ID,
        }],
    }
    headers = {
        "X-Access-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            PARTNER_LINKS_URL,
            json=body,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TransferLinkError("Travelpayouts is unavailable") from exc

    if response.status_code == 401:
        raise TransferLinkNotConfigured("Travelpayouts API token is invalid")
    if response.status_code >= 400:
        raise TransferLinkError(f"Travelpayouts returned HTTP {response.status_code}")

    try:
        payload = response.json()
        item = payload["result"]["links"][0]
        partner_url = str(item.get("partner_url", "")).strip()
        code = str(item.get("code", "")).strip().lower()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise TransferLinkError("Travelpayouts returned an invalid response") from exc

    if code != "success" or not partner_url.startswith("https://"):
        message = str(item.get("message", "")).strip()
        if "not subscribed" in message.lower():
            raise TransferLinkNotConfigured(
                "Kiwitaxi program is not enabled for this project"
            )
        raise TransferLinkError(message or "Travelpayouts could not create a Kiwitaxi link")

    return partner_url
