#!/usr/bin/env bash
# Notice a bot that went deaf while still looking alive.
#
# Twice this bot stopped answering while every indicator stayed green: systemd
# printed active (running), /health returned 200, the log was silent, and the
# outage was found by a client instead of by us. Restarting fixed it both
# times. So: poll the one signal that means something — how long since
# Telegram last answered getUpdates, which /health now reports with a status
# code — and restart when it goes stale.
#
# A cooldown keeps a genuine Telegram outage from turning into a restart loop:
# if restarting did not help, restarting again every two minutes will not
# either, and the alert is the useful part.
#
#   ./watchdog.sh turbot        # Telegram bot on 127.0.0.1:8000
#   ./watchdog.sh vk-turbot     # VK bot on 127.0.0.1:5100
set -uo pipefail

UNIT="${1:-turbot}"
case "$UNIT" in
  turbot)     PORT=8000 ;;
  vk-turbot)  PORT=5100 ;;
  *) echo "unknown unit: $UNIT (expected turbot or vk-turbot)" >&2; exit 2 ;;
esac

# Overridable so the script can be exercised against a fake health server
# instead of being first run for real during an outage.
URL="${WATCHDOG_URL:-http://127.0.0.1:${PORT}/health}"
ENV_FILE="${WATCHDOG_ENV_FILE:-/opt/turbot/.env}"
STATE_DIR="${WATCHDOG_STATE_DIR:-/var/lib/turbot-watchdog}"
STAMP="${STATE_DIR}/${UNIT}.last-restart"
COOLDOWN="${WATCHDOG_COOLDOWN:-600}"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

mkdir -p "$STATE_DIR"

env_get() {
  # Deliberately not `source`. An unquoted value in this same .env once turned
  # a config read into command execution; take the literal text after the
  # first '=' and strip one layer of quotes, nothing more.
  sed -n "s/^${1}=//p" "$ENV_FILE" 2>/dev/null | head -n1 \
    | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

notify() {
  local token chat
  token="$(env_get BOT_TOKEN)"
  chat="$(env_get ADMIN_ID)"
  [ -n "$token" ] && [ -n "$chat" ] || return 0
  # -K - reads the request from stdin so the token never lands in `ps`.
  curl -sS --max-time 10 -o /dev/null -K - <<CURLRC || true
url = "https://api.telegram.org/bot${token}/sendMessage"
data-urlencode = "chat_id=${chat}"
data-urlencode = "text=${1}"
CURLRC
}

# curl already prints 000 for a connection that never happened, so do not add
# another one on failure — that produced a memorable "HTTP 000000" the first
# time this script was run for real.
code="$(curl -sS -o "$BODY" -w '%{http_code}' --max-time 20 "$URL" 2>/dev/null)"
code="${code:-000}"

if [ "$code" = "200" ]; then
  exit 0
fi

if [ "$code" = "000" ]; then
  reason="no answer from ${URL}"
else
  # Pull the age out without a jq dependency; it is the number worth reporting.
  age="$(grep -o '"seconds_since_poll_ok":[^,}]*' "$BODY" 2>/dev/null \
         | head -n1 | cut -d: -f2 | tr -cd '0-9.')"
  reason="HTTP ${code}, silent for ${age:-?}s"
fi

echo "$(date -Is) ${UNIT} unhealthy: ${reason}"

now="$(date +%s)"
last=0
[ -f "$STAMP" ] && last="$(cat "$STAMP" 2>/dev/null || echo 0)"
if [ $(( now - last )) -lt "$COOLDOWN" ]; then
  echo "restarted $(( now - last ))s ago — inside the ${COOLDOWN}s cooldown, not restarting again"
  exit 0
fi

echo "$now" > "$STAMP"
notify "Watchdog: ${UNIT} is not answering (${reason}). Restarting."
systemctl restart "$UNIT"
sleep 15

code_after="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$URL" 2>/dev/null)"
code_after="${code_after:-000}"
if [ "$code_after" = "200" ]; then
  echo "restart recovered ${UNIT}"
  notify "Watchdog: ${UNIT} recovered after restart."
else
  echo "restart did NOT recover ${UNIT} (HTTP ${code_after})"
  notify "Watchdog: ${UNIT} still down after restart (HTTP ${code_after}). Needs a look."
fi
