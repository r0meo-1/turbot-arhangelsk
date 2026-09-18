import sqlite3

import bot


def _use_temp_db(monkeypatch, tmp_path):
    path = tmp_path / "partner-analytics.sqlite"
    monkeypatch.setattr(bot, "DATABASE_PATH", str(path))
    bot.init_db()
    return path


def test_partner_click_schema_contains_no_identity_columns(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)

    with sqlite3.connect(path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(partner_clicks)").fetchall()
        }

    assert {"service", "destination", "mode", "source", "created_at"} <= columns
    assert "chat_id" not in columns
    assert "user_id" not in columns
    assert "username" not in columns
    assert "phone" not in columns


def test_record_partner_click_persists_anonymous_dimensions(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)

    bot.record_partner_click("hotel", "Таиланд", "api")

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT service, destination, mode, source FROM partner_clicks"
        ).fetchone()

    assert row == ("hotel", "Таиланд", "api", "telegram_mini_app")


def test_record_partner_click_rejects_unknown_dimensions(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)

    bot.record_partner_click("unknown", "Таиланд", "api")
    bot.record_partner_click("hotel", "Таиланд", "mystery")

    with sqlite3.connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM partner_clicks").fetchone()[0]

    assert count == 0


def test_partner_endpoint_records_resolved_click(monkeypatch):
    captured = []
    monkeypatch.setattr(bot, "BOT_TOKEN", "token")

    # This endpoint test focuses on the analytics handoff. Signature/origin
    # validation already has dedicated coverage elsewhere.
    monkeypatch.setattr(
        bot,
        "validate_init_data",
        lambda *_args, **_kwargs: {"id": 42},
    )
    monkeypatch.setattr(
        bot._travelpayouts_links,
        "resolve_esim_link",
        lambda destination: ("https://airalo.tp.st/example", "redirect"),
    )
    monkeypatch.setattr(
        bot,
        "record_partner_click",
        lambda service, destination, mode, **_kwargs: captured.append(
            (service, destination, mode)
        ),
    )

    origin = bot.MINI_APP_ORIGIN
    response = bot.app.test_client().post(
        "/miniapp/partner-link",
        json={
            "initData": "signed",
            "service": "esim",
            "destination": "Вьетнам",
        },
        headers={"Origin": origin},
    )

    assert response.status_code == 200
    assert captured == [("esim", "Вьетнам", "redirect")]


def test_admin_analytics_includes_partner_click_summary(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    bot.record_partner_click("hotel", "Таиланд", "api")
    bot.record_partner_click("hotel", "Таиланд", "redirect")
    bot.record_partner_click("transfer", "Вьетнам", "direct")

    sent = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or True,
    )

    assert bot._admin_analytics(999, "") is True
    text = sent[-1][1]

    assert "Партнёрские переходы" in text
    assert "за 30 дней: 3" in text
    assert "отели: 2" in text
    assert "трансферы: 1" in text
    assert "Partner Links API: 1" in text
    assert "affiliate redirect: 1" in text
    assert "прямой fallback: 1" in text
    assert "Таиланд: 2" in text
