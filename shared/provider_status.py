from __future__ import annotations

import os
from typing import Mapping, Optional

from shared import tourvisor


_TRUE = {"1", "true", "yes", "on"}


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(env.get(name, "") or "").strip()
    if not raw:
        return default
    return raw.lower() in _TRUE


def _pair_state(
    env: Mapping[str, str],
    user_key: str,
    password_key: str,
    enabled_key: str,
) -> tuple[str, bool]:
    user = str(env.get(user_key, "") or "").strip()
    password = str(env.get(password_key, "") or "").strip()
    if bool(user) != bool(password):
        return "🔴 неполная пара credentials", False
    configured = bool(user and password)
    enabled = _flag(env, enabled_key, configured)
    if not configured:
        return "🟡 доступ не настроен", False
    if not enabled:
        return "⚪ настроен, но выключен", False
    return "🟢 готов", True


def format_report(
    env: Optional[Mapping[str, str]] = None,
    *,
    now: Optional[float] = None,
) -> str:
    values = os.environ if env is None else env

    sletat_text, sletat_ready = _pair_state(
        values, "SLETAT_LOGIN", "SLETAT_PASSWORD", "VK_SLETAT_ENABLED"
    )
    travelata_text, travelata_ready = _pair_state(
        values, "TRAVELATA_USERNAME", "TRAVELATA_PASSWORD", "VK_TRAVELATA_ENABLED"
    )

    token = str(values.get("TOURVISOR_TOKEN", "") or "").strip()
    token_state = tourvisor.jwt_status(token, now=now)
    tourvisor_enabled = _flag(values, "VK_TOURVISOR_ENABLED", bool(token))
    if not token:
        tourvisor_text = "🟡 JWT не настроен"
        tourvisor_ready = False
    elif token_state["status"] == "expired":
        tourvisor_text = "🔴 JWT истёк"
        tourvisor_ready = False
    elif token_state["status"] == "valid" and not tourvisor_enabled:
        tourvisor_text = "⚪ JWT есть, но провайдер выключен"
        tourvisor_ready = False
    elif token_state["status"] == "valid":
        remaining = token_state.get("expires_in_seconds")
        if isinstance(remaining, int):
            days = remaining // 86400
            suffix = f" · ещё ~{days} дн." if days > 0 else " · истекает <24 ч"
        else:
            suffix = ""
        tourvisor_text = "🟢 готов" + suffix
        tourvisor_ready = True
    else:
        tourvisor_text = f"🔴 JWT: {token_state['status']}"
        tourvisor_ready = False

    ready = [
        name
        for name, state in (
            ("Sletat", sletat_ready),
            ("Travelata", travelata_ready),
            ("Tourvisor", tourvisor_ready),
        )
        if state
    ]
    overall = "🟢 доступен" if ready else "🔴 недоступен"
    active = ", ".join(ready) if ready else "нет"

    return "\n".join(
        [
            "🧭 Провайдеры туров",
            f"Автопоиск: {overall}",
            f"Активные: {active}",
            "",
            f"Sletat: {sletat_text}",
            f"Travelata: {travelata_text}",
            f"Tourvisor: {tourvisor_text}",
            "",
            "Секреты и логины в отчёт не выводятся.",
        ]
    )
