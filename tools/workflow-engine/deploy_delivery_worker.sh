#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${WORKFLOW_APP_DIR:-/opt/workflow-engine}"
REF="${WORKFLOW_SOURCE_REF:-main}"
REPO="${WORKFLOW_SOURCE_REPO:-r0meo-1/turbot-arhangelsk}"
SOURCE="https://raw.githubusercontent.com/${REPO}/${REF}/tools/workflow-engine/delivery_worker.py"
TARGET="${APP_DIR}/workflow_engine/delivery_worker.py"

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: run as root" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

curl -fsSL "${SOURCE}" -o "${tmp}"

"${APP_DIR}/.venv/bin/python" -m py_compile "${tmp}"

backup="${TARGET}.$(date +%Y%m%d-%H%M%S).bak"
cp "${TARGET}" "${backup}"

install -o root -g root -m 0644 "${tmp}" "${TARGET}"

systemctl restart workflow-delivery
sleep 3

systemctl is-active workflow-delivery

echo "delivery worker deployed"
echo "source_ref=${REF}"
echo "backup=${backup}"
