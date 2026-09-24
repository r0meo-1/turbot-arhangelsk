#!/usr/bin/env bash
# One full-discovery run of the pinned PR239 snapshot. No deployment.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/bin:/usr/bin:/bin
readonly PY=/opt/turbot/venv/bin/python
readonly KEEP=/var/tmp/turbot-pr238.jIreqRI5
readonly HEAD_SHA=f779710c95df4005590077ac9ad37bdfcfd3fe95
readonly BASE_SHA=3e3281fe2d9fe368547b95c9e8ec34c8db9a8be1
readonly SHA=7bd9c5f2dda2167b1d3797bf28c11d3ffda4d07b
OUT=''; REPO=''; SOURCE_READY=0; GATE='NOT RUN'; PYTEST_RC='NOT RUN'
WARNING_GATE='NOT EVALUATED'; UNIT=''; ACTIVE=0

clean() {
    /usr/bin/env -i PATH=/opt/turbot/venv/bin:/usr/local/bin:/usr/bin:/bin \
        HOME="$OUT/home" TMPDIR="$OUT/tmp" TZ=UTC LC_ALL=C.UTF-8 \
        PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHON_DOTENV_DISABLED=1 \
        GIT_OPTIONAL_LOCKS=0 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 "$@"
}

audit_source() {
    # Only the NEW checkout is inspected. Never read the saved PR238 workspace.
    clean "$PY" -I -S -B - "$REPO" <<'PY'
import hashlib, os, stat, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(root),
                                   '-C', str(root), *args], timeout=30)
h = hashlib.sha256()
def add(value):
    h.update(len(value).to_bytes(8, 'big')); h.update(value)
add(git('rev-parse', 'HEAD'))
add(git('ls-files', '--stage', '-z'))
paths = set(git('ls-files', '--cached', '--others', '-z').split(b'\0')) - {b''}
for name in sorted(paths):
    path = root / os.fsdecode(name)
    add(name)
    try:
        info = path.lstat()
    except FileNotFoundError:
        add(b'MISSING'); continue
    add(str(info.st_mode).encode())
    if stat.S_ISLNK(info.st_mode):
        add(os.fsencode(os.readlink(path)))
    elif stat.S_ISREG(info.st_mode):
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            add(hashlib.file_digest(stream, 'sha256').digest())
    else:
        add(b'NONREGULAR')
print(h.hexdigest())
PY
}

