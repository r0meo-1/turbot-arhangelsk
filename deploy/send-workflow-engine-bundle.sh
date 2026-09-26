#!/usr/bin/env bash
# Send only the tested Workflow Engine runtime tree to the restricted
# production deploy entrypoint. Refuse moving refs and dirty checkouts.
set -Eeuo pipefail

expected="${1:?expected full SHA required}"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Invalid SHA" >&2
  exit 1
}

[[ "$(git rev-parse HEAD)" == "$expected" ]] || {
  echo "Checkout/SHA mismatch" >&2
  exit 1
}

git diff --quiet HEAD -- tools/workflow-engine/runtime || {
  echo "Refusing modified Workflow Engine runtime files" >&2
  exit 1
}

remote="$(git ls-remote origin refs/heads/main | cut -f1)"
[[ "$remote" == "$expected" ]] || {
  echo "Refusing stale or non-main Workflow Engine release" >&2
  exit 1
}

test -n "${DEPLOY_HOST:-}"

mapfile -t files < <(
  git ls-files -- tools/workflow-engine/runtime
)
[[ "${#files[@]}" -gt 0 ]] || {
  echo "Workflow Engine runtime manifest is empty" >&2
  exit 1
}

bundle="$(mktemp)"
trap 'rm -f "$bundle"' EXIT

tar -czf "$bundle" "${files[@]}"

{
  printf 'WORKFLOW_ENGINE_DEPLOY_BUNDLE_V1\n%s\n' "$expected"
  cat "$bundle"
} | ssh \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  "root@$DEPLOY_HOST" true
