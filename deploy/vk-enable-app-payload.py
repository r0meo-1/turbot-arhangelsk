#!/usr/bin/env python3
"""Enable VK Callback API app_payload without disturbing existing event flags.

This script is intended to run only on the production host through the
restricted deploy entrypoint. It reads the existing VK group token from the
server .env, snapshots callback settings, enables app_payload, then verifies
that every other event flag stayed unchanged. If VK changes anything else,
it attempts to restore the snapshot and exits non-zero.
"""
from __future__ import annotations

import re
import sys
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values

ENV_PATH = "/opt/turbot/.env"
SAFE_EVENT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _bool_int(value: object) -> int | None:
    if value in (1, True, "1"):
        return 1
    if value in (0, False, "0"):
        return 0
    return None


def main() -> int:
    values = dotenv_values(ENV_PATH)
    token = str(values.get("VK_ACCESS_TOKEN") or "").strip()
    group_raw = str(values.get("VK_GROUP_ID") or "").strip()
    public_url = str(values.get("PUBLIC_BASE_URL") or "").strip()
    api_version = str(values.get("VK_API_VERSION") or "5.199").strip() or "5.199"

    if not token:
        raise SystemExit("VK_ACCESS_TOKEN is not configured")
    if not group_raw.isdigit() or int(group_raw) <= 0:
        raise SystemExit("VK_GROUP_ID is invalid")

    group_id = int(group_raw)
    expected_host = urlparse(
        public_url if "://" in public_url else "https://" + public_url
    ).hostname or ""

    session = requests.Session()

    def call(method: str, **params: object) -> dict:
        params.update(access_token=token, v=api_version)
        response = session.post(
            f"https://api.vk.ru/method/{method}",
            data=params,
            timeout=12,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            err = body.get("error") or {}
            raise RuntimeError(
                f"VK API {method} error_code={err.get('error_code', 'unknown')}"
            )
        result = body.get("response")
        if isinstance(result, dict):
            return result
        return {"value": result}

    servers = call("groups.getCallbackServers", group_id=group_id)
    items = list(servers.get("items") or [])
    target = None
    for item in items:
        url = str(item.get("url") or "")
        parsed = urlparse(url)
        if parsed.path.rstrip("/").endswith("/vk/webhook") and (
            not expected_host or parsed.hostname == expected_host
        ):
            target = item
            break
    if target is None and len(items) == 1:
        target = items[0]
    if target is None:
        raise SystemExit(f"VK Callback server not uniquely identified (count={len(items)})")

    server_id = int(target.get("id") or 0)
    if server_id <= 0:
        raise SystemExit("VK Callback server id is invalid")

    def read_events() -> dict[str, int]:
        settings = call(
            "groups.getCallbackSettings",
            group_id=group_id,
            server_id=server_id,
        )
        raw = settings.get("events") if isinstance(settings.get("events"), dict) else settings
        result: dict[str, int] = {}
        for key, value in (raw or {}).items():
            if not isinstance(key, str) or not SAFE_EVENT.fullmatch(key):
                continue
            normalized = _bool_int(value)
            if normalized is not None:
                result[key] = normalized
        return result

    before = read_events()
    if not before:
        raise SystemExit("VK Callback event snapshot is empty")

    enabled_before = sorted(key for key, value in before.items() if value)
    if before.get("app_payload") == 1:
        print(
            "VK Callback app_payload already enabled; "
            f"server_id={server_id} preserved_events={len(before) - 1}"
        )
        return 0

    response = call(
        "groups.setCallbackSettings",
        group_id=group_id,
        server_id=server_id,
        app_payload=1,
    )
    if response.get("value") not in (1, True, "1", None):
        raise SystemExit("VK API did not acknowledge callback settings update")

    after = read_events()
    changed_other = {
        key: (before.get(key), after.get(key))
        for key in sorted(set(before) | set(after))
        if key != "app_payload" and before.get(key) != after.get(key)
    }

    if after.get("app_payload") != 1 or changed_other:
        # Restore every boolean event VK returned in the original snapshot.
        # Using the exact snapshot protects message_new and all other existing
        # subscriptions if the API ever changes partial-update semantics.
        rollback_params: dict[str, object] = {
            "group_id": group_id,
            "server_id": server_id,
        }
        rollback_params.update(before)
        try:
            call("groups.setCallbackSettings", **rollback_params)
            restored = read_events()
            rollback_ok = all(restored.get(k) == v for k, v in before.items())
        except Exception:
            rollback_ok = False

        print(
            "VK Callback app_payload verification failed; "
            f"changed_other={','.join(changed_other) or 'none'} "
            f"rollback={'ok' if rollback_ok else 'failed'}",
            file=sys.stderr,
        )
        return 1

    enabled_after = sorted(key for key, value in after.items() if value)
    if [k for k in enabled_after if k != "app_payload"] != enabled_before:
        raise SystemExit("Enabled event set changed unexpectedly")

    print(
        "VK Callback app_payload enabled; "
        f"server_id={server_id} preserved_enabled={','.join(enabled_before) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
