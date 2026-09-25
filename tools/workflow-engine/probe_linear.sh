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
import sys
import urllib.error
import urllib.request

endpoint = "https://api.linear.app/graphql"
api_key = os.environ["LINEAR_API_KEY"]
team_id = os.environ["LINEAR_TEAM_ID"]

# Intentionally query only team-scoped data. Fine-grained API keys can be
# restricted to selected teams; workspace-level fields such as viewer may be
# forbidden even when the key is valid for issue operations in that team.
query = """
query WorkflowProbe($teamId: String!) {
  team(id: $teamId) {
    id
    name
    key
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
        "User-Agent": "workflow-engine-probe/1.2",
    },
)

try:
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except Exception:
        detail = ""

    if (
        exc.code == 403
        and (
            "RESTRICTED_COUNTRY_BLOCKED" in detail
            or "not available in Russia" in detail
        )
    ):
        print(
            "LINEAR PROBE BLOCKED: Linear rejected this server region "
            "(countryCode=RU / RESTRICTED_COUNTRY_BLOCKED).",
            file=sys.stderr,
        )
        print(
            "This is not an API-key permission failure. Keep LINEAR_MODE=dry_run "
            "on this host and run live delivery only from a Linear-supported region.",
            file=sys.stderr,
        )
    elif exc.code == 403:
        print(
            "LINEAR PROBE FAIL: HTTP 403 Forbidden. "
            "The key is not allowed to read the selected team.",
            file=sys.stderr,
        )
        print(
            "Check that Read is enabled and Team access includes R0meo1. "
            "If you just changed permissions, save them and retry; "
            "if 403 persists, create a fresh key with the same scopes.",
            file=sys.stderr,
        )
    elif exc.code == 401:
        print(
            "LINEAR PROBE FAIL: HTTP 401 Unauthorized. "
            "The API key is invalid, revoked, or copied incorrectly.",
            file=sys.stderr,
        )
    else:
        print(
            f"LINEAR PROBE FAIL: HTTP {exc.code}",
            file=sys.stderr,
        )

    if detail:
        print("Linear response: " + detail[:800], file=sys.stderr)

    raise SystemExit(1)
except urllib.error.URLError as exc:
    print(
        "LINEAR PROBE FAIL: network error: " + str(exc),
        file=sys.stderr,
    )
    raise SystemExit(1)

if data.get("errors"):
    print(
        "LINEAR PROBE FAIL: "
        + "; ".join(x.get("message", "error") for x in data["errors"]),
        file=sys.stderr,
    )
    raise SystemExit(1)

team = (data.get("data") or {}).get("team") or {}

if team.get("id") != team_id:
    print("LINEAR PROBE FAIL: team mismatch", file=sys.stderr)
    raise SystemExit(1)

print("LINEAR PROBE PASS")
print("team=" + str(team.get("name") or team_id))
print("team_key=" + str(team.get("key") or "unknown"))
PY
