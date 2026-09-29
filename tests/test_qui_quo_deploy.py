from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _location_block(source: str, marker: str) -> str:
    start = source.index(marker)
    tail = source[start:]
    end = tail.find("\n    }\n")
    assert end >= 0
    return tail[: end + len("\n    }\n")]


def test_qui_quo_secret_path_is_not_persisted_in_production_access_logs():
    nginx = (ROOT / "deploy" / "nginx-turbot.conf").read_text(encoding="utf-8")
    https_block = _location_block(nginx, "location ^~ /qq-webhook/")
    assert "access_log off;" in https_block

    # The config contains a second no-log block in the HTTP redirect server too.
    assert nginx.count("location ^~ /qq-webhook/") == 2
    assert nginx.count("access_log off;") >= 2

    service = (ROOT / "deploy" / "turbot.service").read_text(encoding="utf-8")
    assert "--access-logfile" not in service


def test_compose_proxy_does_not_log_qui_quo_secret_path():
    nginx = (ROOT / "deploy" / "nginx-compose.conf").read_text(encoding="utf-8")
    block = _location_block(nginx, "location ^~ /qq-webhook/")
    assert "access_log off;" in block
    assert "proxy_pass http://telegram-bot:5000;" in block


def test_container_entrypoints_load_website_and_qui_quo_routes():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "website_app.py" in dockerfile
    assert "gunicorn.conf.py" in dockerfile
    assert '"website_app:app"' in dockerfile
    assert "gunicorn website_app:app" in compose


def test_website_app_registers_qui_quo_after_crm_schema_and_with_projector():
    source = (ROOT / "website_app.py").read_text(encoding="utf-8")

    schema_call = source.rfind("\n_init_schema()\n")
    registration = source.rfind('if "qui_quo_webhook" not in app.blueprints:')
    assert schema_call >= 0
    assert registration > schema_call
    assert "projector=_project_qui_quo_activity" in source
    assert '/agent-extension/crm/qui-quo-link' in source
