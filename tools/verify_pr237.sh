#!/usr/bin/env bash
# Verify the CI-tested PR snapshot without modifying the live working tree.
set -Eeuo pipefail
umask 077

LIVE=/opt/turbot
PY=/opt/turbot/venv/bin/python
REPO=https://github.com/r0meo-1/turbot-arhangelsk.git
HEAD_SHA=fc14acc8f1e17067e2d80de4b31cfdf0849da9b4
TESTED_SHA=3e9d5e827dce05e4d5207933833302f1d7b20562
export PATH=/usr/local/bin:/usr/bin:/bin

[[ -x "$PY" ]] || { echo 'BLOCKED: existing virtualenv Python not found'; exit 1; }
for tool in git timeout sha256sum; do
    command -v "$tool" >/dev/null || { echo "BLOCKED: $tool missing"; exit 1; }
done

# Public GitHub GET requests only. API errors, changed revisions or pending
# checks stop the operation. No credentials or production modules are loaded.
"$PY" -I -B - <<'PY'
import json
import urllib.request

BASE = 'https://api.github.com/repos/r0meo-1/turbot-arhangelsk'
HEAD = 'fc14acc8f1e17067e2d80de4b31cfdf0849da9b4'
BASE_SHA = '0b6bcd0d3293e73c3fa0b0d86b5c466d096ba7b3'
TESTED = '3e9d5e827dce05e4d5207933833302f1d7b20562'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def get(path):
    req = urllib.request.Request(BASE + path, headers={
        'User-Agent': 'TurBot-PR237-verifier',
        'Accept': 'application/vnd.github+json',
    })
    with opener.open(req, timeout=30) as response:
        return json.load(response)

try:
    pr = get('/pulls/237')
    if (pr.get('state') != 'open' or pr.get('draft')
            or pr.get('mergeable') is not True
            or pr['head']['sha'] != HEAD or pr['base']['sha'] != BASE_SHA
            or pr.get('merge_commit_sha') != TESTED):
        raise ValueError('PR state/revisions changed or mergeability is not confirmed')
    data = get('/actions/runs?head_sha=' + HEAD + '&per_page=100')
    runs = data.get('workflow_runs', [])
    if not runs or data.get('total_count') != len(runs):
        raise ValueError('workflow listing empty or incomplete')
    if not {'tests', 'Security Baseline', 'Edge Bot CI'} <= {r['name'] for r in runs}:
        raise ValueError('expected workflows missing')
    if any(r['head_sha'] != HEAD or r['status'] != 'completed'
           or r['conclusion'] != 'success' for r in runs):
        raise ValueError('some workflows are pending or not successful')
    data = get('/commits/' + HEAD + '/status?per_page=100')
    statuses = data.get('statuses', [])
    if data.get('total_count') != len(statuses):
        raise ValueError('commit-status listing incomplete')
    if any(s['state'] != 'success' for s in statuses):
        raise ValueError('some commit statuses are not successful')
    print(f'REMOTE CI | PASS | {len(runs)} successful workflows', flush=True)
except Exception as exc:
    # Never print HTTP headers, response bodies or inherited environment.
    detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    raise SystemExit(f'BLOCKED: remote verification failed: {detail}')
PY

WORK="$(mktemp -d /var/tmp/turbot-pr237.XXXXXXXX)"
mkdir -p "$WORK/home" "$WORK/tmp" "$WORK/reports"
echo "VERIFICATION DIRECTORY | $WORK"

clean() {
    env -i \
        PATH="/opt/turbot/venv/bin:/usr/local/bin:/usr/bin:/bin" \
        HOME="$WORK/home" TMPDIR="$WORK/tmp" TZ=UTC LC_ALL=C.UTF-8 \
        PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
        PYTHON_DOTENV_DISABLED=1 \
        GIT_OPTIONAL_LOCKS=0 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
        "$@"
}

