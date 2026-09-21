#!/usr/bin/env bash
# Root-owned entrypoint invoked through a forced-command SSH deploy key.
set -Eeuo pipefail

repo=/opt/turbot
branch=main
venv="$repo/venv/bin"

ensure_runtime_permissions() {
  if [[ ! -d "$repo" ]]; then
    echo "TurBot runtime directory is missing: $repo" >&2
    return 1
  fi

  # The services run as the unprivileged turbot user. Repair the app
  # directory and only the live SQLite state files/sidecars. Do not recursively
  # chown the tree: backups intentionally have a separate root-owned policy.
  chown turbot:turbot "$repo"
  chmod 0750 "$repo"

  local state_file
  for state_file in "$repo/bot_state.sqlite" "$repo/bot_state.sqlite-wal" "$repo/bot_state.sqlite-shm" "$repo/vk_bot_state.sqlite" "$repo/vk_bot_state.sqlite-wal" "$repo/vk_bot_state.sqlite-shm"; do
    python3 - "$state_file" <<'PY'
import errno
import grp
import os
import pwd
import stat
import sys

path = sys.argv[1]
if not hasattr(os, "O_NOFOLLOW"):
    raise SystemExit("Refusing state repair without O_NOFOLLOW support")

flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
except FileNotFoundError:
    raise SystemExit(0)
except OSError as exc:
    if exc.errno == errno.ELOOP:
        raise SystemExit(f"Refusing symlinked SQLite state path: {path}")
    raise

try:
    mode = os.fstat(fd).st_mode
    if not stat.S_ISREG(mode):
        raise SystemExit(f"Refusing non-regular SQLite state path: {path}")
    user = pwd.getpwnam("turbot")
    group = grp.getgrnam("turbot")
    os.fchown(fd, user.pw_uid, group.gr_gid)
    os.fchmod(fd, 0o600)
finally:
    os.close(fd)
PY
  done
}

install_systemd_units() {
  local main_unit="$repo/deploy/turbot.service"
  local vk_unit="$repo/deploy/vk-turbot.service"

  if [[ ! -f "$main_unit" || ! -f "$vk_unit" ]]; then
    echo "TurBot systemd unit files are missing from the deployed revision" >&2
    return 1
  fi

  install -o root -g root -m 0644 "$main_unit" /etc/systemd/system/turbot.service
  install -o root -g root -m 0644 "$vk_unit" /etc/systemd/system/vk-turbot.service
  systemctl daemon-reload
}


# Run before cd so the forced-command deploy entrypoint can recover even when
# a bad ownership/mode change made WorkingDirectory inaccessible to systemd.
ensure_runtime_permissions
cd "$repo"

deploy_bundle() {
  local target_sha bundle stage old_manifest backup
  local -a metadata=()
  IFS= read -r target_sha || {
    echo "Missing bundle commit SHA" >&2
    return 1
  }
  if [[ ! "$target_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid bundle commit SHA" >&2
    return 1
  fi

  bundle="$(mktemp)"
  stage="$(mktemp -d)"
  backup="$(mktemp)"
  trap 'rm -f "$bundle" "$backup"; rm -rf "$stage"' RETURN

  cat > "$bundle"
  tar -tzf "$bundle" >/dev/null
  tar -xzf "$bundle" -C "$stage"
  if [[ ! -f "$stage/.deploy-manifest" ]]; then
    echo "Deploy bundle manifest missing" >&2
    return 1
  fi

  "$venv/python" - "$stage/.deploy-manifest" <<'PY'
from pathlib import Path
import sys

manifest = Path(sys.argv[1])
for raw in manifest.read_text(encoding="utf-8").splitlines():
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"Unsafe deploy path: {raw!r}")
PY

  old_manifest="$(mktemp)"
  if [[ -f "$repo/.deploy-manifest" ]]; then
    cp "$repo/.deploy-manifest" "$old_manifest"
  else
    git -C "$repo" ls-files > "$old_manifest"
  fi

  # Restore revision and manifest together with the previous application files.
  for name in .deploy-manifest .deployed-commit; do
    [[ ! -f "$repo/$name" ]] || metadata+=("$name")
  done
  tar -czf "$backup" -C "$repo" --ignore-failed-read -T "$old_manifest" "${metadata[@]}"

  "$venv/python" - "$repo" "$old_manifest" <<'PY'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
manifest = Path(sys.argv[2])
protected = {".env", ".deploy-manifest", ".deployed-commit"}
for raw in manifest.read_text(encoding="utf-8").splitlines():
    if not raw or raw in protected:
        continue
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        continue
    path = root / rel
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
    except FileNotFoundError:
        pass
PY

  cp -a "$stage/." "$repo/"
  printf '%s\n' "$target_sha" > "$repo/.deployed-commit"
  chown -R turbot:turbot "$repo"
  chown root:root "$repo/deploy/turbot-deploy.sh" 2>/dev/null || true
  chmod 755 "$repo/deploy/turbot-deploy.sh" 2>/dev/null || true

  rollback_bundle() {
    echo "Bundle deployment failed; restoring previous files" >&2
    "$venv/python" - "$repo" "$repo/.deploy-manifest" <<'PY'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
manifest = Path(sys.argv[2])
if manifest.exists():
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        if not raw:
            continue
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        path = root / rel
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path)
        except FileNotFoundError:
            pass
PY
    rm -f "$repo/.deployed-commit" "$repo/.deploy-manifest"
    tar -xzf "$backup" -C "$repo"
    install_systemd_units 2>/dev/null || true
    systemctl restart turbot 2>/dev/null || true
    systemctl restart vk-turbot 2>/dev/null || true
  }
  trap rollback_bundle ERR

  ensure_backup_and_restore_drill
  "$venv/pip" install --requirement "$repo/requirements.txt"
  install_systemd_units
  systemctl restart turbot
  systemctl restart vk-turbot
  source_deployer="$(readlink -f "$repo/deploy/turbot-deploy.sh")"
  installed_deployer="$(readlink -f /usr/local/sbin/turbot-deploy 2>/dev/null || true)"
  if [[ "$source_deployer" != "$installed_deployer" ]]; then
    cp "$repo/deploy/turbot-deploy.sh" /usr/local/sbin/turbot-deploy
  fi
  chmod 755 /usr/local/sbin/turbot-deploy

  for _ in {1..12}; do
    if curl --fail --silent --max-time 5 http://127.0.0.1:8000/health >/dev/null \
      && curl --fail --silent --max-time 5 http://127.0.0.1:5100/vk/health >/dev/null; then
      chmod +x "$repo/deploy/verify-vk-miniapp.sh"
      "$repo/deploy/verify-vk-miniapp.sh"
      trap - ERR
      rm -f "$old_manifest"
      echo "TurBot bundle deployed: $target_sha"
      return 0
    fi
    sleep 2
  done

  echo "TurBot bundle did not become healthy" >&2
  return 1
}

