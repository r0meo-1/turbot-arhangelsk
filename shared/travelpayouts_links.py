"""Server-side affiliate links for Telegram Mini App partner services.

The browser sends only trip context. Travelpayouts partner/project identifiers
stay here so they can be changed without publishing a new Mini App bundle.

For hotel/eSIM links we prefer Travelpayouts Partner Links API because it gives
us a first-class SubID. If a brand cannot be wrapped by the API, the historical
Travelpayouts redirect remains as an affiliate fallback.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests


PARTNER_LINKS_URL = "https://api.travelpayouts.com/links/v1/create"
DEFAULT_MARKER = 778488
DEFAULT_TRS = 574782

YANDEX_TRAVEL_PROGRAM = 5916
AIRALO_PROGRAM = 8310
AIRALO_CAMPAIGN_ID = 541

HOTELS_SUB_ID = "tg_hotels"
ESIM_SUB_ID = "tg_esim"


class PartnerLinkError(RuntimeError):
    """Raised when Travelpayouts cannot create a partner link."""


class PartnerLinkNotConfigured(PartnerLinkError):
    """Raised when the Travelpayouts token/project settings are unusable."""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise PartnerLinkNotConfigured(f"{name} is invalid") from exc


def _normalize(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").strip().split())


_HOTEL_COUNTRY_SLUGS = {
    "таиланд": "thailand",
    "thailand": "thailand",
    "пхукет": "thailand",
    "паттайя": "thailand",
    "бангкок": "thailand",
    "самуи": "thailand",
    "вьетнам": "vietnam",
    "vietnam": "vietnam",
    "нячанг": "vietnam",
    "ня чанг": "vietnam",
    "фукуок": "vietnam",
    "фу куок": "vietnam",
    "дананг": "vietnam",
    "шри-ланка": "sri-lanka",
    "шри ланка": "sri-lanka",
    "sri-lanka": "sri-lanka",
    "sri lanka": "sri-lanka",
    "коломбо": "sri-lanka",
}

_AIRALO_TARGETS = {
    "таиланд": "https://www.airalo.com/thailand-esim",
    "thailand": "https://www.airalo.com/thailand-esim",
    "пхукет": "https://www.airalo.com/thailand-esim",
    "паттайя": "https://www.airalo.com/thailand-esim",
    "бангкок": "https://www.airalo.com/thailand-esim",
    "самуи": "https://www.airalo.com/thailand-esim",
    "вьетнам": "https://www.airalo.com/vietnam-esim",
    "vietnam": "https://www.airalo.com/vietnam-esim",
    "нячанг": "https://www.airalo.com/vietnam-esim",
    "ня чанг": "https://www.airalo.com/vietnam-esim",
    "фукуок": "https://www.airalo.com/vietnam-esim",
    "фу куок": "https://www.airalo.com/vietnam-esim",
    "дананг": "https://www.airalo.com/vietnam-esim",
    "шри-ланка": "https://www.airalo.com/sri-lanka-esim",
    "шри ланка": "https://www.airalo.com/sri-lanka-esim",
    "sri-lanka": "https://www.airalo.com/sri-lanka-esim",
    "sri lanka": "https://www.airalo.com/sri-lanka-esim",
    "коломбо": "https://www.airalo.com/sri-lanka-esim",
}


def build_yandex_travel_url(
    destination: str,
    *,
    checkin: str = "",
    nights: int = 0,
    adults: int = 2,
) -> str:
    """Build the same Yandex Travel hotel target previously assembled in JS."""
    slug = _HOTEL_COUNTRY_SLUGS.get(_normalize(destination))
    base = (
        f"https://travel.yandex.ru/hotels/{slug}/"
        if slug
        else "https://travel.yandex.ru/hotels/"
    )

    params = []
    try:
        parsed_checkin = date.fromisoformat(str(checkin))
        parsed_nights = int(nights)
    except (TypeError, ValueError):
        parsed_checkin = None
        parsed_nights = 0

    if parsed_checkin is not None and parsed_nights > 0:
        checkout = parsed_checkin + timedelta(days=min(parsed_nights, 60))
        params.extend([
            ("checkinDate", parsed_checkin.isoformat()),
            ("checkoutDate", checkout.isoformat()),
        ])

    try:
        parsed_adults = int(adults)
    except (TypeError, ValueError):
        parsed_adults = 2
    params.extend([
        ("adults", str(max(1, min(8, parsed_adults or 2)))),
        ("utm_source", "turbot"),
        ("utm_medium", "miniapp"),
        ("utm_campaign", "yandex_travel"),
        ("utm_content", "telegram"),
    ])
    return base + "?" + urlencode(params)


def build_airalo_url(destination: str) -> str:
    """Build the Airalo target used by Telegram Mini App."""
    target = _AIRALO_TARGETS.get(_normalize(destination), "https://www.airalo.com/")
    params = urlencode({
        "utm_source": "turbot",
        "utm_medium": "miniapp",
        "utm_campaign": "esim",
        "utm_content": "telegram",
    })
    return target + ("&" if "?" in target else "?") + params


def build_legacy_redirect(
    target: str,
    *,
    program: int,
    sub_id: str,
    campaign_id: Optional[int] = None,
) -> str:
    """Build the previous tp.media redirect server-side as a safe fallback."""
    marker = _env_int("TRAVELPAYOUTS_MARKER", DEFAULT_MARKER)
    trs = _env_int("TRAVELPAYOUTS_TRS", DEFAULT_TRS)
    if marker <= 0 or trs <= 0 or program <= 0:
        raise PartnerLinkNotConfigured("Travelpayouts project ids are invalid")

    params = {
        "marker": f"{marker}.{sub_id}",
        "trs": str(trs),
        "p": str(program),
        "u": target,
    }
    if campaign_id:
        params["campaign_id"] = str(campaign_id)
    return "https://tp.media/r?" + urlencode(params)


def create_partner_link(
    target: str,
    *,
    sub_id: str,
    timeout: int = 12,
) -> str:
    """Wrap a brand URL through Travelpayouts Partner Links API."""
    token = os.getenv("TRAVELPAYOUTS_API_TOKEN", "").strip()
    if not token:
        raise PartnerLinkNotConfigured("TRAVELPAYOUTS_API_TOKEN is missing")

    marker = _env_int("TRAVELPAYOUTS_MARKER", DEFAULT_MARKER)
    trs = _env_int("TRAVELPAYOUTS_TRS", DEFAULT_TRS)
    if marker <= 0 or trs <= 0:
        raise PartnerLinkNotConfigured("Travelpayouts project ids are invalid")

    body = {
        "trs": trs,
        "marker": marker,
        "shorten": True,
        "links": [{
            "url": target,
            "sub_id": sub_id,
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
        raise PartnerLinkError("Travelpayouts is unavailable") from exc

    if response.status_code == 401:
        raise PartnerLinkNotConfigured("Travelpayouts API token is invalid")
    if response.status_code >= 400:
        raise PartnerLinkError(f"Travelpayouts returned HTTP {response.status_code}")

    try:
        payload = response.json()
        item = payload["result"]["links"][0]
        partner_url = str(item.get("partner_url", "")).strip()
        code = str(item.get("code", "")).strip().lower()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise PartnerLinkError("Travelpayouts returned an invalid response") from exc

    if code != "success" or not partner_url.startswith("https://"):
        message = str(item.get("message", "")).strip()
        if "not subscribed" in message.lower():
            raise PartnerLinkNotConfigured("Travelpayouts program is not enabled")
        raise PartnerLinkError(message or "Travelpayouts could not create a partner link")

    return partner_url


def resolve_hotel_link(
    destination: str,
    *,
    checkin: str = "",
    nights: int = 0,
    adults: int = 2,
) -> tuple[str, str]:
    """Return hotel affiliate URL and resolution mode."""
    target = build_yandex_travel_url(
        destination,
        checkin=checkin,
        nights=nights,
        adults=adults,
    )
    try:
        return create_partner_link(target, sub_id=HOTELS_SUB_ID), "api"
    except PartnerLinkError:
        try:
            return build_legacy_redirect(
                target,
                program=YANDEX_TRAVEL_PROGRAM,
                sub_id=HOTELS_SUB_ID,
            ), "redirect"
        except PartnerLinkError:
            return target, "direct"


def resolve_esim_link(destination: str) -> tuple[str, str]:
    """Return Airalo affiliate URL and resolution mode."""
    target = build_airalo_url(destination)
    try:
        return create_partner_link(target, sub_id=ESIM_SUB_ID), "api"
    except PartnerLinkError:
        try:
            return build_legacy_redirect(
                target,
                program=AIRALO_PROGRAM,
                campaign_id=AIRALO_CAMPAIGN_ID,
                sub_id=ESIM_SUB_ID,
            ), "redirect"
        except PartnerLinkError:
            return target, "direct"
