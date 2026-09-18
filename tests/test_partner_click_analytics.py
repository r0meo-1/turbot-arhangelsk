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


def test_partner_analytics_respects_requested_window(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)
    now = 2_000_000_000
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO partner_clicks "
            "(service, destination, mode, source, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("hotel", "Таиланд", "api", "telegram_mini_app", now - 2 * 86400),
                ("esim", "Вьетнам", "redirect", "telegram_mini_app", now - 10 * 86400),
                ("transfer", "Таиланд", "direct", "telegram_mini_app", now - 40 * 86400),
            ],
        )
        conn.execute(
            "INSERT INTO leads "
            "(chat_id, destination, phone, created_at) VALUES (?, ?, ?, ?)",
            (101, "Таиланд", "+70000000000", now - 3 * 86400),
        )
        conn.commit()

    monkeypatch.setattr(bot.time, "time", lambda: now)

    seven = bot.get_partner_analytics(7)
    thirty = bot.get_partner_analytics(30)

    assert seven["total"] == 1
    assert seven["leads"] == 1
    assert seven["by_service"] == [("hotel", 1)]
    assert thirty["total"] == 2
    assert dict(thirty["by_service"]) == {"hotel": 1, "esim": 1}
    assert thirty["affiliate_resolution_rate"] == 100.0


def test_cleanup_partner_clicks_honors_retention(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)
    now = 2_000_000_000
    monkeypatch.setattr(bot, "PARTNER_ANALYTICS_RETENTION_DAYS", 30)

    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO partner_clicks "
            "(service, destination, mode, source, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("hotel", "Таиланд", "api", "telegram_mini_app", now - 29 * 86400),
                ("esim", "Вьетнам", "redirect", "telegram_mini_app", now - 31 * 86400),
            ],
        )
        conn.commit()

    assert bot.cleanup_partner_clicks(now=now) == 1
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT service FROM partner_clicks ORDER BY service"
        ).fetchall()
    assert rows == [("hotel",)]


def test_cleanup_partner_clicks_can_be_disabled(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "PARTNER_ANALYTICS_RETENTION_DAYS", 0)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO partner_clicks "
            "(service, destination, mode, source, created_at) VALUES (?, ?, ?, ?, ?)",
            ("hotel", "Таиланд", "api", "telegram_mini_app", 1),
        )
        conn.commit()

    assert bot.cleanup_partner_clicks(now=2_000_000_000) == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM partner_clicks").fetchone()[0] == 1


def test_admin_partners_reports_aggregate_not_attribution(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)
    now = 2_000_000_000
    monkeypatch.setattr(bot.time, "time", lambda: now)

    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO partner_clicks "
            "(service, destination, mode, source, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("hotel", "Таиланд", "api", "telegram_mini_app", now - 100),
                ("transfer", "Таиланд", "direct", "telegram_mini_app", now - 200),
            ],
        )
        conn.execute(
            "INSERT INTO leads "
            "(chat_id, destination, phone, created_at) VALUES (?, ?, ?, ?)",
            (102, "Таиланд", "+70000000001", now - 300),
        )
        conn.commit()

    sent = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append((chat_id, text)) or True,
    )

    assert bot._admin_partners(999, "30") is True
    text = sent[-1][1]
    assert "Партнёрская аналитика · 30 дн." in text
    assert "Переходов: 2" in text
    assert "Заявок в боте за тот же период: 1" in text
    assert "Сопоставление объёмов: 50.0 заявок на 100 переходов" in text
    assert "не связываются по человеку" in text
    assert "не user-level attribution" in text


def test_admin_partners_validates_window(monkeypatch):
    sent = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append(text) or True,
    )
    assert bot._admin_partners(999, "0") is True
    assert "от 1 до 365" in sent[-1]
    assert bot._admin_partners(999, "banana") is True
    assert "Использование" in sent[-1]


