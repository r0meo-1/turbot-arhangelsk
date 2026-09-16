#!/usr/bin/env bash
set -Eeuo pipefail

repo=${TURBOT_ROOT:-/opt/turbot}
venv=${TURBOT_VENV:-$repo/venv/bin}
env_file=${TURBOT_ENV_FILE:-$repo/.env}

run_mdt_check() {
  if [[ -f "$repo/deploy/verify-mdt.sh" ]]; then
    chmod +x "$repo/deploy/verify-mdt.sh" 2>/dev/null || true
    "$repo/deploy/verify-mdt.sh" || true
  fi
}
trap run_mdt_check EXIT

if [[ ! -x "$venv/python" ]]; then
  echo "Python runtime not found: $venv/python" >&2
  exit 1
fi

read_config() {
  "$venv/python" - "$env_file" <<'PY'
import sys
from urllib.parse import urlparse
from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
app_id = str(values.get('VK_MINI_APP_ID') or '').strip()
public_url = str(values.get('PUBLIC_BASE_URL') or '').strip()
if public_url and '://' not in public_url:
    public_url = 'https://' + public_url
host = urlparse(public_url).hostname or ''
print('1' if app_id.isdigit() and int(app_id) > 0 else '0')
print(host)
PY
}

inspect_callback_settings() {
  "$venv/python" - "$env_file" <<'PY'
import sys
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
token = str(values.get('VK_ACCESS_TOKEN') or '').strip()
group_raw = str(values.get('VK_GROUP_ID') or '').strip()
public_url = str(values.get('PUBLIC_BASE_URL') or '').strip()
api_version = str(values.get('VK_API_VERSION') or '5.199').strip() or '5.199'

if not token or not group_raw.isdigit() or int(group_raw) <= 0:
    print('VK Callback API: readiness check skipped (token/group not configured)')
    raise SystemExit(0)

group_id = int(group_raw)
expected_host = urlparse(public_url if '://' in public_url else 'https://' + public_url).hostname or ''


def call(method, **params):
    params.update(access_token=token, v=api_version)
    response = requests.post(
        f'https://api.vk.ru/method/{method}',
        data=params,
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    if 'error' in body:
        err = body.get('error') or {}
        raise RuntimeError(f"VK API {method} error_code={err.get('error_code', 'unknown')}")
    return body.get('response') or {}


try:
    servers = call('groups.getCallbackServers', group_id=group_id)
    items = list(servers.get('items') or [])
    target = None
    for item in items:
        url = str(item.get('url') or '')
        parsed = urlparse(url)
        if parsed.path.rstrip('/').endswith('/vk/webhook') and (not expected_host or parsed.hostname == expected_host):
            target = item
            break
    if target is None and len(items) == 1:
        target = items[0]
    if target is None:
        print(f'VK Callback API: server_found=no count={len(items)} app_payload=unknown')
        raise SystemExit(0)

    server_id = int(target.get('id') or 0)
    settings = call('groups.getCallbackSettings', group_id=group_id, server_id=server_id)
    events = settings.get('events') if isinstance(settings.get('events'), dict) else settings
    enabled = bool((events or {}).get('app_payload'))
    status = str(target.get('status') or 'unknown').replace(' ', '_')[:32]
    print(
        'VK Callback API: '
        f'server_found=yes server_id={server_id} status={status} '
        f'app_payload={"enabled" if enabled else "disabled"}'
    )
except Exception as exc:
    # This probe is read-only and advisory while app_payload is not yet part of
    # the production handoff. Never expose the access token or response body.
    print(f'VK Callback API: readiness check unavailable ({exc})')
PY
}

mapfile -t config < <(read_config)
miniapp_enabled=${config[0]:-0}
host=${config[1]:-}

if [[ "$miniapp_enabled" != "1" ]]; then
  echo "VK Mini App smoke check skipped: VK_MINI_APP_ID is not configured"
  exit 0
fi

if [[ -z "$host" ]]; then
  echo "VK Mini App configured but PUBLIC_BASE_URL has no hostname" >&2
  exit 1
fi

if ! command -v nginx >/dev/null 2>&1; then
  echo "VK Mini App configured but nginx is unavailable" >&2
  exit 1
fi

nginx -t
nginx_dump=$(nginx -T 2>&1)
if ! grep -Eq 'location[[:space:]]+/vk/' <<<"$nginx_dump"; then
  echo "nginx does not expose the required /vk/ proxy" >&2
  exit 1
fi

for _ in {1..12}; do
  if curl --fail --silent --show-error --max-time 5 \
      http://127.0.0.1:5100/vk/health >/dev/null; then
    break
  fi
  sleep 2
done

curl --fail --silent --show-error --max-time 8 \
  --resolve "$host:443:127.0.0.1" \
  "https://$host/vk/health" >/dev/null

curl --fail --silent --show-error --max-time 8 \
  --resolve "$host:443:127.0.0.1" \
  "https://$host/vk/miniapp/" >/dev/null

echo "VK public health and Mini App routes healthy"
inspect_callback_settings
