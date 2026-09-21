"""Static mobile manager shell; CRM data retains existing bearer authorization."""

from pathlib import Path

from flask import Blueprint, abort, send_from_directory

manager_web = Blueprint("manager_web", __name__)
_ROOT = Path(__file__).resolve().parents[1] / "manager-web"


@manager_web.get("/manager/")
@manager_web.get("/manager/<name>")
def manager_asset(name="index.html"):
    if name not in {"index.html", "app.js", "styles.css"}:
        abort(404)
    response = send_from_directory(_ROOT, name)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