finish() {
    local rc=$?
    trap - EXIT
    set +e
    if [[ "$ACTIVE" == 1 && "$UNIT" == turbot-qa-pr239-* ]]; then
        # Stops only this launcher's transient QA unit, never production units.
        systemctl stop "$UNIT" >/dev/null 2>&1
    fi
    local integrity='NOT CHECKED'
    if [[ "$SOURCE_READY" == 1 ]]; then
        if AFTER="$(audit_source)"; then
            printf '%s\n' "$AFTER" > "$OUT/reports/source.after.sha256"
            if [[ "$BEFORE" == "$AFTER" ]]; then
                integrity=UNCHANGED
            else
                integrity=CHANGED; GATE='FAILED: new checkout changed'; rc=1
            fi
        else
            integrity=UNKNOWN; GATE='INCOMPLETE: source audit failed'; rc=1
        fi
    fi
    echo "TESTED COMMIT | $SHA"
    echo "FULL REGRESSION GATE | $GATE"
    echo "WARNING GATE | $WARNING_GATE"
    echo "PYTEST EXIT CODE | $PYTEST_RC"
    echo "NEW CHECKOUT INTEGRITY | $integrity"
    echo "VERIFICATION EXIT CODE | $rc"
    if [[ -n "$OUT" && -d "$OUT/reports" ]]; then
        printf '%s\n' "$rc" > "$OUT/reports/verification.exit-code"
        echo "REPORTS | $OUT/reports"
    else
        echo 'REPORTS | NOT CREATED'
    fi
    echo 'PROTECTED PR238 DIRECTORY | NOT INSPECTED BY THIS LAUNCHER'
    echo 'DEPLOY | NOT PERFORMED; production service checks are a separate step'
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

[[ "$EUID" == 0 && -x "$PY" && -d /run/systemd/system ]] || {
    echo 'BLOCKED: run from the existing root VPS Bash session with its virtualenv and systemd'
    exit 1
}
for tool in git systemd-run systemctl mktemp timeout tee chown id; do
    command -v "$tool" >/dev/null || { echo "BLOCKED: $tool missing"; exit 1; }
done
id turbot >/dev/null 2>&1 || { echo 'BLOCKED: existing turbot service user missing'; exit 1; }
OUT="$(mktemp -d /var/tmp/turbot-pr239-full.XXXXXXXX)"
mkdir -p "$OUT/home" "$OUT/tmp" "$OUT/cache" "$OUT/reports"
REPO="$OUT/repo"
echo "NEW VERIFICATION DIRECTORY | $OUT"

# BEGIN_REMOTE_CHECK
clean "$PY" -I -S -B - "$OUT/reports/remote-ci.json" <<'PY'
import json, sys, urllib.request
BASE = 'https://api.github.com/repos/r0meo-1/turbot-arhangelsk'
HEAD = 'f779710c95df4005590077ac9ad37bdfcfd3fe95'
PARENT = '3e3281fe2d9fe368547b95c9e8ec34c8db9a8be1'
MERGE = '7bd9c5f2dda2167b1d3797bf28c11d3ffda4d07b'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    request = urllib.request.Request(BASE + path, headers={
        'User-Agent': 'TurBot-PR239-full-verifier', 'Accept': 'application/vnd.github+json'})
    with opener.open(request, timeout=30) as response:
        return json.load(response)
def verify(fetch):
    pr = fetch('/pulls/239')
    if (pr.get('state') != 'open' or pr.get('merged') or pr.get('mergeable') is not True
        or pr['head']['sha'] != HEAD or pr['base']['sha'] != PARENT
        or pr.get('merge_commit_sha') != MERGE
        or pr['head']['repo']['full_name'] != 'r0meo-1/turbot-arhangelsk'):
        raise ValueError('PR state or pinned revisions changed; no automatic repinning')
    data = fetch('/actions/runs?head_sha=' + HEAD + '&per_page=100')
    runs = data.get('workflow_runs', [])
    required = {'tests', 'Security Baseline', 'UTC compatibility gate', 'Browser fixture isolation gate'}
    if not runs or data.get('total_count') != len(runs) or not required <= {r['name'] for r in runs}:
        raise ValueError('CI listing missing required workflows or incomplete')
    if any(r['head_sha'] != HEAD or r['status'] != 'completed' or r['conclusion'] != 'success' for r in runs):
        raise ValueError('CI not completely successful')
    if not {35940455484, 35940455505} <= {r['id'] for r in runs}:
        raise ValueError('pinned browser/strict regression evidence missing')
    return {'head': HEAD, 'base': PARENT, 'tested_merge': MERGE,
            'runs': [{'id': r['id'], 'name': r['name'], 'conclusion': r['conclusion']} for r in runs]}
if __name__ == '__main__':
    try:
        result = verify(get)
        with open(sys.argv[1], 'x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print('REMOTE CI | PASS: pinned PR239 workflows verified', flush=True)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        raise SystemExit('BLOCKED: remote verification: ' + detail)
PY
# END_REMOTE_CHECK

clean git -c core.hooksPath=/dev/null init -q "$REPO"
clean git -C "$REPO" remote add origin https://github.com/r0meo-1/turbot-arhangelsk.git
clean timeout --signal=TERM --kill-after=15s 180s \
    git -C "$REPO" fetch --no-tags --depth=2 origin refs/pull/239/merge
[[ "$(clean git -C "$REPO" rev-parse FETCH_HEAD)" == "$SHA" ]] || {
    echo 'BLOCKED: PR merge ref moved'; exit 1;
}
[[ "$(clean git -C "$REPO" show -s --format=%P "$SHA")" == "$BASE_SHA $HEAD_SHA" ]] || {
    echo 'BLOCKED: merge parents mismatch'; exit 1;
}
clean git -c core.hooksPath=/dev/null -C "$REPO" checkout -q --detach "$SHA"
[[ ! -e "$REPO/.env" && ! -L "$REPO/.env" ]] || { echo 'BLOCKED: unexpected .env'; exit 1; }
printf '%s\n' "$SHA" > "$OUT/reports/tested-revision.txt"

# The payload is OUTSIDE the checkout. All runtime writes have separate paths.
cat > "$OUT/run-tests.sh" <<'PAYLOAD'
#!/usr/bin/env bash
set -Eeuo pipefail
OUT=$1
PY=/opt/turbot/venv/bin/python
for tool in node sqlite3; do
    command -v "$tool" >/dev/null || { echo "BLOCKED: $tool missing; no packages installed"; exit 1; }
done
"$PY" --version
"$PY" -m pytest --version
node --version
sqlite3 --version
"$PY" -m pytest --help > "$OUT/reports/pytest-help.txt"
grep -q -- '--max-warnings' "$OUT/reports/pytest-help.txt" || {
    echo 'BLOCKED: installed pytest lacks --max-warnings; no automatic upgrade'; exit 1;
}
echo '=== FULL REGRESSION: ONE DISCOVERY RUN, NO SEPARATE TARGETED RERUN ==='
echo 'NETWORK | loopback-only OS namespace'
echo 'WARNING POLICY | -W error --max-warnings=0'
printf '%s\n' STARTED > "$OUT/reports/pytest.started"
set +e
"$PY" -B -m pytest tests -o addopts= -q -ra --tb=short \
    -W error --max-warnings=0 --durations=10 \
    -o faulthandler_timeout=120 -o "cache_dir=$OUT/cache/pytest" \
    --basetemp="$OUT/tmp/pytest" --junitxml="$OUT/reports/pytest.xml"
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/reports/pytest.exit-code"
exit "$rc"
PAYLOAD
chmod 700 "$OUT/run-tests.sh"
# Ownership changes apply ONLY to this newly-created directory.
chown -R turbot:turbot "$OUT"
BEFORE="$(audit_source)"
printf '%s\n' "$BEFORE" > "$OUT/reports/source.before.sha256"
SOURCE_READY=1
UNIT="turbot-qa-pr239-${OUT##*.}"
echo "QA UNIT | $UNIT (temporary; no production service restart)"
ACTIVE=1
set +e
systemd-run --quiet --wait --pipe --collect --unit="$UNIT" \
    -p Type=exec -p User=turbot -p Group=turbot -p "WorkingDirectory=$REPO" \
    -p ProtectSystem=strict -p ProtectHome=yes \
    -p "ReadOnlyPaths=$REPO" \
    -p "ReadWritePaths=$OUT/home $OUT/tmp $OUT/cache $OUT/reports" \
    -p TemporaryFileSystem=/opt/turbot:ro -p BindReadOnlyPaths=/opt/turbot/venv \
    -p "InaccessiblePaths=/run $KEEP" \
    -p PrivateNetwork=yes -p PrivateDevices=yes -p NoNewPrivileges=yes \
    -p CapabilityBoundingSet= -p RestrictSUIDSGID=yes \
    -p ProtectKernelTunables=yes -p ProtectKernelModules=yes -p ProtectControlGroups=yes \
    -p RuntimeMaxSec=20min -p TimeoutStopSec=15s -p KillMode=control-group \
    -p CPUQuota=50% -p MemoryMax=1G -p TasksMax=256 \
    /usr/bin/env -i PATH=/opt/turbot/venv/bin:/usr/local/bin:/usr/bin:/bin \
    HOME="$OUT/home" TMPDIR="$OUT/tmp" XDG_CACHE_HOME="$OUT/cache" TZ=UTC LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHON_DOTENV_DISABLED=1 \
    PYTHONTRACEMALLOC=1 GIT_OPTIONAL_LOCKS=0 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
    /bin/bash "$OUT/run-tests.sh" "$OUT" </dev/null 2>&1 | tee "$OUT/reports/pytest.log"
RCS=("${PIPESTATUS[@]}")
set -e
ACTIVE=0
printf '%s\n' "${RCS[0]}" > "$OUT/reports/systemd-run.exit-code"
if [[ -f "$OUT/reports/pytest.exit-code" ]]; then
    PYTEST_RC="$(cat "$OUT/reports/pytest.exit-code")"
fi
GATE='FAILED_OR_INCOMPLETE: inspect pytest.log'
if [[ "${RCS[1]}" != 0 ]]; then echo 'BLOCKED: log could not be saved'; exit 1; fi
if [[ "${RCS[0]}" != 0 || "$PYTEST_RC" != 0 ]]; then
    [[ "$PYTEST_RC" == 6 ]] && WARNING_GATE='FAILED: warning threshold exceeded'
    [[ ! -f "$OUT/reports/pytest.started" ]] && GATE='NOT RUN: sandbox or prerequisites failed'
    exit 1
fi
WARNING_GATE='PASS: pytest exit 0 with warnings-as-errors and zero-warning limit'

# BEGIN_JUNIT_CHECK
set +e
clean "$PY" -I -S -B - "$OUT/reports/pytest.xml" "$OUT/reports/summary.json" <<'PY'
import hashlib, json, sys
import xml.etree.ElementTree as ET
BROWSER = {'test_vk_miniapp_e2e', 'test_telegram_miniapp_e2e', 'test_manager_web_e2e'}
NON_BROWSER_HASH = '6312b2005aed4f21bb6f00b35c51376ed4012fd2bd2991c82ff10f893add1ed6'
BROWSER_HASH = '566f8cfb8d4a29e3a3863ecd82f595cea4cb79bf77c862f8e1a2ce5595560a1d'
def digest(rows):
    return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
def evaluate(root):
    cases = list(root.iter('testcase'))
    failures = sum(c.find('failure') is not None for c in cases)
    errors = sum(c.find('error') is not None for c in cases)
    skips = [c for c in cases if c.find('skipped') is not None]
    ids = [(c.get('classname', ''), c.get('name', '')) for c in cases if c.find('skipped') is None]
    non_browser = [row for row in ids if row[0].split('.')[-1] not in BROWSER]
    browser = [row for row in ids if row[0].split('.')[-1] in BROWSER]
    result = {'passed': len(cases) - failures - errors - len(skips), 'failed': failures,
              'errors': errors, 'skipped': len(skips), 'gate': 'FAILED_OR_INCOMPLETE'}
    if failures or errors or len(non_browser) != 1097 or digest(non_browser) != NON_BROWSER_HASH:
        return result, 1
    result['non_browser_identity_match'] = True
    if not skips and len(browser) == 19 and digest(browser) == BROWSER_HASH:
        result['gate'] = 'PASS: complete 1116-case VPS coverage'
        return result, 0
    # Collection skips are retained, never converted into passing cases.
    skip_names = {c.get('name') for c in skips if not c.get('classname')}
    if not browser and len(skips) == 3 and skip_names == {'tests.' + name for name in BROWSER}:
        result['gate'] = 'INCOMPLETE: 1097 non-browser cases passed; 3 browser modules skipped'
        return result, 2
    return result, 1
if __name__ == '__main__':
    result, rc = evaluate(ET.parse(sys.argv[1]).getroot())
    with open(sys.argv[2], 'x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print('FULL JUNIT | {passed} passed; {failed} failed; {errors} errors; {skipped} skipped'.format(**result))
    print('COVERAGE | ' + result['gate'])
    raise SystemExit(rc)
PY
EVIDENCE_RC=$?
set -e
case "$EVIDENCE_RC" in
    0) GATE='PASS: full VPS coverage and strict warning gate' ;;
    2) GATE='INCOMPLETE: browser modules skipped; 1097 non-browser tests passed' ;;
    *) GATE='FAILED_OR_INCOMPLETE: JUnit coverage mismatch' ;;
esac
exit "$EVIDENCE_RC"
