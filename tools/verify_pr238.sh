#!/usr/bin/env bash
# Validate the pinned PR238 snapshot; never promote or modify the live checkout.
set -Eeuo pipefail
umask 077

LIVE=/opt/turbot
PY=/opt/turbot/venv/bin/python
REPO=https://github.com/r0meo-1/turbot-arhangelsk.git
HEAD_SHA=3e3281fe2d9fe368547b95c9e8ec34c8db9a8be1
BASE_SHA=fc14acc8f1e17067e2d80de4b31cfdf0849da9b4
TESTED_SHA=5a9abe482f9e86fdd11b9ee13714a93f839dd606
export PATH=/usr/local/bin:/usr/bin:/bin
WORK=''
BEFORE=''
TEST_GATE='NOT RUN'

clean() {
    env -i \
        PATH="/opt/turbot/venv/bin:/usr/local/bin:/usr/bin:/bin" \
        HOME="$WORK/home" TMPDIR="$WORK/tmp" TZ=UTC LC_ALL=C.UTF-8 \
        XDG_CACHE_HOME="$WORK/cache" \
        PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
        PYTHON_DOTENV_DISABLED=1 \
        GIT_OPTIONAL_LOCKS=0 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
        "$@"
}

# Fingerprint file contents and index entries, not just git-status filenames.
# Ignored runtime files are excluded except .env; do not follow symlinks.
fingerprint() {
    clean "$PY" -I -S -B - "$LIVE" <<'PY'
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
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            add(hashlib.file_digest(stream, 'sha256').digest())
    else:
        add(b'NONREGULAR')
print(h.hexdigest())
PY
}

finish() {
    rc=$?
    trap - EXIT
    local_result='UNKNOWN'
    if [[ -n "$BEFORE" ]]; then
        if AFTER="$(fingerprint)"; then
            printf '%s\n' "$AFTER" > "$WORK/reports/live.after.sha256"
            if [[ "$BEFORE" == "$AFTER" ]]; then
                local_result='UNCHANGED'
            else
                local_result='CHANGED; automatic reconciliation prohibited'
                rc=1
            fi
        else
            local_result='UNKNOWN; final fingerprint failed'
            rc=1
        fi
    fi
    echo "TARGETED TEST GATE | $TEST_GATE"
    echo "LIVE TRACKED/UNTRACKED FILES + .env | $local_result"
    echo "VERIFICATION EXIT CODE | $rc"
    if [[ -n "$WORK" && -d "$WORK/reports" ]]; then
        printf '%s\n' "$rc" > "$WORK/reports/verification.exit-code"
        echo "REPORTS | $WORK/reports"
    else
        echo 'REPORTS | NOT CREATED'
    fi
    echo 'DEPLOY | NOT PERFORMED; promotion requires separate release gates'
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ -x "$PY" ]] || { echo 'BLOCKED: existing virtualenv Python not found'; exit 1; }
for tool in git timeout sha256sum mktemp tee; do
    command -v "$tool" >/dev/null || { echo "BLOCKED: $tool missing"; exit 1; }
done

# BEGIN_REMOTE_GATE
# Draft is allowed for isolated validation, never for automatic promotion.
"$PY" -I -S -B - <<'PY'
import json
import urllib.request

BASE = 'https://api.github.com/repos/r0meo-1/turbot-arhangelsk'
HEAD = '3e3281fe2d9fe368547b95c9e8ec34c8db9a8be1'
BASE_SHA = 'fc14acc8f1e17067e2d80de4b31cfdf0849da9b4'
TESTED = '5a9abe482f9e86fdd11b9ee13714a93f839dd606'
STRICT_RUN = 35935006901
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def get(path):
    req = urllib.request.Request(BASE + path, headers={
        'User-Agent': 'TurBot-PR238-verifier',
        'Accept': 'application/vnd.github+json',
    })
    with opener.open(req, timeout=30) as response:
        return json.load(response)

def verify(fetch):
    pr = fetch('/pulls/238')
    if (pr.get('state') != 'open' or pr.get('merged')
            or pr.get('mergeable') is not True
            or pr['head']['sha'] != HEAD or pr['base']['sha'] != BASE_SHA
            or pr['base']['ref'] != 'fix/test-env-isolation-20260924'
            or pr['head']['repo']['full_name'] != 'r0meo-1/turbot-arhangelsk'
            or pr.get('merge_commit_sha') != TESTED):
        raise ValueError('PR state/revisions changed or mergeability is unconfirmed')
    data = fetch('/actions/runs?head_sha=' + HEAD + '&per_page=100')
    runs = data.get('workflow_runs', [])
    if not runs or data.get('total_count') != len(runs):
        raise ValueError('workflow listing empty or incomplete')
    if not {'tests', 'Security Baseline', 'UTC compatibility gate'} <= {r['name'] for r in runs}:
        raise ValueError('expected workflows missing')
    if any(r['head_sha'] != HEAD or r['status'] != 'completed'
           or r['conclusion'] != 'success' for r in runs):
        raise ValueError('some workflows are pending or not successful')
    if not any(r['id'] == STRICT_RUN and r['name'] == 'UTC compatibility gate' for r in runs):
        raise ValueError('pinned strict run missing')
    data = fetch(f'/actions/runs/{STRICT_RUN}/jobs?per_page=100')
    jobs = data.get('jobs', [])
    required = {'Warning-free regression (Python 3.12)', 'Warning-free regression (Python 3.14)'}
    if data.get('total_count') != len(jobs) or {j['name'] for j in jobs} != required:
        raise ValueError('strict matrix incomplete')
    for job in jobs:
        if job.get('head_sha') != HEAD or job.get('status') != 'completed' or job.get('conclusion') != 'success':
            raise ValueError('strict matrix job is not successful')
        steps = [s for s in job.get('steps', []) if s['name'] == 'Run full regression with warnings treated as errors']
        if len(steps) != 1 or steps[0]['status'] != 'completed' or steps[0]['conclusion'] != 'success':
            raise ValueError('full strict regression step did not pass')
    data = fetch('/commits/' + HEAD + '/status?per_page=100')
    statuses = data.get('statuses', [])
    if data.get('total_count') != len(statuses):
        raise ValueError('commit-status listing incomplete')
    if any(s['state'] != 'success' for s in statuses):
        raise ValueError('some commit statuses are not successful')
    return len(runs)

