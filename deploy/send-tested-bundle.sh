#!/usr/bin/env bash
# Called after regression passes. Refuse a moving branch or a stale queued run.
set -Eeuo pipefail
expected="${1:?expected full SHA required}"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid SHA' >&2; exit 1; }
[[ "$(git rev-parse HEAD)" == "$expected" ]] || { echo 'Checkout/SHA mismatch' >&2; exit 1; }
git diff --quiet HEAD -- || { echo 'Refusing modified tracked files' >&2; exit 1; }
remote="$(git ls-remote origin refs/heads/main | cut -f1)"
[[ "$remote" == "$expected" ]] || { echo 'Refusing stale or non-main release' >&2; exit 1; }
test -n "${DEPLOY_HOST:-}"
bundle="$(mktemp)"
trap 'rm -f "$bundle" .deploy-manifest' EXIT
git ls-files > .deploy-manifest
test -s .deploy-manifest
tar -czf "$bundle" -T .deploy-manifest .deploy-manifest
{
  printf 'TURBOT_DEPLOY_BUNDLE_V1\n%s\n' "$expected"
  cat "$bundle"
} | ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "root@$DEPLOY_HOST" true
