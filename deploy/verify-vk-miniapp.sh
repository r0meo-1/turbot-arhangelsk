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
    events = events or {}
    enabled = bool(events.get('app_payload'))
    enabled_events = sorted(
        str(key) for key, value in events.items()
        if key != 'app_payload' and value in (1, True, '1')
    )
    status = str(target.get('status') or 'unknown').replace(' ', '_')[:32]
    print(
        'VK Callback API: '
        f'server_found=yes server_id={server_id} status={status} '
        f'app_payload={"enabled" if enabled else "disabled"}'
    )
    print(
        'VK Callback API: enabled_events=' +
        (','.join(enabled_events) if enabled_events else 'none')
    )
except Exception as exc:
    # Read-only probe. Never expose the access token or response body.
    print(f'VK Callback API: readiness check unavailable ({exc})')
PY
}

inspect_travelata() {
  "$venv/python" - "$env_file" <<'PY'
import sys
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
username = str(values.get('TRAVELATA_USERNAME') or '').strip()
password = str(values.get('TRAVELATA_PASSWORD') or '').strip()
flag = str(values.get('VK_TRAVELATA_ENABLED') or '').strip().lower()
credentials_set = bool(username and password)
enabled = flag in {'1', 'true', 'yes'} if flag else credentials_set
base_url = str(
    values.get('TRAVELATA_BASE_URL')
    or 'https://api-gateway.travelata.ru'
).strip().rstrip('/')
host = urlparse(base_url).hostname or 'unknown'

print(
    'Travelata config: '
    f'enabled={"yes" if enabled else "no"} '
    f'credentials={"set" if credentials_set else "unset"} '
    f'endpoint_host={host}'
)
if not enabled or not credentials_set:
    raise SystemExit(0)

try:
    response = requests.get(
        base_url + '/partners/directory/departureCities',
        params={'disabled': 0},
        auth=(username, password),
        headers={'Accept': 'application/json'},
        timeout=10,
    )
    status = response.status_code
    response.raise_for_status()
    body = response.json()
    success = isinstance(body, dict) and body.get('success') is True
    print(
        'Travelata API: '
        f'http={status} success={"yes" if success else "no"}'
    )
except requests.HTTPError as exc:
    status = exc.response.status_code if exc.response is not None else 'unknown'
    print(f'Travelata API: http_error={status}')
except Exception as exc:
    # Read-only probe. Never print credentials, auth headers, URL query, or body.
    print(f'Travelata API: probe_error={type(exc).__name__}')
PY
}

inspect_tourvisor() {
  PYTHONPATH="$repo" "$venv/python" - "$env_file" <<'PY'
import base64
import json
import sys
import time
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values
from shared.tourvisor import _find_named_id

values = dotenv_values(sys.argv[1])
token = str(values.get('TOURVISOR_TOKEN') or '').strip()
flag = str(values.get('VK_TOURVISOR_ENABLED') or '').strip().lower()
enabled = flag in {'1', 'true', 'yes'} if flag else bool(token)
base_url = str(
    values.get('TOURVISOR_BASE_URL')
    or 'https://api.tourvisor.ru/search/api/v1'
).strip().rstrip('/')
host = urlparse(base_url).hostname or 'unknown'

jwt_exp_status = 'absent'
if token:
    try:
        parts = token.split('.')
        if len(parts) == 3:
            payload_raw = parts[1] + '=' * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_raw).decode('utf-8'))
            exp = payload.get('exp')
            if isinstance(exp, (int, float)):
                jwt_exp_status = 'expired' if float(exp) <= time.time() else 'valid'
            else:
                jwt_exp_status = 'not_set'
        else:
            jwt_exp_status = 'not_jwt'
    except Exception:
        jwt_exp_status = 'unreadable'

print(
    'Tourvisor config: '
    f'enabled={"yes" if enabled else "no"} '
    f'token={"set" if token else "unset"} '
    f'jwt_exp={jwt_exp_status} '
    f'endpoint_host={host}'
)
if not token:
    raise SystemExit(0)

headers = {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json',
}


def get(path, params):
    response = requests.get(
        base_url + '/' + path.lstrip('/'),
        params=params,
        headers=headers,
        timeout=10,
    )
    status = response.status_code
    response.raise_for_status()
    return status, response.json()


try:
    departures_status, departures = get(
        'departures', {'departureCountryId': 1}
    )
    departure_id = _find_named_id(departures, 'Архангельск')
    if departure_id is None:
        print(
            'Tourvisor API: '
            f'departures_http={departures_status} origin=unresolved '
            'countries=skipped destination=skipped'
        )
        raise SystemExit(0)

    countries_status, countries = get(
        'countries', {'departureId': departure_id}
    )
    country_id = _find_named_id(countries, 'Таиланд')
    print(
        'Tourvisor API: '
        f'departures_http={departures_status} origin=resolved '
        f'countries_http={countries_status} '
        f'destination={"resolved" if country_id is not None else "unresolved"}'
    )
except requests.HTTPError as exc:
    status = exc.response.status_code if exc.response is not None else 'unknown'
    print(f'Tourvisor API: http_error={status}')
except Exception as exc:
    # Read-only probe. Never print the token, headers, URL query, or response body.
    print(f'Tourvisor API: probe_error={type(exc).__name__}')
PY
}

mapfile -t config < <(read_config)
miniapp_enabled=${config[0]:-0}
host=${config[1]:-}

if [[ "$miniapp_enabled" != "1" ]]; then
  echo "VK Mini App smoke check skipped: VK_MINI_APP_ID is not configured"
  exit 0
fi

if [[ ! -f "$repo/vk_bot.py" ]] || ! grep -q '^def _process_app_payload' "$repo/vk_bot.py"; then
  echo "VK Mini App app_payload handler is missing from deployed vk_bot.py" >&2
  exit 1
fi

echo "VK Mini App app_payload handler present"

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
inspect_travelata
inspect_tourvisor
