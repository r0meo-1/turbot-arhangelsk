"""VK Mini App authentication, bot entry button, and same-origin draft API."""
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from shared.telegram_webapp import MiniAppValidationError, validate_trip_request
from shared.legal_identity import (
    LEGAL_OPERATOR_DISPLAY,
    LEGAL_PRIVACY_CONTACT,
    LEGAL_PROJECT_URL,
)
from shared import travelpayouts_booking as _travelpayouts_booking


MINIAPP_BUTTON_TEXT = "🧳 Подобрать тур в приложении"


def build_open_app_button(app_id, group_id, *, enabled=True, hash_value="bot"):
    """Build a native VK ``open_app`` keyboard button when Mini App is ready.

    ``owner_id`` is the negative community id because the application is
    installed and opened in the community context. Returning ``None`` keeps
    the ordinary chat flow intact on hosts where the app ID/secret are not
    configured yet.
    """
    if not enabled:
        return None
    try:
        app_id = int(app_id)
        group_id = int(group_id)
    except (TypeError, ValueError):
        return None
    if app_id <= 0 or group_id <= 0:
        return None
    return {
        "action": {
            "type": "open_app",
            "app_id": app_id,
            "owner_id": -abs(group_id),
            "label": MINIAPP_BUTTON_TEXT,
            "hash": str(hash_value or "bot")[:128],
        }
    }


def validate_launch_params(raw, secret, app_id, group_id, *, now=None):
    if not secret or not app_id:
        raise MiniAppValidationError("App is not configured")
    if not isinstance(raw, str) or not raw or len(raw) > 8192:
        raise MiniAppValidationError("Invalid launch params")
    try:
        pairs = parse_qsl(raw.lstrip("?"), keep_blank_values=True, max_num_fields=100)
    except ValueError as exc:
        raise MiniAppValidationError("Invalid launch params") from exc
    params = dict(pairs)
    if len(pairs) != len(params):
        raise MiniAppValidationError("Duplicate launch params")
    signed = urlencode(sorted((k, v) for k, v in pairs if k.startswith("vk_")))
    expected = base64.urlsafe_b64encode(hmac.new(
        secret.encode(), signed.encode(), hashlib.sha256
    ).digest()).decode().rstrip("=")
    signature = params.get("sign", "")
    if not signature.isascii() or not hmac.compare_digest(expected, signature):
        raise MiniAppValidationError("Invalid signature")
    try:
        uid, ts = int(params.get("vk_user_id", "0")), int(params.get("vk_ts", "0"))
    except ValueError as exc:
        raise MiniAppValidationError("Invalid identity") from exc
    now = time.time() if now is None else now
    if uid <= 0 or ts <= 0 or ts > now + 60 or now - ts > 3600:
        raise MiniAppValidationError("Expired identity")
    if params.get("vk_app_id") != str(app_id):
        raise MiniAppValidationError("Wrong app")
    if params.get("vk_group_id", "0") not in ("0", str(group_id)):
        raise MiniAppValidationError("Wrong community")
    return uid


def _signed_launch_metadata(raw):
    """Return non-sensitive attribution fields after ``raw`` was authenticated."""
    params = dict(parse_qsl(raw.lstrip("?"), keep_blank_values=True, max_num_fields=100))
    metadata = {}
    for key in ("vk_ref", "vk_platform"):
        value = str(params.get(key, "")).strip()
        if value:
            metadata[key] = value[:128]
    return metadata


def _booking_configuration_code(exc):
    """Map internal Travelpayouts configuration failures to safe public codes."""
    reason = str(exc).lower()
    if "token is invalid" in reason:
        return "travelpayouts_token_invalid"
    if "token is missing" in reason:
        return "travelpayouts_token_missing"
    if "program is not enabled" in reason:
        return "booking_program_not_enabled"
    return "booking_not_configured"


def validate_vk_trip(payload):
    # Privacy consent and service-terms acceptance are separate user actions.
    if not isinstance(payload, dict) or payload.get("termsAccepted") is not True:
        raise MiniAppValidationError("terms acceptance is required")
    # JSON numbers must really be integers; do not silently truncate fractions.
    if isinstance(payload, dict):
        for key in ("version", "nights", "adults", "children", "budgetMaxRub"):
            if type(payload.get(key)) is not int:
                raise MiniAppValidationError("Integer required")
        ages = payload.get("childrenAges")
        if not isinstance(ages, list) or any(type(age) is not int for age in ages):
            raise MiniAppValidationError("Invalid child ages")
        # VK v2 historically meant total-trip budget. New clients send the
        # scope explicitly, while old installed WebViews keep their old meaning.
        normalized_payload = dict(payload)
        normalized_payload.setdefault("budgetScope", "total")
    else:
        normalized_payload = payload
    info = validate_trip_request(normalized_payload)
    info.update(source="vk_mini_app", terms_accepted=True)
    return info


