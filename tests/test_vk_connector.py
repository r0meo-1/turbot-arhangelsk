import json
import sqlite3
from contextlib import contextmanager

from flask import Flask

from shared.funnel_metrics import init_schema
from shared.vk_connector import create_blueprint


AUTH_TOKEN = "connector-auth-token-" + ("x" * 32)
VK_TOKEN = "server-only-vk-token"


class FixtureDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        init_schema(self.conn.cursor())
        self.conn.commit()

    @contextmanager
    def cursor(self, commit=False):
        cur = self.conn.cursor()
        try:
            yield cur
            if commit:
                self.conn.commit()
        except BaseException:
            if commit:
                self.conn.rollback()
            raise
        finally:
            cur.close()

    def close(self):
        self.conn.close()


def _client(db, calls):
    def fake_vk_call(method, token, **params):
        calls.append((method, token, params))
        if method == "groups.getById":
            return [{
                "id": 240310110,
                "name": "АПРЕЛЬ тур",
                "screen_name": "club240310110",
                "is_closed": 0,
                "type": "group",
                "members_count": 123,
                "ignored_secret": "must-not-pass-through",
            }]
        if method == "wall.get":
            return {
                "count": 2,
                "items": [
                    {
                        "id": 101,
                        "owner_id": -240310110,
                        "date": 1_790_000_000,
                        "text": "Тестовый пост",
                        "comments": {"count": 3},
                        "likes": {"count": 4},
                        "reposts": {"count": 5},
                        "views": {"count": 6},
                        "attachments": [{"type": "photo", "access_key": "private-ish"}],
                    }
                ],
            }
        raise AssertionError(method)

    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(
            db.cursor,
            token_getter=lambda: AUTH_TOKEN,
            resolve_identity=lambda: (VK_TOKEN, -240310110, 240310110),
            vk_call=fake_vk_call,
        )
    )
    return app.test_client()


def _rpc(client, method, params=None, *, token=AUTH_TOKEN, request_id=1):
    return client.post(
        "/mcp/vk",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }),
    )


def test_connector_auth_is_fail_closed():
    db = FixtureDB()
    try:
        app = Flask(__name__)
        app.register_blueprint(
            create_blueprint(
                db.cursor,
                token_getter=lambda: "",
                resolve_identity=lambda: (VK_TOKEN, -240310110, 240310110),
            )
        )
        client = app.test_client()
        assert _rpc(client, "tools/list").status_code == 503

        calls = []
        client = _client(db, calls)
        missing = client.post(
            "/mcp/vk",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert missing.status_code == 401
        assert _rpc(client, "tools/list", token="wrong-" + ("z" * 40)).status_code == 401
        assert calls == []
    finally:
        db.close()


def test_initialize_and_tool_list_are_read_only():
    db = FixtureDB()
    calls = []
    try:
        client = _client(db, calls)
        response = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        )
        assert response.status_code == 200
        assert response.json["result"]["protocolVersion"] == "2025-11-25"

        listed = _rpc(client, "tools/list")
        names = {item["name"] for item in listed.json["result"]["tools"]}
        assert names == {
            "vk.get_group",
            "vk.list_posts",
            "vk.get_campaign_attribution",
            "vk.get_leads_by_source",
        }
        assert not names & {
            "vk.create_post",
            "vk.schedule_post",
            "vk.reply_comment",
            "vk.send_message",
        }
        assert calls == []
    finally:
        db.close()


def test_group_and_wall_reads_reuse_server_side_vk_identity():
    db = FixtureDB()
    calls = []
    try:
        client = _client(db, calls)

        group = _rpc(
            client,
            "tools/call",
            {"name": "vk.get_group", "arguments": {}},
        )
        assert group.status_code == 200
        structured = group.json["result"]["structuredContent"]
        assert structured["group"]["id"] == 240310110
        assert structured["group"]["name"] == "АПРЕЛЬ тур"
        body = group.get_data(as_text=True)
        assert VK_TOKEN not in body
        assert "must-not-pass-through" not in body

        posts = _rpc(
            client,
            "tools/call",
            {"name": "vk.list_posts", "arguments": {"limit": 10, "offset": 0}},
        )
        item = posts.json["result"]["structuredContent"]["items"][0]
        assert item == {
            "id": 101,
            "owner_id": -240310110,
            "date": 1_790_000_000,
            "text": "Тестовый пост",
            "comments": 3,
            "likes": 4,
            "reposts": 5,
            "views": 6,
        }
        assert calls[0][0] == "groups.getById"
        assert calls[1][0] == "wall.get"
        assert all(call[1] == VK_TOKEN for call in calls)
    finally:
        db.close()


def test_write_tools_fail_closed_without_touching_vk():
    db = FixtureDB()
    calls = []
    try:
        client = _client(db, calls)
        response = _rpc(
            client,
            "tools/call",
            {
                "name": "vk.send_message",
                "arguments": {"peer_id": 42, "message": "do not send"},
            },
        )
        assert response.status_code == 200
        assert response.json["error"]["code"] == -32010
        assert response.json["error"]["message"] == "VK write tools are disabled"
        assert calls == []
    finally:
        db.close()


def test_attribution_actions_return_counts_without_customer_data():
    db = FixtureDB()
    calls = []
    try:
        with db.cursor(commit=True) as cur:
            rows = [
                ("vk", "video_pain", "start", "opened", 1_790_000_000),
                ("vk", "video_pain", "lead", "accepted", 1_790_000_001),
                ("vk", "video_pain", "lead", "duplicate", 1_790_000_002),
                ("vk", "video_pain", "manager", "delivered", 1_790_000_003),
                ("vk", "other", "lead", "accepted", 1_790_000_004),
                ("telegram", "video_pain", "lead", "accepted", 1_790_000_005),
            ]
            cur.executemany(
                """
                INSERT INTO acquisition_funnel_events(
                    channel, source, stage, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

        client = _client(db, calls)
        by_source = _rpc(
            client,
            "tools/call",
            {
                "name": "vk.get_leads_by_source",
                "arguments": {"source_tag": "Video_Pain", "window_days": 90},
            },
        )
        data = by_source.json["result"]["structuredContent"]
        assert data["source_tag"] == "video_pain"
        assert data["start_opened"] == 1
        assert data["leads_accepted"] == 1
        assert data["leads_duplicate"] == 1
        assert data["manager_delivered"] == 1
        serialized = by_source.get_data(as_text=True)
        for forbidden in ("phone", "email", "first_name", "vk_user_id"):
            assert forbidden not in serialized.lower()

        invalid = _rpc(
            client,
            "tools/call",
            {
                "name": "vk.get_leads_by_source",
                "arguments": {"source_tag": "https://example.com/user/12345678"},
            },
        )
        assert invalid.json["error"]["code"] == -32602
        assert calls == []
    finally:
        db.close()
