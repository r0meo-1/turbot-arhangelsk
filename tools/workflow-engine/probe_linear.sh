#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${WORKFLOW_ENV_FILE:-/etc/workflow-engine/env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

: "${LINEAR_API_KEY:?LINEAR_API_KEY is missing}"
: "${LINEAR_TEAM_ID:?LINEAR_TEAM_ID is missing}"

LINEAR_API_KEY="${LINEAR_API_KEY}" LINEAR_TEAM_ID="${LINEAR_TEAM_ID}" python3 - <<'PY'
import json
import os
import urllib.request

endpoint = "https://api.linear.app/graphql"
api_key = os.environ["LINEAR_API_KEY"]
team_id = os.environ["LINEAR_TEAM_ID"]

query = """
query WorkflowProbe($teamId: String!) {
  viewer {
    id
    name
  }
  team(id: $teamId) {
    id
    name
  }
}
"""

payload = json.dumps({
    "query": query,
    "variables": {"teamId": team_id},
}).encode("utf-8")

req = urllib.request.Request(
    endpoint,
    data=payload,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "Authorization": api_key,
        "User-Agent": "workflow-engine-probe/1.0",
    },
)

with urllib.request.urlopen(req, timeout=20) as response:
    data = json.loads(response.read().decode("utf-8"))

if data.get("errors"):
    raise SystemExit(
        "LINEAR PROBE FAIL: "
        + "; ".join(x.get("message", "error") for x in data["errors"])
    )

viewer = (data.get("data") or {}).get("viewer") or {}
team = (data.get("data") or {}).get("team") or {}

if team.get("id") != team_id:
    raise SystemExit("LINEAR PROBE FAIL: team mismatch")

print("LINEAR PROBE PASS")
print("viewer=" + str(viewer.get("name") or viewer.get("id") or "ok"))
print("team=" + str(team.get("name") or team_id))
PY
