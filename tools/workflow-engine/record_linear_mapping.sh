#!/usr/bin/env bash
set -Eeuo pipefail

DB="${WORKFLOW_DB:-/var/lib/workflow-engine/workflow.db}"

usage() {
  cat >&2 <<'EOF'
Usage:
  record_linear_mapping.sh <task_id> <linear_issue_uuid> [version]

Records a Linear issue created through the ChatGPT Linear connector back into
workflow-engine canonical delivery state.

If version is omitted, the current task version is read from the database.
EOF
  exit 2
}

[[ $# -ge 2 && $# -le 3 ]] || usage

TASK_ID="$1"
ISSUE_UUID="$2"
VERSION="${3:-}"

if [[ ! -f "${DB}" ]]; then
  echo "ERROR: database not found: ${DB}" >&2
  exit 1
fi

if ! [[ "${ISSUE_UUID}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  echo "ERROR: invalid Linear issue UUID" >&2
  exit 1
fi

task_version="$(
  sqlite3 "${DB}"     "SELECT version FROM tasks WHERE id = '${TASK_ID}' LIMIT 1;"
)"

if [[ -z "${task_version}" ]]; then
  echo "ERROR: canonical task not found: ${TASK_ID}" >&2
  exit 1
fi

if [[ -z "${VERSION}" ]]; then
  VERSION="${task_version}"
fi

if ! [[ "${VERSION}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: version must be an integer" >&2
  exit 1
fi

sqlite3 "${DB}" <<SQL
BEGIN IMMEDIATE;

INSERT INTO destination_objects (
    task_id,
    destination,
    external_object_id,
    last_synced_version,
    updated_at
)
VALUES (
    '${TASK_ID}',
    'linear',
    '${ISSUE_UUID}',
    ${VERSION},
    CURRENT_TIMESTAMP
)
ON CONFLICT(task_id, destination)
DO UPDATE SET
    external_object_id = excluded.external_object_id,
    last_synced_version = MAX(
        destination_objects.last_synced_version,
        excluded.last_synced_version
    ),
    updated_at = CURRENT_TIMESTAMP;

UPDATE deliveries
SET
    status = 'delivered_connector',
    external_object_id = '${ISSUE_UUID}',
    last_synced_version = ${VERSION},
    last_error = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE destination = 'linear'
  AND event_id IN (
      SELECT id
      FROM outbox
      WHERE event_type = 'TASK_CHANGED'
        AND json_extract(payload_json, '$.task_id') = '${TASK_ID}'
        AND COALESCE(
            json_extract(payload_json, '$.version'),
            1
        ) <= ${VERSION}
  );

COMMIT;
SQL

echo "LINEAR CONNECTOR MAPPING RECORDED"

sqlite3 -header -column "${DB}" "
SELECT
    task_id,
    destination,
    external_object_id,
    last_synced_version
FROM destination_objects
WHERE task_id = '${TASK_ID}'
  AND destination = 'linear';
"

sqlite3 -header -column "${DB}" "
SELECT
    destination,
    status,
    external_object_id,
    last_synced_version
FROM deliveries
WHERE destination = 'linear'
ORDER BY updated_at DESC;
"