def create_blueprint(save_draft, settings):
    bp = Blueprint("vk_miniapp", __name__)
    static = Path(__file__).resolve().parents[1] / "vk-miniapp"

    @bp.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.get("/vk/miniapp/")
    def index():
        return send_from_directory(static, "index.html")

    @bp.get("/vk/miniapp/legal.json")
    def legal_config():
        return jsonify(
            operatorName=os.getenv("DATA_OPERATOR_NAME", LEGAL_OPERATOR_DISPLAY).strip() or LEGAL_OPERATOR_DISPLAY,
            privacyContact=os.getenv(
                "DATA_OPERATOR_CONTACT",
                LEGAL_PRIVACY_CONTACT,
            ).strip() or LEGAL_PRIVACY_CONTACT,
            projectUrl=os.getenv(
                "PUBLIC_PROJECT_URL",
                LEGAL_PROJECT_URL,
            ).strip() or LEGAL_PROJECT_URL,
        )

    @bp.get("/vk/miniapp/<name>")
    def asset(name):
        if name not in (
            "app.js", "styles.css", "vk-bridge.js", "legal.js",
            "privacy.html", "consent.html", "terms.html", "moderation.html",
        ):
            return jsonify(ok=False), 404
        return send_from_directory(static, name)

    @bp.post("/vk/miniapp/booking-link")
    def booking_link():
        secret, app_id, group_id = settings()
        if not secret or not app_id:
            current_app.logger.error("vk_booking_link status=unconfigured_app")
            return jsonify(ok=False, error="Приложение ещё не подключено. Попробуйте позже."), 503
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="Некорректные данные поиска."), 400
        raw_launch = body.get("launchParams")
        try:
            validate_launch_params(raw_launch, secret, app_id, group_id)
            info = validate_vk_trip(body.get("payload"))
        except MiniAppValidationError as exc:
            current_app.logger.warning("vk_booking_link status=rejected reason=%s", str(exc))
            return jsonify(ok=False, error="Откройте приложение заново из VK и проверьте параметры."), 401
        try:
            url = _travelpayouts_booking.create_booking_partner_link(info)
        except _travelpayouts_booking.BookingLinkNotConfigured as exc:
            error_code = _booking_configuration_code(exc)
            current_app.logger.warning(
                "vk_booking_link status=not_configured code=%s reason=%s",
                error_code,
                str(exc),
            )
            return jsonify(
                ok=False,
                error="Поиск Booking.com ещё не подключён к проекту.",
                errorCode=error_code,
            ), 503
        except _travelpayouts_booking.BookingLinkError as exc:
            current_app.logger.warning("vk_booking_link status=provider_error reason=%s", str(exc))
            return jsonify(ok=False, error="Booking.com временно недоступен. Попробуйте позже."), 502
        current_app.logger.info("vk_booking_link status=created")
        return jsonify(ok=True, url=url)

    @bp.post("/vk/miniapp/draft")
    def draft():
        secret, app_id, group_id = settings()
        if not secret or not app_id:
            current_app.logger.error("vk_miniapp_draft status=unconfigured")
            return jsonify(ok=False, error="Приложение ещё не подключено. Попробуйте позже."), 503
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            current_app.logger.warning("vk_miniapp_draft status=invalid_body")
            return jsonify(ok=False, error="Некорректные данные формы."), 400
        raw_launch = body.get("launchParams")
        try:
            uid = validate_launch_params(raw_launch, secret, app_id, group_id)
        except MiniAppValidationError as exc:
            # Never log the raw launch query, signature, secret, or VK user id.
            current_app.logger.warning("vk_miniapp_draft status=auth_rejected reason=%s", str(exc))
            return jsonify(
                ok=False,
                error="Откройте приложение заново из VK.",
                authReason=str(exc),
            ), 401
        try:
            info = validate_vk_trip(body.get("payload"))
        except MiniAppValidationError:
            current_app.logger.warning("vk_miniapp_draft status=payload_rejected")
            return jsonify(ok=False, error="Проверьте поля, дату и согласие на обработку данных."), 400
        metadata = _signed_launch_metadata(raw_launch)
        info.update(metadata)
        try:
            save_draft(uid, info)
        except MiniAppValidationError:
            current_app.logger.warning("vk_miniapp_draft status=save_conflict")
            return jsonify(ok=False, error="Заявка уже отправляется. Подождите несколько секунд и повторите."), 409
        except Exception:
            current_app.logger.exception("vk_miniapp_draft status=save_failed")
            return jsonify(ok=False, error="Не удалось сохранить параметры. Повторите попытку."), 500
        current_app.logger.info(
            "vk_miniapp_draft status=saved ref=%s platform=%s",
            metadata.get("vk_ref", "-"),
            metadata.get("vk_platform", "-"),
        )
        return jsonify(ok=True, state="review", groupId=group_id)

    return bp
