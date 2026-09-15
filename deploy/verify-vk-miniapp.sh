#!/usr/bin/env bash
set -Eeuo pipefail

repo=${TURBOT_ROOT:-/opt/turbot}
venv=${TURBOT_VENV:-$repo/venv/bin}
env_file=${TURBOT_ENV_FILE:-$repo/.env}

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
