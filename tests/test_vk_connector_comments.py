import json

import pytest
from flask import Flask

from shared.vk_connector import VKConnectorError, VKReadActions, create_blueprint


AUTH = "fixture-connector-" + "x" * 32
OWNER = -240310110


@pytest.mark.parametrize("modern", [False, True])
def test_comments_rpc_projects_bounded_text_without_profiles_or_threads(modern):
    calls = []

    def upstream(method, token, **params):
        calls.append((method, params))
        return {"count": 1, "profiles": [{"phone": "private-profile"}], "items": [{
            "id": 8, "date": 123, "text": "a" * 2100,
            "from_id": 999, "attachments": [{"access_key": "private-attachment"}],
            "thread": {"items": [{"text": "private-thread"}]},
        }]}

    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        lambda: None, token_getter=lambda: AUTH,
        resolve_identity=lambda: ("server-token", OWNER, -OWNER), vk_call=upstream,
    ))
    params = {"name": "vk.get_comments", "arguments": {"post_id": 7, "limit": 1}}
    headers = {"Authorization": f"Bearer {AUTH}"}
    if modern:
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
        headers.update({"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call",
                        "Mcp-Name": "vk.get_comments"})
    response = app.test_client().post("/mcp/vk", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params,
    })
    assert response.status_code == 200
    data = response.json["result"]["structuredContent"]
    assert data == {"owner_id": OWNER, "post_id": 7, "count": 1, "offset": 0,
                    "items": [{"id": 8, "date": 123, "text": "a" * 2000}]}
    assert json.loads(response.json["result"]["content"][0]["text"]) == data
    assert calls == [("wall.getComments", {"owner_id": OWNER, "post_id": 7, "count": 1,
        "offset": 0, "sort": "desc", "extended": 0, "need_likes": 0,
        "preview_length": 2000, "thread_items_count": 0})]
    assert "private-" not in response.get_data(as_text=True)
    assert "server-token" not in response.get_data(as_text=True)


@pytest.mark.parametrize("arguments", [
    {}, {"post_id": True}, {"post_id": 0}, {"post_id": "7"},
    {"post_id": 2147483648}, {"post_id": 7, "owner_id": -999},
    {"post_id": 7, "limit": 0}, {"post_id": 7, "limit": 101},
    {"post_id": 7, "offset": -1}, {"post_id": 7, "offset": 10001},
])
def test_invalid_arguments_never_resolve_credentials(arguments):
    def forbidden():
        pytest.fail("credentials must not be resolved")
    actions = VKReadActions(lambda: None, resolve_identity=forbidden)
    with pytest.raises(VKConnectorError) as error:
        actions.call("vk.get_comments", arguments)
    assert error.value.code == -32602


@pytest.mark.parametrize("raw", [
    {}, {"count": 0, "items": {}}, {"count": True, "items": []},
    {"count": -1, "items": []},
    {"count": 1, "items": [{"id": True, "date": 0, "text": "x"}]},
    {"count": 1, "items": [{"id": 1, "date": -1, "text": "x"}]},
    {"count": 1, "items": [{"id": 1, "date": 0, "text": {"secret": "bad"}}]},
    {"count": 1, "items": [{"id": 1, "date": 0, "text": "x", "owner_id": -999}]},
    {"count": 1, "items": [{"id": 1, "date": 0, "text": "x", "post_id": 8}]},
    {"count": 2, "items": [{"id": 1, "date": 0, "text": "x"}] * 2},
    {"count": 3, "items": [{"id": n, "date": 0, "text": "x"} for n in (1, 2, 3)]},
])
def test_malformed_upstream_fails_closed(raw):
    actions = VKReadActions(lambda: None,
        resolve_identity=lambda: ("fixture", OWNER, -OWNER), vk_call=lambda *a, **kw: raw)
    with pytest.raises(VKConnectorError, match="Invalid VK comments response"):
        actions.call("vk.get_comments", {"post_id": 7, "limit": 2})


def test_empty_comments_are_valid():
    actions = VKReadActions(lambda: None,
        resolve_identity=lambda: ("fixture", OWNER, -OWNER),
        vk_call=lambda *a, **kw: {"count": 0, "items": []})
    assert actions.call("vk.get_comments", {"post_id": 7})["items"] == []
