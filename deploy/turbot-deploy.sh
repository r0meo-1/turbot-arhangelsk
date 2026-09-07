#!/usr/bin/env bash
# Root-owned entrypoint invoked through a forced-command SSH deploy key.
set -Eeuo pipefail

repo=/opt/turbot
branch=main
venv="$repo/venv/bin"

cd "$repo"
previous=$(git rev-parse HEAD)
git fetch --depth=1 origin "$branch"
target=$(git rev-parse "origin/$branch")

if [[ "$target" == "$previous" ]]; then
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
    print_telegram_username
    git rev-parse HEAD
    exit 0
  fi
  sleep 2
done

echo "TurBot did not become healthy" >&2
exit 1