# Hash HEAD, staged entries, tracked/untracked files, and .env if present.
# Do not follow symlinks or open sockets/FIFOs. Ignore runtime files already
# excluded by Git, except .env. Only the combined fingerprint is returned.
fingerprint() {
    clean "$PY" -I -B - "$LIVE" <<'PY'
import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
def git(*args):
    return subprocess.check_output(
        ['git', '-c', 'safe.directory=' + str(root), '-C', str(root), *args],
        stderr=subprocess.DEVNULL, timeout=60,
    )
if Path(os.fsdecode(git('rev-parse', '--show-toplevel')).strip()).resolve() != root:
    raise SystemExit('BLOCKED: unexpected live repository root')
h = hashlib.sha256()
def add(data):
    h.update(len(data).to_bytes(8, 'big'))
    h.update(data)
add(git('rev-parse', 'HEAD'))
add(git('ls-files', '--stage', '-z'))
paths = set(git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0'))
paths.discard(b'')
paths.add(b'.env')
for raw in sorted(paths):
    path = root / os.fsdecode(raw)
    add(raw)
    try:
        info = path.lstat()
    except FileNotFoundError:
        add(b'MISSING')
        continue
    add(str(info.st_mode).encode())
    if stat.S_ISLNK(info.st_mode):
        add(os.fsencode(os.readlink(path)))
    elif stat.S_ISREG(info.st_mode):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').digest()
        add(digest)
    else:
        add(b'NONREGULAR')
print(h.hexdigest())
PY
}

BEFORE="$(fingerprint)"
printf '%s\n' "$BEFORE" > "$WORK/reports/live.before.sha256"
finish() {
    rc=$?
    trap - EXIT
    if AFTER="$(fingerprint)"; then
        printf '%s\n' "$AFTER" > "$WORK/reports/live.after.sha256"
        if [[ "$BEFORE" == "$AFTER" ]]; then
            echo 'LIVE TRACKED/UNTRACKED FILES + .env | UNCHANGED'
        else
            echo 'BLOCKED: live workspace changed during verification; no automatic reconciliation'
            rc=1
        fi
    else
        echo 'BLOCKED: could not verify final live-workspace fingerprint'
        rc=1
    fi
    echo "VERIFICATION EXIT CODE | $rc"
    echo "REPORTS | $WORK/reports"
    echo 'DEPLOY | NOT PERFORMED; remaining release gates still apply'
    exit "$rc"
}
trap finish EXIT

# All Git mutations are confined to this NEW repository, not /opt/turbot/.git.
clean git -c core.hooksPath=/dev/null init -q "$WORK/repo"
clean git -C "$WORK/repo" remote add origin "$REPO"
clean git -C "$WORK/repo" fetch --no-tags --depth=2 origin refs/pull/237/merge
ACTUAL="$(clean git -C "$WORK/repo" rev-parse FETCH_HEAD)"
[[ "$ACTUAL" == "$TESTED_SHA" ]] || { echo 'BLOCKED: CI-tested merge ref moved'; exit 1; }
PARENTS="$(clean git -C "$WORK/repo" show -s --format=%P "$TESTED_SHA")"
[[ "$PARENTS" == "0b6bcd0d3293e73c3fa0b0d86b5c466d096ba7b3 $HEAD_SHA" ]] || {
    echo 'BLOCKED: unexpected merge parents'; exit 1;
}
clean git -c core.hooksPath=/dev/null -C "$WORK/repo" checkout -q --detach "$TESTED_SHA"
[[ ! -e "$WORK/repo/.env" ]] || { echo 'BLOCKED: checkout contains .env'; exit 1; }
cd "$WORK/repo"

clean "$PY" --version
clean "$PY" -m pytest --version
clean "$PY" -m pytest --help > "$WORK/reports/pytest-help.txt"
if grep -q -- '--max-warnings' "$WORK/reports/pytest-help.txt"; then
    WARNING_GATE=(--max-warnings=0)
else
    WARNING_GATE=(-W error)
fi

# No pip install, stash, pull/reset in LIVE, service restart, merge or deploy.
# Read the existing virtualenv; keep environment, DBs and reports in WORK.
set +e
clean timeout --signal=TERM --kill-after=15s 15m \
    "$PY" -m pytest \
    tests/test_bot.py tests/test_vk_bot.py \
    -q -ra --tb=short "${WARNING_GATE[@]}" \
    --basetemp="$WORK/tmp/pytest" \
    --junitxml="$WORK/reports/pytest.xml" \
    2>&1 | tee "$WORK/reports/pytest.log"
PIPE_RCS=("${PIPESTATUS[@]}")
TEST_RC=${PIPE_RCS[0]}
set -e
if [[ "${PIPE_RCS[1]}" -ne 0 ]]; then
    echo 'BLOCKED: test log could not be written'
    TEST_RC=1
fi
printf '%s\n' "$TEST_RC" > "$WORK/reports/pytest.exit-code"
if [[ "$TEST_RC" -eq 0 ]]; then
    echo 'TARGETED TEST GATE | PASS'
elif [[ "$TEST_RC" -eq 6 ]]; then
    echo 'TARGETED TEST GATE | BLOCKED: warning threshold exceeded'
elif [[ "$TEST_RC" -eq 124 || "$TEST_RC" -eq 137 ]]; then
    echo 'TARGETED TEST GATE | BLOCKED: test timeout'
else
    echo 'TARGETED TEST GATE | FAILED; inspect pytest.log'
fi
exit "$TEST_RC"
