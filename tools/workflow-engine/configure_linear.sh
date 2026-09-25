#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${WORKFLOW_ENV_FILE:-/etc/workflow-engine/env}"
TEAM_ID="${LINEAR_TEAM_ID:-46763101-7eb7-456f-b21b-7c284d240f3e}"
MODE="${LINEAR_MODE:-dry_run}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root" >&2
  exit 1
fi

if ! getent group workflow >/dev/null; then
  echo "ERROR: workflow group does not exist" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found" >&2
  exit 1
fi

case "${MODE}" in
  dry_run|live) ;;
  *)
    echo "ERROR: LINEAR_MODE must be dry_run or live" >&2
    exit 1
    ;;
esac

if [[ -z "${LINEAR_API_KEY:-}" ]]; then
  read -r -s -p "Linear API key: " LINEAR_API_KEY
  echo
fi

if [[ -z "${LINEAR_API_KEY}" ]]; then
  echo "ERROR: empty Linear API key" >&2
  exit 1
fi

tmp="$(mktemp)"
new_tmp="$(mktemp)"
trap 'rm -f "${tmp}" "${new_tmp}"; unset LINEAR_API_KEY' EXIT

grep -vE '^(LINEAR_API_KEY|LINEAR_TEAM_ID|LINEAR_MODE)=' "${ENV_FILE}" > "${tmp}" || true

{
  cat "${tmp}"
  printf '\nLINEAR_API_KEY=%s\n' "${LINEAR_API_KEY}"
  printf 'LINEAR_TEAM_ID=%s\n' "${TEAM_ID}"
  printf 'LINEAR_MODE=%s\n' "${MODE}"
} > "${new_tmp}"

install -o root -g workflow -m 0640 "${new_tmp}" "${ENV_FILE}"

systemctl restart workflow-delivery
sleep 2

echo "workflow-delivery: $(systemctl is-active workflow-delivery)"
echo "LINEAR_API_KEY=present"
echo "LINEAR_TEAM_ID=${TEAM_ID}"
echo "LINEAR_MODE=${MODE}"
