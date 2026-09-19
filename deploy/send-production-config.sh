#!/usr/bin/env bash
set -Eeuo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${VK_MINI_APP_ID:?VK_MINI_APP_ID is required}"
: "${VK_MINI_APP_SECRET:?VK_MINI_APP_SECRET is required}"
: "${MDT_API_KEY:?MDT_API_KEY is required}"
: "${TRAVELPAYOUTS_API_TOKEN:?TRAVELPAYOUTS_API_TOKEN is required}"

TRAVELATA_USERNAME="${TRAVELATA_USERNAME:-}"
TRAVELATA_PASSWORD="${TRAVELATA_PASSWORD:-}"
SLETAT_LOGIN="${SLETAT_LOGIN:-}"
SLETAT_PASSWORD="${SLETAT_PASSWORD:-}"

if [[ -n "$TRAVELATA_USERNAME" || -n "$TRAVELATA_PASSWORD" ]]; then
  if [[ -z "$TRAVELATA_USERNAME" || -z "$TRAVELATA_PASSWORD" ]]; then
    echo "Travelata credentials must be configured as a complete pair" >&2
    exit 1
  fi
fi

if [[ -n "$SLETAT_LOGIN" || -n "$SLETAT_PASSWORD" ]]; then
  if [[ -z "$SLETAT_LOGIN" || -z "$SLETAT_PASSWORD" ]]; then
    echo "Sletat credentials must be configured as a complete pair" >&2
    exit 1
  fi
fi

b64() {
  printf '%s' "$1" | base64 -w0
}

app_id_b64="$(b64 "$VK_MINI_APP_ID")"
secret_b64="$(b64 "$VK_MINI_APP_SECRET")"
mdt_key_b64="$(b64 "$MDT_API_KEY")"
travelata_user_b64="$(b64 "$TRAVELATA_USERNAME")"
travelata_password_b64="$(b64 "$TRAVELATA_PASSWORD")"
travelpayouts_token_b64="$(b64 "$TRAVELPAYOUTS_API_TOKEN")"
sletat_login_b64="$(b64 "$SLETAT_LOGIN")"
sletat_password_b64="$(b64 "$SLETAT_PASSWORD")"

send_payload() {
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    "root@$DEPLOY_HOST" true
}

# First use the oldest configuration marker so an older production deploy
# entrypoint can refresh the required baseline configuration safely.
printf 'TURBOT_DEPLOY_CONFIG_V1\n%s\n%s\n' \
  "$app_id_b64" \
  "$secret_b64" |
  send_payload

# Bootstrap new deploy protocol versions without a circular dependency.
# If Sletat is not configured yet, V4 contains everything needed today and is
# understood by the previous production entrypoint. The following bundle
# deploy then installs V5 support. Once a complete Sletat pair exists, V5 is
# required and intentionally fails closed on an unexpectedly old server.
if [[ -n "$SLETAT_LOGIN" && -n "$SLETAT_PASSWORD" ]]; then
  printf 'TURBOT_DEPLOY_CONFIG_V5\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n' \
    "$app_id_b64" \
    "$secret_b64" \
    "$mdt_key_b64" \
    "$travelata_user_b64" \
    "$travelata_password_b64" \
    "$travelpayouts_token_b64" \
    "$sletat_login_b64" \
    "$sletat_password_b64" |
    send_payload
else
  printf 'TURBOT_DEPLOY_CONFIG_V4\n%s\n%s\n%s\n%s\n%s\n%s\n' \
    "$app_id_b64" \
    "$secret_b64" \
    "$mdt_key_b64" \
    "$travelata_user_b64" \
    "$travelata_password_b64" \
    "$travelpayouts_token_b64" |
    send_payload
fi