ensure_backup_and_restore_drill() {
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "Production backup safety requires sqlite3" >&2
    return 1
  fi

  chmod 755 "$repo/scripts/backup.sh" "$repo/scripts/restore-drill.sh" "$repo/scripts/offsite-backup.sh"

  cat > /etc/cron.d/turbot-backup <<CRON
# TurBot — nightly verified SQLite online backup.
0 3 * * * root $repo/scripts/backup.sh >> /var/log/turbot-backup.log 2>&1
CRON
  chmod 644 /etc/cron.d/turbot-backup

  if ! systemctl is-active --quiet cron; then
    systemctl enable --now cron
  fi

  APP_DIR="$repo" BACKUP_DIR="$repo/backups" KEEP_DAYS=7 \
    "$repo/scripts/backup.sh"
  APP_DIR="$repo" BACKUP_DIR="$repo/backups" MAX_BACKUP_AGE_SECONDS=86400 \
    "$repo/scripts/restore-drill.sh"

  echo "Production backup schedule and isolated restore drill: ok"
}


apply_stdin_config() {
  local marker payload

  # Normal deploy connections have empty stdin.
  if ! IFS= read -r marker; then
    return 0
  fi

  if [[ "$marker" == "TURBOT_DEPLOY_BUNDLE_V1" ]]; then
    deploy_bundle
    exit $?
  fi

  if [[ "$marker" == "TURBOT_BACKUP_DRILL_V1" ]]; then
    if IFS= read -r _unexpected; then
      echo "Unexpected backup drill marker payload" >&2
      return 1
    fi
    ensure_backup_and_restore_drill
    CONFIG_APPLIED=1
    return 0
  fi

  if [[ "$marker" == "TURBOT_AI_LEAD_ASSIST_CONFIG_V1" ]]; then
    local desired
    if ! IFS= read -r desired; then
      echo "Missing AI lead assist desired state" >&2
      return 1
    fi
    if IFS= read -r _unexpected; then
      echo "Unexpected AI lead assist payload" >&2
      return 1
    fi
    if [[ "$desired" != "true" && "$desired" != "false" ]]; then
      echo "AI lead assist desired state must be true or false" >&2
      return 1
    fi

    "$venv/python" - "$desired" <<'PY'
import os
import sys
from pathlib import Path

desired = sys.argv[1]
env_path = Path("/opt/turbot/.env")
if not env_path.exists():
    raise SystemExit("Server .env not found")

key = "AI_LEAD_ASSIST_ENABLED"
src = env_path.read_text(encoding="utf-8").splitlines()
out = []
written = False
for line in src:
    if line.startswith(key + "="):
        if not written:
            out.append(f"{key}={desired}")
            written = True
        continue
    out.append(line)
if not written:
    out.append(f"{key}={desired}")

tmp = env_path.with_name(".env.tmp")
tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, env_path)
PY

    chown turbot:turbot /opt/turbot/.env
    chmod 600 /opt/turbot/.env
    systemctl restart turbot

    for _ in {1..12}; do
      if "$venv/python" - "$desired" <<'PY'