if __name__ == '__main__':
    try:
        count = verify(get)
        print(f'REMOTE CI | PASS | {count} successful workflows; strict 3.12/3.14 verified', flush=True)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        raise SystemExit(f'BLOCKED: remote verification failed: {detail}')
PY
# END_REMOTE_GATE

WORK="$(mktemp -d /var/tmp/turbot-pr238.XXXXXXXX)"
mkdir -p "$WORK/home" "$WORK/tmp" "$WORK/reports" "$WORK/cache"
echo "VERIFICATION DIRECTORY | $WORK"
BEFORE="$(fingerprint)"
printf '%s\n' "$BEFORE" > "$WORK/reports/live.before.sha256"

# Mutate only a new repository; the live Git index, worktree and refs stay intact.
clean git -c core.hooksPath=/dev/null init -q "$WORK/repo"
clean git -C "$WORK/repo" remote add origin "$REPO"
clean timeout --signal=TERM --kill-after=15s 180s \
    git -C "$WORK/repo" fetch --no-tags --depth=2 origin refs/pull/238/merge
ACTUAL="$(clean git -C "$WORK/repo" rev-parse FETCH_HEAD)"
[[ "$ACTUAL" == "$TESTED_SHA" ]] || { echo 'BLOCKED: tested merge ref moved'; exit 1; }
PARENTS="$(clean git -C "$WORK/repo" show -s --format=%P "$TESTED_SHA")"
[[ "$PARENTS" == "$BASE_SHA $HEAD_SHA" ]] || { echo 'BLOCKED: unexpected merge parents'; exit 1; }
clean git -c core.hooksPath=/dev/null -C "$WORK/repo" checkout -q --detach "$TESTED_SHA"
[[ ! -e "$WORK/repo/.env" && ! -L "$WORK/repo/.env" ]] || { echo 'BLOCKED: checkout contains .env'; exit 1; }
cd "$WORK/repo"
printf '%s\n' "$TESTED_SHA" > "$WORK/reports/tested-revision.txt"

clean "$PY" --version
clean "$PY" -m pytest --version
clean "$PY" -m pytest --help > "$WORK/reports/pytest-help.txt"
WARNING_GATE=(-W error)
if grep -q -- '--max-warnings' "$WORK/reports/pytest-help.txt"; then
    WARNING_GATE+=(--max-warnings=0)
fi

echo '=== TARGETED APPLICATION TESTS: Telegram + VK ==='
echo 'Tests run in the isolated checkout; maximum runtime 15 minutes.'
set +e
clean timeout --signal=TERM --kill-after=15s 15m \
    "$PY" -m pytest tests/test_bot.py tests/test_vk_bot.py \
    -q -ra --tb=short --durations=10 "${WARNING_GATE[@]}" \
    -o faulthandler_timeout=60 \
    --basetemp="$WORK/tmp/pytest" --junitxml="$WORK/reports/pytest.xml" \
    2>&1 | tee "$WORK/reports/pytest.log"
PIPE_RCS=("${PIPESTATUS[@]}")
TEST_RC=${PIPE_RCS[0]}
set -e
[[ "${PIPE_RCS[1]}" -eq 0 ]] || TEST_RC=1
printf '%s\n' "$TEST_RC" > "$WORK/reports/pytest.exit-code"
TEST_GATE='FAILED; inspect pytest.log'
if [[ "$TEST_RC" -eq 0 ]]; then
    TEST_GATE='INCOMPLETE; validating JUnit evidence'
    clean "$PY" -I -S -B - "$WORK/reports/pytest.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET
cases = list(ET.parse(sys.argv[1]).getroot().iter('testcase'))
modules = {c.get('classname', '').split('.')[-1] for c in cases}
if not cases or not {'test_bot', 'test_vk_bot'} <= modules:
    raise SystemExit('BLOCKED: missing targeted test evidence')
if any(c.find(tag) is not None for c in cases for tag in ('failure', 'error', 'skipped')):
    raise SystemExit('BLOCKED: targeted JUnit contains failed, errored or skipped tests')
print(f'TARGETED JUNIT | {len(cases)} passed; 0 failed; 0 errors; 0 skipped')
PY
    TEST_GATE='PASS'
elif [[ "$TEST_RC" -eq 6 ]]; then
    TEST_GATE='BLOCKED: warning threshold exceeded'
elif [[ "$TEST_RC" -eq 124 || "$TEST_RC" -eq 137 ]]; then
    TEST_GATE='BLOCKED: test timeout'
fi
exit "$TEST_RC"