def test_admin_partners_includes_travelpayouts_money(monkeypatch, tmp_path):
    path = _use_temp_db(monkeypatch, tmp_path)
    now = 2_000_000_000
    monkeypatch.setattr(bot.time, "time", lambda: now)

    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO partner_clicks "
            "(service, destination, mode, source, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("hotel", "Таиланд", "api", "telegram_mini_app", now - 100),
                ("transfer", "Вьетнам", "redirect", "telegram_mini_app", now - 200),
            ],
        )
        conn.commit()

    monkeypatch.setattr(
        bot._travelpayouts_stats,
        "fetch_partner_performance",
        lambda days, force=False: {
            "totals": {
                "bookings": 2,
                "paid": 1,
                "processing": 1,
                "canceled": 0,
                "other": 0,
                "booking_value_eur": 620.0,
                "paid_profit_eur": 42.5,
            },
            "by_service": {
                "hotel": {
                    "bookings": 1,
                    "paid": 1,
                    "processing": 0,
                    "canceled": 0,
                    "other": 0,
                    "booking_value_eur": 500.0,
                    "paid_profit_eur": 35.0,
                },
                "esim": {
                    "bookings": 0,
                    "paid": 0,
                    "processing": 0,
                    "canceled": 0,
                    "other": 0,
                    "booking_value_eur": 0.0,
                    "paid_profit_eur": 0.0,
                },
                "transfer": {
                    "bookings": 1,
                    "paid": 0,
                    "processing": 1,
                    "canceled": 0,
                    "other": 0,
                    "booking_value_eur": 120.0,
                    "paid_profit_eur": 7.5,
                },
            },
        },
    )

    sent = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append(text) or True,
    )

    assert bot._admin_partners(999, "30") is True
    text = sent[-1]
    assert "💶 Travelpayouts:" in text
    assert "Брони: 2" in text
    assert "оплачено: 1" in text
    assert "в обработке: 1" in text
    assert "Подтверждённый доход: €42.50" in text
    assert "Стоимость неотменённых броней: €620.00" in text
    assert "active bookings / affiliate clicks: 100.0%" in text
    assert "🏨 Отели: 1 брон., 1 оплач., €35.00" in text
    assert "🚕 Трансферы: 1 брон., 0 оплач., €7.50" in text


def test_admin_partners_survives_stats_api_failure(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)

    def fail(_days, force=False):
        raise bot._travelpayouts_stats.TravelpayoutsStatsError("down")

    monkeypatch.setattr(bot._travelpayouts_stats, "fetch_partner_performance", fail)
    sent = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append(text) or True,
    )

    assert bot._admin_partners(999, "30") is True
    assert "Статистика временно недоступна" in sent[-1]


def test_admin_partners_reload_forces_travelpayouts_refresh(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    captured = []

    def fake_performance(days, *, force=False):
        captured.append((days, force))
        return {
            "totals": {
                "bookings": 0,
                "paid": 0,
                "processing": 0,
                "canceled": 0,
                "other": 0,
                "booking_value_eur": 0.0,
                "paid_profit_eur": 0.0,
            },
            "by_service": {
                "hotel": {"bookings": 0, "paid": 0, "paid_profit_eur": 0.0},
                "esim": {"bookings": 0, "paid": 0, "paid_profit_eur": 0.0},
                "transfer": {"bookings": 0, "paid": 0, "paid_profit_eur": 0.0},
            },
        }

    monkeypatch.setattr(
        bot._travelpayouts_stats,
        "fetch_partner_performance",
        fake_performance,
    )
    sent = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda chat_id, text, **kwargs: sent.append(text) or True,
    )

    assert bot._admin_partners(999, "90 reload") is True
    assert captured == [(90, True)]
    assert "♻️ Данные принудительно обновлены." in sent[-1]


def test_admin_partners_reload_uses_default_window(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    captured = []

    def fail(days, *, force=False):
        captured.append((days, force))
        raise bot._travelpayouts_stats.TravelpayoutsStatsError("down")

    monkeypatch.setattr(bot._travelpayouts_stats, "fetch_partner_performance", fail)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: True)

    assert bot._admin_partners(999, "reload") is True
    assert captured == [(30, True)]
