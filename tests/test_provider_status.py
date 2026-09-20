import base64
import json

from shared import provider_status


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_report_shows_all_providers_unavailable_without_credentials():
    report = provider_status.format_report({}, now=1000)

    assert "Автопоиск: 🔴 недоступен" in report
    assert "Sletat: 🟡 доступ не настроен" in report
    assert "Travelata: 🟡 доступ не настроен" in report
    assert "Tourvisor: 🟡 JWT не настроен" in report
    assert "Секреты и логины" in report


def test_report_rejects_partial_credential_pairs():
    report = provider_status.format_report(
        {
            "SLETAT_LOGIN": "agency",
            "TRAVELATA_PASSWORD": "secret",
        },
        now=1000,
    )

    assert "Sletat: 🔴 неполная пара credentials" in report
    assert "Travelata: 🔴 неполная пара credentials" in report
    assert "Автопоиск: 🔴 недоступен" in report


def test_report_marks_expired_tourvisor_jwt_unavailable():
    report = provider_status.format_report(
        {
            "TOURVISOR_TOKEN": _jwt(900),
            "VK_TOURVISOR_ENABLED": "true",
        },
        now=1000,
    )

    assert "Tourvisor: 🔴 JWT истёк" in report
    assert "Автопоиск: 🔴 недоступен" in report


def test_report_marks_ready_providers_and_never_exposes_credentials():
    env = {
        "SLETAT_LOGIN": "agency-secret-login",
        "SLETAT_PASSWORD": "agency-secret-password",
        "VK_SLETAT_ENABLED": "true",
        "TOURVISOR_TOKEN": _jwt(1000 + 5 * 86400),
        "VK_TOURVISOR_ENABLED": "true",
    }

    report = provider_status.format_report(env, now=1000)

    assert "Автопоиск: 🟢 доступен" in report
    assert "Активные: Sletat, Tourvisor" in report
    assert "Sletat: 🟢 готов" in report
    assert "Tourvisor: 🟢 готов · ещё ~5 дн." in report
    assert "agency-secret-login" not in report
    assert "agency-secret-password" not in report
    assert env["TOURVISOR_TOKEN"] not in report
