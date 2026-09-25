#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${WORKFLOW_APP_DIR:-/opt/workflow-engine}"
DB="${WORKFLOW_DB:-/var/lib/workflow-engine/workflow.db}"
ENV_FILE="${WORKFLOW_ENV_FILE:-/etc/workflow-engine/env}"

echo "=== SERVICES ==="
printf "workflow-engine: "
systemctl is-active workflow-engine
printf "workflow-delivery: "
systemctl is-active workflow-delivery

echo
echo "=== CONFIG ==="
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

[[ -n "${LINEAR_API_KEY:-}" ]] && echo "LINEAR_API_KEY=present" || echo "LINEAR_API_KEY=missing"
echo "LINEAR_TEAM_ID=${LINEAR_TEAM_ID:-missing}"
echo "LINEAR_MODE=${LINEAR_MODE:-missing}"

echo
echo "=== ENGINE STATUS ==="
cd "${APP_DIR}"
"${APP_DIR}/.venv/bin/python" -m workflow_engine.main status

echo
echo "=== DELIVERY STATE ==="
sqlite3 -header -column "${DB}" "
SELECT destination, status, attempts, last_synced_version
FROM deliveries
ORDER BY destination, status;
"

echo
echo "=== DUPLICATE CHECK ==="
duplicates="$(sqlite3 "${DB}" "
SELECT COUNT(*)
FROM (
  SELECT event_id, destination
  FROM deliveries
  GROUP BY event_id, destination
  HAVING COUNT(*) > 1
);
")"
echo "delivery_duplicates=${duplicates}"
[[ "${duplicates}" = "0" ]]

echo
echo "=== MAPPINGS ==="
sqlite3 -header -column "${DB}" "
SELECT destination, external_object_id, last_synced_version
FROM destination_objects
ORDER BY destination, external_object_id;
"

echo
echo "VERIFY PASS"
