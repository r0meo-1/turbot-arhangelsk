"""Read-only VK MCP connector for TurBot.

The connector deliberately exposes only privacy-safe/read-only actions in its
first production slice. VK write methods are recognized but always rejected.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

import vk_autopost as _vk_autopost
from shared.funnel_metrics import snapshot as funnel_snapshot


_MAX_BODY_BYTES = 64 * 1024
_MAX_POST_TEXT = 4000
_SOURCE_TAG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUPPORTED_PROTOCOLS = {"2025-03-26", "2025-06-18", "2025-11-25"}
_MODERN_PROTOCOL = "2026-07-28"
_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
_SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
_SERVER_INFO = {"name": "turbot-vk", "version": "0.2.0"}
_WRITE_TOOLS = {
    "vk.create_post",
    "vk.schedule_post",
    "vk.reply_comment",
    "vk.send_message",
}


class VKConnectorError(RuntimeError):
    """Safe connector error that never carries a VK token."""

    def __init__(self, message: str, *, code: int = -32000):
        super().__init__(message)
        self.code = int(code)


def _positive_int(arguments: dict[str, Any], key: str, *, default: int, maximum: int) -> int:
    value = arguments.get(key, default)
    if type(value) is not int or value < 0 or value > maximum:
        raise VKConnectorError(f"{key} must be an integer from 0 to {maximum}", code=-32602)
    return value


def _source_tag(arguments: dict[str, Any]) -> str:
    raw = str(arguments.get("source_tag") or "").strip()
    if not _SOURCE_TAG.fullmatch(raw):
        raise VKConnectorError(
            "source_tag must match [A-Za-z0-9_-]{1,64}",
            code=-32602,
        )
    return raw.lower()


def _post_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {
        "id": int(item.get("id") or 0),
        "owner_id": int(item.get("owner_id") or 0),
        "date": int(item.get("date") or 0),
        "text": str(item.get("text") or "")[:_MAX_POST_TEXT],
        "comments": int((item.get("comments") or {}).get("count") or 0),
        "likes": int((item.get("likes") or {}).get("count") or 0),
        "reposts": int((item.get("reposts") or {}).get("count") or 0),
        "views": int((item.get("views") or {}).get("count") or 0),
    }


def _server_meta() -> dict[str, Any]:
    return {_SERVER_INFO_META_KEY: dict(_SERVER_INFO)}


def _complete_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "resultType": "complete",
        **data,
        "_meta": _server_meta(),
    }


def _tool_result(data: dict[str, Any], *, modern: bool = False) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    result = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": False,
    }
    return _complete_result(result) if modern else result


class VKReadActions:
    def __init__(
        self,
        db_cursor_factory: Callable[..., Any],
        *,
        resolve_identity: Callable[[], tuple[str, int, int]] = _vk_autopost.resolve_identity,
        vk_call: Callable[..., Any] = _vk_autopost.vk_call,
    ):
        self.db_cursor_factory = db_cursor_factory
        self.resolve_identity = resolve_identity
        self.vk_call = vk_call

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "vk.get_group",
                "description": "Read public metadata for the configured VK community.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "vk.list_posts",
                "description": "List recent posts from the configured VK community wall.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "vk.get_post_stats",
                "description": "Read aggregate public counters for one configured-community post.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "post_id": {"type": "integer", "minimum": 1, "maximum": 2147483647}
                    },
                    "required": ["post_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "vk.get_campaign_attribution",
                "description": "Return privacy-minimized VK funnel counts by source tag.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "window_days": {"type": "integer", "minimum": 1, "maximum": 90}
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "vk.get_leads_by_source",
                "description": "Return privacy-safe VK lead and manager-delivery counts for one source tag.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source_tag": {
                            "type": "string",
                            "pattern": "^[A-Za-z0-9_-]{1,64}$",
                        },
                        "window_days": {"type": "integer", "minimum": 1, "maximum": 90},
                    },
                    "required": ["source_tag"],
                    "additionalProperties": False,
                },
            },
        ]

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        if name in _WRITE_TOOLS:
            raise VKConnectorError("VK write tools are disabled", code=-32010)
        if not isinstance(arguments, dict):
            raise VKConnectorError("arguments must be an object", code=-32602)

        if name == "vk.get_group":
            return self.get_group(arguments)
        if name == "vk.list_posts":
            return self.list_posts(arguments)
        if name == "vk.get_post_stats":
            return self.get_post_stats(arguments)
        if name == "vk.get_campaign_attribution":
            return self.get_campaign_attribution(arguments)
        if name == "vk.get_leads_by_source":
            return self.get_leads_by_source(arguments)
        raise VKConnectorError("Unknown VK tool", code=-32601)

    def get_group(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments:
            raise VKConnectorError("vk.get_group accepts no arguments", code=-32602)
        token, _owner_id, group_id = self.resolve_identity()
        raw = self.vk_call(
            "groups.getById",
            token,
            group_id=group_id,
            fields="screen_name,is_closed,type,members_count",
        )
        items = raw.get("groups", []) if isinstance(raw, dict) else raw
        group = items[0] if isinstance(items, list) and items else {}
        if not isinstance(group, dict):
            group = {}
        return {
            "group": {
                "id": int(group.get("id") or group_id),
                "name": str(group.get("name") or ""),
                "screen_name": str(group.get("screen_name") or ""),
                "is_closed": int(group.get("is_closed") or 0),
                "type": str(group.get("type") or ""),
                "members_count": int(group.get("members_count") or 0),
            }
        }

    def list_posts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unknown = set(arguments) - {"limit", "offset"}
        if unknown:
            raise VKConnectorError("Unknown vk.list_posts argument", code=-32602)
        limit = _positive_int(arguments, "limit", default=20, maximum=100)
        if limit < 1:
            raise VKConnectorError("limit must be at least 1", code=-32602)
        offset = _positive_int(arguments, "offset", default=0, maximum=10000)
        token, owner_id, _group_id = self.resolve_identity()
        raw = self.vk_call(
            "wall.get",
            token,
            owner_id=owner_id,
            count=limit,
            offset=offset,
            filter="owner",
        )
        items = raw.get("items", []) if isinstance(raw, dict) else []
        return {
            "count": int(raw.get("count") or 0) if isinstance(raw, dict) else 0,
            "offset": offset,
            "items": [_post_item(item) for item in items if isinstance(item, dict)],
        }

    def get_post_stats(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if set(arguments) != {"post_id"}:
            raise VKConnectorError(
                "vk.get_post_stats requires only post_id",
                code=-32602,
            )
        post_id = _positive_int(
            arguments,
            "post_id",
            default=0,
            maximum=2_147_483_647,
        )
        if post_id < 1:
            raise VKConnectorError("post_id must be at least 1", code=-32602)

        token, owner_id, _group_id = self.resolve_identity()
        raw = self.vk_call(
            "wall.getById",
            token,
            posts=f"{owner_id}_{post_id}",
            extended=0,
        )
        items = raw.get("items", []) if isinstance(raw, dict) else raw
        post = items[0] if isinstance(items, list) and items else None
        if not isinstance(post, dict):
            raise VKConnectorError("VK post not found", code=-32044)

        actual_owner = int(post.get("owner_id") or 0)
        actual_id = int(post.get("id") or 0)
        if actual_owner != owner_id or actual_id != post_id:
            raise VKConnectorError("VK post identity mismatch", code=-32044)

        return {
            "post_id": actual_id,
            "owner_id": actual_owner,
            "date": int(post.get("date") or 0),
            "comments": int((post.get("comments") or {}).get("count") or 0),
            "likes": int((post.get("likes") or {}).get("count") or 0),
            "reposts": int((post.get("reposts") or {}).get("count") or 0),
            "views": int((post.get("views") or {}).get("count") or 0),
        }

    def _vk_snapshot(self, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        unknown = set(arguments) - {"window_days", "source_tag"}
        if unknown:
            raise VKConnectorError("Unknown attribution argument", code=-32602)
        days = _positive_int(arguments, "window_days", default=30, maximum=90)
        if days < 1:
            raise VKConnectorError("window_days must be at least 1", code=-32602)
        data = funnel_snapshot(
            self.db_cursor_factory,
            window_seconds=days * 86400,
        )
        channels = data.get("channels") if isinstance(data, dict) else {}
        vk = channels.get("vk", {}) if isinstance(channels, dict) else {}
        return days, vk if isinstance(vk, dict) else {}

    def get_campaign_attribution(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if "source_tag" in arguments:
            raise VKConnectorError("source_tag is not accepted here", code=-32602)
        days, sources = self._vk_snapshot(arguments)
        return {
            "window_days": days,
            "channel": "vk",
            "sources": sources,
        }

    def get_leads_by_source(self, arguments: dict[str, Any]) -> dict[str, Any]:
        tag = _source_tag(arguments)
        days, sources = self._vk_snapshot(arguments)
        node = sources.get(tag, {})
        if not isinstance(node, dict):
            node = {}
        return {
            "window_days": days,
            "channel": "vk",
            "source_tag": tag,
            "start_opened": int((node.get("start") or {}).get("opened") or 0),
            "leads_accepted": int((node.get("lead") or {}).get("accepted") or 0),
            "leads_duplicate": int((node.get("lead") or {}).get("duplicate") or 0),
            "manager_delivered": int((node.get("manager") or {}).get("delivered") or 0),
            "manager_failed": int((node.get("manager") or {}).get("failed") or 0),
        }


def create_blueprint(
    db_cursor_factory: Callable[..., Any],
    *,
    token_getter: Callable[[], str],
    resolve_identity: Callable[[], tuple[str, int, int]] = _vk_autopost.resolve_identity,
    vk_call: Callable[..., Any] = _vk_autopost.vk_call,
) -> Blueprint:
    bp = Blueprint("vk_connector", __name__)
    actions = VKReadActions(
        db_cursor_factory,
        resolve_identity=resolve_identity,
        vk_call=vk_call,
    )

    def response(payload: dict[str, Any], *, status: int = 200) -> Response:
        result = jsonify(payload)
        result.status_code = status
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "no-referrer"
        return result

    def rpc_error(
        request_id: Any,
        code: int,
        message: str,
        *,
        status: int = 200,
    ) -> Response:
        return response(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": int(code), "message": str(message)},
            },
            status=status,
        )

    @bp.post("/mcp/vk")
    def vk_mcp() -> Response:
        if request.query_string:
            return Response(status=400, headers={"Cache-Control": "no-store"})
        configured = str(token_getter() or "").strip()
        if len(configured) < 32:
            return Response(status=503, headers={"Cache-Control": "no-store"})
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return Response(status=401, headers={"Cache-Control": "no-store"})
        supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(configured, supplied):
            return Response(status=401, headers={"Cache-Control": "no-store"})
        if request.mimetype != "application/json":
            return Response(status=415, headers={"Cache-Control": "no-store"})
        if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
            return Response(status=413, headers={"Cache-Control": "no-store"})
        body = request.get_data(cache=False)
        if len(body) > _MAX_BODY_BYTES:
            return Response(status=413, headers={"Cache-Control": "no-store"})
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            return rpc_error(None, -32700, "Parse error")
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return rpc_error(payload.get("id") if isinstance(payload, dict) else None, -32600, "Invalid Request")

        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return rpc_error(request_id, -32600, "Invalid Request")

        meta = params.get("_meta")
        envelope_version = (
            meta.get(_PROTOCOL_VERSION_META_KEY)
            if isinstance(meta, dict)
            else None
        )
        modern = method == "server/discover" or envelope_version is not None

        if modern:
            if envelope_version != _MODERN_PROTOCOL:
                return rpc_error(
                    request_id,
                    -32022,
                    "Unsupported protocol version",
                    status=400,
                )
            if (
                request.headers.get("MCP-Protocol-Version") != _MODERN_PROTOCOL
                or request.headers.get("Mcp-Method") != method
            ):
                return rpc_error(
                    request_id,
                    -32020,
                    "MCP standard header mismatch",
                    status=400,
                )
            if method == "tools/call":
                tool_name = params.get("name")
                if (
                    not isinstance(tool_name, str)
                    or not tool_name
                    or request.headers.get("Mcp-Name") != tool_name
                ):
                    return rpc_error(
                        request_id,
                        -32020,
                        "MCP tool name header mismatch",
                        status=400,
                    )

        if method == "server/discover":
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _complete_result(
                        {
                            "supportedVersions": [_MODERN_PROTOCOL],
                            "capabilities": {"tools": {"listChanged": False}},
                            "instructions": (
                                "Read-only TurBot VK connector. "
                                "VK write tools are disabled."
                            ),
                        }
                    ),
                }
            )

        if method == "notifications/initialized" and not modern:
            return Response(status=204, headers={"Cache-Control": "no-store"})
        if method == "ping" and not modern:
            return response({"jsonrpc": "2.0", "id": request_id, "result": {}})
        if method == "initialize" and not modern:
            requested = str(params.get("protocolVersion") or "")
            protocol = requested if requested in _SUPPORTED_PROTOCOLS else "2025-11-25"
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": protocol,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": dict(_SERVER_INFO),
                    },
                }
            )
        if method == "tools/list":
            result = {"tools": actions.tools()}
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _complete_result(result) if modern else result,
                }
            )
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not name:
                return rpc_error(request_id, -32602, "Tool name is required")
            try:
                data = actions.call(name, arguments)
            except VKConnectorError as exc:
                return rpc_error(request_id, exc.code, str(exc))
            except _vk_autopost.VKAutopostError:
                return rpc_error(request_id, -32020, "VK API request failed")
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _tool_result(data, modern=modern),
                }
            )
        return rpc_error(request_id, -32601, "Method not found")

    return bp