import sys

import requests

desired = sys.argv[1] == "true"
response = requests.get("http://127.0.0.1:8000/health", timeout=3)
response.raise_for_status()
ai = response.json().get("ai_selection") or {}
if bool(ai.get("lead_assist_enabled")) != desired:
    raise SystemExit(1)
if desired and not bool(ai.get("ready")):
    raise SystemExit(1)
PY
      then
        echo "Telegram AI lead assist configured: $desired"
        CONFIG_APPLIED=1
        return 0
      fi
      sleep 1
    done

    echo "Telegram AI lead assist health verification failed" >&2
    systemctl status turbot --no-pager -l || true
    journalctl -u turbot -n 80 --no-pager || true
    return 1
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

  if [[ "$marker" != "TURBOT_DEPLOY_CONFIG_V1" \
        && "$marker" != "TURBOT_DEPLOY_CONFIG_V2" \
        && "$marker" != "TURBOT_DEPLOY_CONFIG_V3" \
        && "$marker" != "TURBOT_DEPLOY_CONFIG_V4" \
        && "$marker" != "TURBOT_DEPLOY_CONFIG_V5" ]]; then
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

markers = {
    "TURBOT_DEPLOY_CONFIG_V1": 3,
    "TURBOT_DEPLOY_CONFIG_V2": 4,
    "TURBOT_DEPLOY_CONFIG_V3": 6,
    "TURBOT_DEPLOY_CONFIG_V4": 7,
    "TURBOT_DEPLOY_CONFIG_V5": 9,
}
if not lines or lines[0] not in markers:
    raise SystemExit("Invalid deploy payload")

marker = lines[0]
if len(lines) != markers[marker]:
    raise SystemExit("Invalid deploy payload")


def decode(index: int) -> str:
    try:
        return base64.b64decode(lines[index], validate=True).decode("utf-8")
    except Exception:
        raise SystemExit("Invalid encoded deploy payload")


