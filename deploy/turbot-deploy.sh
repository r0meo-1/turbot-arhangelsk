#!/usr/bin/env bash
# Root-owned entrypoint invoked through a forced-command SSH deploy key.
set -Eeuo pipefail

repo=/opt/turbot
branch=main
venv="$repo/venv/bin"

cd "$repo"

apply_stdin_config() {
  local marker payload

  # Normal deploy connections have empty stdin.
  if ! IFS= read -r marker; then
    return 0
  fi

  if [[ "$marker" == "TURBOT_VK_CALLBACK_APP_PAYLOAD_V1" ]]; then
    # This marker performs one narrowly scoped VK API mutation. It accepts no
    # payload data and uses only the protected production .env on the server.
    if IFS= read -r _unexpected; then
      echo "Unexpected VK callback marker payload" >&2
      return 1
    fi
    if ! grep -q 'def _process_app_payload' "$repo/vk_bot.py"; then
      echo "VK app_payload handler is not deployed" >&2
      return 1
    fi
    "$venv/python" "$repo/deploy/vk-enable-app-payload.py"
    return $?
  fi

  if [[ "$marker" != "TURBOT_DEPLOY_CONFIG_V1" && "$marker" != "TURBOT_DEPLOY_CONFIG_V2" ]]; then
    echo "Unsupported deploy payload" >&2
    return 1
  fi

  payload=$(mktemp)
  chmod 600 "$payload"

  {
    printf '%s\n' "$marker"
    cat
  } > "$payload"

  if ! "$venv/python" - "$payload" <<'PY'
import base64
import os
import sys
from pathlib import Path

payload_path = Path(sys.argv[1])
lines = payload_path.read_text(encoding="utf-8").splitlines()

if not lines or lines[0] not in {"TURBOT_DEPLOY_CONFIG_V1", "TURBOT_DEPLOY_CONFIG_V2"}:
    raise SystemExit("Invalid deploy payload")

marker = lines[0]
expected_lines = 3 if marker == "TURBOT_DEPLOY_CONFIG_V1" else 4
if len(lines) != expected_lines:
    raise SystemExit("Invalid deploy payload")

try:
    app_id = base64.b64decode(lines[1], validate=True).decode("utf-8")
    secret = base64.b64decode(lines[2], validate=True).decode("utf-8")
    mdt_api_key = (
        base64.b64decode(lines[3], validate=True).decode("utf-8")
        if marker == "TURBOT_DEPLOY_CONFIG_V2"
        else ""
    )
except Exception:
    raise SystemExit("Invalid encoded deploy payload")

if not app_id.isdigit():
    raise SystemExit("Invalid VK Mini App ID")
if not secret or "\n" in secret or "\r" in secret:
    raise SystemExit("Invalid VK Mini App secret")
if marker == "TURBOT_DEPLOY_CONFIG_V2" and (
    not mdt_api_key or "\n" in mdt_api_key or "\r" in mdt_api_key
):
    raise SystemExit("Invalid MDT API key")

env_path = Path("/opt/turbot/.env")
if not env_path.exists():
    raise SystemExit("Server .env not found")

def quote_env(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

values = {
    "VK_MINI_APP_ID": app_id,
    "VK_MINI_APP_SECRET": quote_env(secret),
}
if marker == "TURBOT_DEPLOY_CONFIG_V2":
    values.update(
        {
            "MDT_API_KEY": quote_env(mdt_api_key),
            "MDT_ENABLED": "true",
            "MDT_MODE": "lead",
            "VK_MDT_MODE": "preorder",
            "MDT_ACCOUNT": quote_env("apreltour"),
            "MDT_BASE_URL": quote_env("https://apreltour.moidokumenti.ru"),
        }
    )

src = env_path.read_text(encoding="utf-8").splitlines()
out = []
written = set()

for line in src:
    matched = False
    for key, value in values.items():
        if line.startswith(key + "="):
            if key not in written:
                out.append(f"{key}={value}")
                written.add(key)
            matched = True
            break
    if not matched:
        out.append(line)

for key, value in values.items():
    if key not in written:
        out.append(f"{key}={value}")

tmp = env_path.with_name(".env.tmp")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, env_path)

print("VK Mini App configuration installed")
if marker == "TURBOT_DEPLOY_CONFIG_V2":
    print("MDT production configuration installed")
PY
  then
    rm -f "$payload"
    return 1
  fi

  rm -f "$payload"

  chown turbot:turbot /opt/turbot/.env
  chmod 600 /opt/turbot/.env

  systemctl restart turbot
  systemctl restart vk-turbot

  for _ in {1..10}; do
    if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null \
      && curl --fail --silent --max-time 3 http://127.0.0.1:5100/vk/health >/dev/null; then
      echo "TurBot and VK Mini App services healthy"
      return 0
    fi
    sleep 1
  done

  echo "TurBot services did not become healthy after config update" >&2

  systemctl status turbot --no-pager -l || true
  systemctl status vk-turbot --no-pager -l || true
  journalctl -u turbot -n 80 --no-pager || true
  journalctl -u vk-turbot -n 80 --no-pager || true
  return 1
}

apply_stdin_config
previous=$(git rev-parse HEAD)
git fetch --depth=1 origin "$branch"
target=$(git rev-parse "origin/$branch")

if [[ "$target" == "$previous" ]]; then
  chmod +x "$repo/deploy/verify-vk-miniapp.sh"
  "$repo/deploy/verify-vk-miniapp.sh"
  echo "TurBot already runs $target"
  exit 0
fi

rollback() {
  echo "Deployment failed; restoring $previous" >&2
  git reset --hard "$previous"
  "$venv/pip" install --requirement requirements.txt
  systemctl restart turbot
  systemctl restart vk-turbot 2>/dev/null || true
}
trap rollback ERR

print_telegram_username() {
  # Resolve only the public @username via Telegram getMe. The BOT_TOKEN stays
  # inside the Python process and is never printed or placed in a shell argv.
  "$venv/python" - <<'PY' || true
import os

import requests
from dotenv import load_dotenv

load_dotenv('/opt/turbot/.env')
token = os.getenv('BOT_TOKEN', '').strip()
if not token:
    raise SystemExit(0)
try:
    response = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
    data = response.json()
    username = (data.get('result') or {}).get('username')
    if data.get('ok') and username:
        print(f'Telegram bot: @{username}')
except Exception:
    pass
PY
}

git reset --hard "$target"
"$venv/pip" install --requirement requirements.txt
systemctl daemon-reload 2>/dev/null || true
systemctl restart turbot 2>/dev/null || true
systemctl restart vk-turbot 2>/dev/null || true
systemctl restart turbot-vk 2>/dev/null || true
systemctl restart vk_turbot 2>/dev/null || true
pkill -f "vk_bot" 2>/dev/null || true
cp "$repo/deploy/turbot-deploy.sh" /usr/local/sbin/turbot-deploy 2>/dev/null || true
chmod 755 /usr/local/sbin/turbot-deploy 2>/dev/null || true
cp "$repo/deploy/turbot-deploy.sh" /root/turbot-deploy.sh 2>/dev/null || true

for _ in {1..12}; do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health >/dev/null; then
    chmod +x "$repo/deploy/verify-vk-miniapp.sh"
    "$repo/deploy/verify-vk-miniapp.sh"
    print_telegram_username
    git rev-parse HEAD
    exit 0
  fi
  sleep 2
done

echo "TurBot did not become healthy" >&2
exit 1
