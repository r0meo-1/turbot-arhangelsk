"""VK Mini App authentication, bot entry button, and same-origin draft API."""
import base64
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from flask import Blueprint, jsonify, request, send_from_directory
from shared.telegram_webapp import MiniAppValidationError, validate_trip_request


MINIAPP_BUTTON_TEXT = "🧳 Подобрать тур в приложении"


def build_open_app_button(app_id, group_id, *, enabled=True, hash_value="bot"):
    """Build a native VK ``open_app`` keyboard button when Mini App is ready.

    ``owner_id`` is the negative community id because the application is
    installed and opened in the community context.  Returning ``None`` keeps
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


def validate_vk_trip(payload):
    # JSON numbers must really be integers; do not silently truncate fractions.
    if isinstance(payload, dict):
        for key in ("version", "nights", "adults", "children", "budgetMaxRub"):
            if type(payload.get(key)) is not int:
                raise MiniAppValidationError("Integer required")
        ages = payload.get("childrenAges")
        if not isinstance(ages, list) or any(type(age) is not int for age in ages):
            raise MiniAppValidationError("Invalid child ages")
    info = validate_trip_request(payload)
    info.update(source="vk_mini_app", budget_scope="total")
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

    @bp.get("/vk/miniapp/<name>")
    def asset(name):
        if name not in ("app.js", "styles.css", "vk-bridge.js"):
            return jsonify(ok=False), 404
        return send_from_directory(static, name)

    @bp.post("/vk/miniapp/draft")
    def draft():
        secret, app_id, group_id = settings()
        if not secret or not app_id:
            return jsonify(ok=False, error="Приложение ещё не подключено. Попробуйте позже."), 503
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="Некорректные данные формы."), 400
        try:
            uid = validate_launch_params(body.get("launchParams"), secret, app_id, group_id)
        except MiniAppValidationError as exc:
            return jsonify(
                ok=False,
                error="Откройте приложение заново из VK.",
                authReason=str(exc),
            ), 401
        try:
            info = validate_vk_trip(body.get("payload"))
        except MiniAppValidationError:
            return jsonify(ok=False, error="Проверьте поля, дату и согласие на обработку данных."), 400
        try:
            save_draft(uid, info)
        except MiniAppValidationError:
            return jsonify(ok=False, error="У вас уже есть подбор в чате. Завершите его или нажмите «Отмена», затем повторите."), 409
        except Exception:
            return jsonify(ok=False, error="Не удалось сохранить параметры. Повторите попытку."), 500
        return jsonify(ok=True, state="review", groupId=group_id)

    return bp