app_id = decode(1)
secret = decode(2)
mdt_api_key = decode(3) if marker in {"TURBOT_DEPLOY_CONFIG_V2", "TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} else ""
travelata_username = decode(4) if marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} else ""
travelata_password = decode(5) if marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} else ""
travelpayouts_api_token = decode(6) if marker in {"TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} else ""
sletat_login = decode(7) if marker == "TURBOT_DEPLOY_CONFIG_V5" else ""
sletat_password = decode(8) if marker == "TURBOT_DEPLOY_CONFIG_V5" else ""

if not app_id.isdigit():
    raise SystemExit("Invalid VK Mini App ID")
if not secret or "\n" in secret or "\r" in secret:
    raise SystemExit("Invalid VK Mini App secret")
if marker in {"TURBOT_DEPLOY_CONFIG_V2", "TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} and (
    not mdt_api_key or "\n" in mdt_api_key or "\r" in mdt_api_key
):
    raise SystemExit("Invalid MDT API key")

if marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"}:
    has_user = bool(travelata_username)
    has_password = bool(travelata_password)
    if has_user != has_password:
        raise SystemExit("Travelata credentials must be supplied as a complete pair")
    if any("\n" in value or "\r" in value for value in (travelata_username, travelata_password)):
        raise SystemExit("Invalid Travelata credentials")

if travelpayouts_api_token and ("\n" in travelpayouts_api_token or "\r" in travelpayouts_api_token):
    raise SystemExit("Invalid Travelpayouts API token")

if marker == "TURBOT_DEPLOY_CONFIG_V5":
    has_sletat_login = bool(sletat_login)
    has_sletat_password = bool(sletat_password)
    if has_sletat_login != has_sletat_password:
        raise SystemExit("Sletat credentials must be supplied as a complete pair")
    if any("\n" in value or "\r" in value for value in (sletat_login, sletat_password)):
        raise SystemExit("Invalid Sletat credentials")

env_path = Path("/opt/turbot/.env")
if not env_path.exists():
    raise SystemExit("Server .env not found")


def quote_env(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


values = {
    "VK_MINI_APP_ID": app_id,
    "VK_MINI_APP_SECRET": quote_env(secret),
}
if marker in {"TURBOT_DEPLOY_CONFIG_V2", "TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"}:
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

# V3/V4 treat an empty Travelata pair as "not supplied", never as "erase the
# production credentials". This lets normal deploys run before API approval
# and protects manually installed credentials if GitHub secrets are absent.
travelata_supplied = bool(travelata_username and travelata_password)
if marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} and travelata_supplied:
    values.update(
        {
            "TRAVELATA_USERNAME": quote_env(travelata_username),
            "TRAVELATA_PASSWORD": quote_env(travelata_password),
            "VK_TRAVELATA_ENABLED": "true",
            "TOUR_PROVIDER_ORDER": quote_env("travelata,tourvisor"),
        }
    )

# Same preservation rule for Travelpayouts. The token is optional so code can
# deploy before Booking.com access is approved; an empty GitHub secret never
# deletes a token installed directly on the production host.
travelpayouts_supplied = bool(travelpayouts_api_token)
if marker in {"TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} and travelpayouts_supplied:
    values["TRAVELPAYOUTS_API_TOKEN"] = quote_env(travelpayouts_api_token)

# V5 adds Sletat as another optional complete credential pair. Empty GitHub
# secrets preserve existing server values so a routine deploy can never erase
# a manually installed or previously approved provider credential.
sletat_supplied = bool(sletat_login and sletat_password)
if marker == "TURBOT_DEPLOY_CONFIG_V5" and sletat_supplied:
    values.update(
        {
            "SLETAT_LOGIN": quote_env(sletat_login),
            "SLETAT_PASSWORD": quote_env(sletat_password),
            "VK_SLETAT_ENABLED": "true",
            "TOUR_PROVIDER_ORDER": quote_env("sletat,travelata,tourvisor"),
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
if marker in {"TURBOT_DEPLOY_CONFIG_V2", "TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"}:
    print("MDT production configuration installed")
if marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"}:
    if travelata_supplied:
        print("Travelata production configuration installed")
    else:
        print("Travelata deploy credentials not supplied; existing server values preserved")
if marker in {"TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"}:
    if travelpayouts_supplied:
        print("Travelpayouts production configuration installed")
    else:
        print("Travelpayouts deploy token not supplied; existing server value preserved")
if marker == "TURBOT_DEPLOY_CONFIG_V5":
    if sletat_supplied:
        print("Sletat production configuration installed")
    else:
        print("Sletat deploy credentials not supplied; existing server values preserved")
PY
  then
    rm -f "$payload"
    return 1
  fi

  rm -f "$payload"

  chown turbot:turbot /opt/turbot/.env
  chmod 600 /opt/turbot/.env

  # Keep the live unit in sync with the checked-out repository. This matters
  # when the WSGI entrypoint changes; merely daemon-reloading an old unit does
  # not update ExecStart, a delightful little systemd trap.
  install_systemd_units
  systemctl restart turbot
  systemctl restart vk-turbot

  for _ in {1..10}; do
    if curl --fail --silent --max-time 3 http://127.0.0.1:8000/health >/dev/null \
      && curl --fail --silent --max-time 3 http://127.0.0.1:5100/vk/health >/dev/null; then
      echo "TurBot and VK Mini App services healthy"
      CONFIG_APPLIED=1
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

CONFIG_APPLIED=0
apply_stdin_config
if [[ "$CONFIG_APPLIED" == "1" ]]; then
  exit 0
fi

previous=$(git rev-parse HEAD)
git fetch --depth=1 origin "$branch"
target=$(git rev-parse "origin/$branch")

if [[ "$target" == "$previous" && ! -f "$repo/.deployed-commit" ]]; then
  chmod +x "$repo/deploy/verify-vk-miniapp.sh"
  "$repo/deploy/verify-vk-miniapp.sh"
  echo "TurBot already runs $target"
  exit 0
fi

rollback() {
  echo "Deployment failed; restoring $previous" >&2
  git reset --hard "$previous"
  rm -f "$repo/.deployed-commit" "$repo/.deploy-manifest"
  "$venv/pip" install --requirement requirements.txt
  install_systemd_units 2>/dev/null || true
  systemctl restart turbot
  systemctl restart vk-turbot 2>/dev/null || true
}
trap rollback ERR

rollback_and_fail() {
  # Explicit exit does not trigger ERR. Disable the trap before an explicit
  # best-effort rollback so a rollback command cannot recurse into the trap.
  trap - ERR
  rollback || true
  exit 1
}

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
rm -f "$repo/.deployed-commit" "$repo/.deploy-manifest"
"$venv/pip" install --requirement requirements.txt
install_systemd_units
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
    if "$repo/deploy/verify-vk-miniapp.sh"; then
      print_telegram_username
      git rev-parse HEAD
      exit 0
    fi

    echo "TurBot VK verification failed; collecting bounded diagnostics" >&2
    systemctl status vk-turbot --no-pager -l || true
    journalctl -u vk-turbot -n 120 --no-pager || true
    rollback_and_fail
  fi
  sleep 2
done

echo "TurBot did not become healthy" >&2
systemctl status turbot --no-pager -l || true
journalctl -u turbot -n 80 --no-pager || true
rollback_and_fail
