#!/usr/bin/env bash
# Two explicit actions: prepare (networked provisioning), run (loopback tests).
set -Eeuo pipefail
exec /usr/bin/python3 -I -S -B - "$0" "$@" <<'PY'
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

SHA = "7bd9c5f2dda2167b1d3797bf28c11d3ffda4d07b"
HEAD = "f779710c95df4005590077ac9ad37bdfcfd3fe95"
BASE = "3e3281fe2d9fe368547b95c9e8ec34c8db9a8be1"
REPOSITORY = "https://github.com/r0meo-1/turbot-arhangelsk.git"
API = "https://api.github.com/repos/r0meo-1/turbot-arhangelsk"
EDGE_API = "https://edgeupdates.microsoft.com/api/products?view=enterprise"
BROWSER_IDS_SHA256 = "566f8cfb8d4a29e3a3863ecd82f595cea4cb79bf77c862f8e1a2ce5595560a1d"
MODULE_COUNTS = {"test_vk_miniapp_e2e": 8, "test_telegram_miniapp_e2e": 10,
                 "test_manager_web_e2e": 1}
MODULES = [f"tests/{name}.py" for name in MODULE_COUNTS]
MIB, GIB = 1024 ** 2, 1024 ** 3
# Pins extracted from the verified PR239 browser CI artifact (10784339712).
CI_PINS = 'annotated-types==0.8.0\nanyio==4.15.1\nblinker==1.9.0\ncertifi==2026.7.22\ncharset-normalizer==3.5.1\nclick==8.5.0\ndistro==1.9.0\nFlask==3.1.3\ngreenlet==3.5.6\ngroq==1.7.0\ngunicorn==26.2.0\nh11==0.16.0\nhttpcore==1.0.9\nhttpx==0.28.1\nidna==3.20\niniconfig==2.3.0\nitsdangerous==2.2.0\nJinja2==3.1.6\njiter==0.17.0\nMarkupSafe==3.0.3\nopenai==2.54.0\npackaging==26.3\nplaywright==1.63.0\npluggy==1.6.0\npydantic==2.13.5\npydantic_core==2.46.5\npyee==13.0.1\nPygments==2.21.0\npytest==9.1.1\npython-dotenv==1.2.3\nrequests==2.34.2\nsniffio==1.3.1\ntqdm==4.70.1\ntyping-inspection==0.4.4\ntyping_extensions==4.16.0\ntzdata==2026.4\nurllib3==2.8.0\nWerkzeug==3.1.8\n'
SOURCE_SCRIPT = Path(sys.argv[1]).resolve()
ACTIVE_UNIT = None


class Blocked(RuntimeError):
    pass


def emit(key, value):
    print(f"{key} | {value}", flush=True)


def require(condition, message):
    if not condition:
        raise Blocked(message)


def dump(path, data):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=True, indent=2)
        stream.write("\n")


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_hash(root):
    """Only inspect the newly created QA tree; never follow symbolic links."""
    root = Path(root)
    h = hashlib.sha256()
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            path = Path(current) / name
            info = path.lstat()
            row = [str(path.relative_to(root)), stat.S_IMODE(info.st_mode)]
            if path.is_symlink():
                row += ["symlink", os.readlink(path)]
            elif stat.S_ISREG(info.st_mode):
                row += ["file", file_hash(path)]
            elif stat.S_ISDIR(info.st_mode):
                row += ["directory"]
            else:
                raise Blocked("Unexpected nonregular object in new QA tree")
            h.update(json.dumps(row, ensure_ascii=True).encode() + b"\n")
    return h.hexdigest()


def clean_env(home, tmp, cache, *, extra=None):
    result = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
              "HOME": str(home), "TMPDIR": str(tmp), "XDG_CACHE_HOME": str(cache),
              "TZ": "UTC", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
              "PYTHONNOUSERSITE": "1", "PYTHON_DOTENV_DISABLED": "1",
              "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
              "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
              "PIP_CONFIG_FILE": "/dev/null", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
              "PIP_NO_CACHE_DIR": "1", "PIP_NO_INPUT": "1"}
    result.update(extra or {})
    return result


def command(args, *, cwd=None, capture=False, env=None, timeout=900):
    return subprocess.run([str(x) for x in args], cwd=cwd, env=env, check=True,
                          text=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None,
                          timeout=timeout).stdout


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TurBot-PR239-browser-QA",
                                               "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as response:
        data = response.read(8 * MIB + 1)
    require(len(data) <= 8 * MIB, "Metadata exceeds bounded size")
    return json.loads(data)


def check_remote():
    pr = get_json(API + "/pulls/239")
    require(pr.get("state") == "open" and not pr.get("merged")
            and pr.get("mergeable") is True
            and pr["head"]["sha"] == HEAD and pr["base"]["sha"] == BASE
            and pr.get("merge_commit_sha") == SHA
            and pr["head"]["repo"]["full_name"] == "r0meo-1/turbot-arhangelsk",
            "PR239 state/revisions changed; no automatic repinning")
    data = get_json(API + "/actions/runs?head_sha=" + HEAD + "&per_page=100")
    runs = data.get("workflow_runs", [])
    require(runs and data.get("total_count") == len(runs), "CI listing incomplete")
    names = {r.get("name") for r in runs}
    require({"tests", "Security Baseline", "UTC compatibility gate",
             "Browser fixture isolation gate"} <= names, "Expected CI jobs missing")
    require(all(r.get("head_sha") == HEAD and r.get("status") == "completed"
                and r.get("conclusion") == "success" for r in runs),
            "CI pending or unsuccessful")
    return {"head": HEAD, "base": BASE, "tested_merge": SHA,
            "run_ids": [r["id"] for r in runs]}


def select_edge(products):
    choices = []
    for product in products:
        if product.get("Product") != "Stable":
            continue
        for release in product.get("Releases", []):
            if release.get("Platform") != "Linux" or release.get("Architecture") != "x64":
                continue
            version = release.get("ProductVersion", "")
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version):
                continue
            for artifact in release.get("Artifacts", []):
                if artifact.get("ArtifactName") != "deb":
                    continue
                url = urllib.parse.urlsplit(artifact.get("Location", ""))
                require(url.scheme == "https" and url.netloc == "packages.microsoft.com"
                        and url.path.startswith("/repos/edge/pool/main/m/microsoft-edge-stable/")
                        and url.path.endswith("_amd64.deb") and not url.query and not url.fragment,
                        "Unexpected Microsoft Edge package URL")
                digest = artifact.get("Hash", "").lower()
                require(artifact.get("HashAlgorithm", "").upper() == "SHA256"
                        and re.fullmatch(r"[0-9a-f]{64}", digest), "Edge SHA256 missing")
                size = artifact.get("SizeInBytes")
                require(type(size) is int and 0 < size <= 512 * MIB,
                        "Unexpected Edge package size")
                choices.append({"version": version, "url": artifact["Location"],
                                "sha256": digest, "size": size})
    require(choices, "No official stable Linux x64 .deb in Microsoft metadata")
    return max(choices, key=lambda c: tuple(map(int, c["version"].split("."))))


def reserve(path, additional=0):
    free = shutil.disk_usage(path).free
    require(free >= GIB + additional,
            f"Disk reserve: need {((GIB + additional) / GIB):.2f} GiB free, have {free/GIB:.2f}; no cleanup of old evidence")


def download_edge(meta, destination):
    reserve(destination.parent, meta["size"] + 800 * MIB)
    req = urllib.request.Request(meta["url"], headers={"User-Agent": "TurBot-PR239-browser-QA"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    h, count, started = hashlib.sha256(), 0, time.monotonic()
    with opener.open(req, timeout=30) as response, destination.open("xb") as output:
        final = urllib.parse.urlsplit(response.url)
        require(final.scheme == "https" and final.netloc == "packages.microsoft.com",
                "Unexpected Edge download redirect")
        while block := response.read(MIB):
            require(time.monotonic() - started < 600, "Edge download deadline exceeded")
            count += len(block)
            require(count <= meta["size"], "Edge download larger than metadata")
            output.write(block)
            h.update(block)
    require(count == meta["size"] and h.hexdigest() == meta["sha256"],
            "Edge package size/SHA256 mismatch")


def apt_configuration(q):
    """A fresh APT state and source list; no host hooks, sources, caches or locks."""
    apt = q / "prep/apt"
    for name in ("etc/parts", "etc/sources", "state/lists/partial", "cache/archives/partial", "log"):
        (apt / name).mkdir(parents=True, exist_ok=True)
    shutil.copyfile("/var/lib/dpkg/status", apt / "state/status")
    key = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
    require(Path(key).is_file(), "Ubuntu archive signing key is unavailable")
    (apt / "etc/sources.list").write_text(
        f"deb [arch=amd64 signed-by={key}] https://archive.ubuntu.com/ubuntu resolute main universe\n"
        f"deb [arch=amd64 signed-by={key}] https://archive.ubuntu.com/ubuntu resolute-updates main universe\n"
        f"deb [arch=amd64 signed-by={key}] https://security.ubuntu.com/ubuntu resolute-security main universe\n")
    text = f'''Dir::Etc "{apt}/etc";
Dir::Etc::main "-";
Dir::Etc::parts "{apt}/etc/parts";
Dir::Etc::sourcelist "{apt}/etc/sources.list";
Dir::Etc::sourceparts "{apt}/etc/sources";
Dir::State "{apt}/state";
Dir::State::status "{apt}/state/status";
Dir::State::lists "{apt}/state/lists";
Dir::State::extended_states "{apt}/state/extended_states";
Dir::Cache "{apt}/cache";
Dir::Cache::archives "{apt}/cache/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Log "{apt}/log";
APT::Architecture "amd64";
APT::Sandbox::User "turbot";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::Download-Only "true";
APT::Get::List-Cleanup "false";
Acquire::Languages "none";
Acquire::GzipIndexes "true";
Acquire::Retries "1";
Acquire::https::Timeout "30";
Acquire::IndexTargets::deb::DEP-11::DefaultEnabled "false";
Acquire::IndexTargets::deb::CNF::DefaultEnabled "false";
'''
    config = apt / "apt.conf"
    config.write_text(text)
    return config


def reject_base_upgrades(plan):
    # Do not substitute a different C runtime or service manager into the QA loader.
    forbidden = {"libc6", "libc-bin", "libc-gconv-modules-extra", "locales",
                 "systemd", "systemd-sysv", "libsystemd0", "libgcc-s1", "libstdc++6"}
    installs = {line.split()[1].split(":")[0] for line in plan.splitlines()
                if line.startswith("Inst ")}
    require(not installs & forbidden,
            "Dependency plan needs base-runtime changes: " + ", ".join(sorted(installs & forbidden)))
    require(not any(line.startswith("Remv ") for line in plan.splitlines()),
            "Dependency plan requested removals")


def provision(q):
    repo, report = q / "repo", q / "reports"
    emit("PREPARE NETWORK", "external HTTPS enabled only for provisioning; host resolver required")
    try:
        socket.getaddrinfo("api.github.com", 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Blocked(f"Preparation DNS unavailable: {exc}; no host changes") from exc
    remote = check_remote()
    dump(report / "remote-ci.json", remote)
    emit("REMOTE CI", "PASS: pinned source and workflows")
    command(["git", "-c", "core.hooksPath=/dev/null", "init", "-q", repo])
    command(["git", "-C", repo, "remote", "add", "origin", REPOSITORY])
    command(["git", "-C", repo, "fetch", "--no-tags", "--depth=2", "origin", "refs/pull/239/merge"], timeout=180)
    actual = command(["git", "-C", repo, "rev-parse", "FETCH_HEAD"], capture=True).strip()
    require(actual == SHA, "Merge ref moved; no automatic checkout of other revisions")
    parents = command(["git", "-C", repo, "show", "-s", "--format=%P", SHA], capture=True).strip()
    require(parents == BASE + " " + HEAD, "Merge parents mismatch")
    command(["git", "-c", "core.hooksPath=/dev/null", "-C", repo, "checkout", "-q", "--detach", SHA])
    require(not (repo / ".env").exists() and not (repo / ".env").is_symlink(), "Unexpected .env")
    (report / "prepared-revision.txt").write_text(SHA + "\n")
    reserve(q, 1100 * MIB)
    emit("PREPARE", "Separate Python venv; production venv is neither read nor changed")
    command(["/usr/bin/python3", "-m", "venv", str(q / "venv")])
    py = q / "venv/bin/python"
    pins = q / "prep/ci-requirements.txt"
    pins.write_text(CI_PINS)
    command([py, "-m", "pip", "install", "--only-binary=:all:", "--no-cache-dir",
             "--index-url", "https://pypi.org/simple", "-r", pins], timeout=600)
    command([py, "-m", "pip", "check"])
    deps = command([py, "-m", "pip", "freeze"], capture=True)
    (report / "dependencies.txt").write_text(deps)
    versions = command([py, "--version"], capture=True) + command([py, "-m", "pytest", "--version"], capture=True)
    (report / "python-pytest-version.txt").write_text(versions)
    help_text = command([py, "-m", "pytest", "--help"], cwd=q / "prep", capture=True)
    require("--max-warnings" in help_text, "QA pytest lacks zero-warning limit")
    emit("PREPARE", "Download Edge as data; no host browser installer")
    meta = select_edge(get_json(EDGE_API))
    dump(report / "edge-metadata.json", meta)
    edgedeb = q / "prep/microsoft-edge-stable.deb"
    download_edge(meta, edgedeb)
    require(command(["dpkg-deb", "-f", edgedeb, "Package"], capture=True).strip() == "microsoft-edge-stable",
            "Unexpected Edge package name")
    require(command(["dpkg-deb", "-f", edgedeb, "Architecture"], capture=True).strip() == "amd64",
            "Unexpected Edge architecture")
    emit("EDGE PACKAGE", meta["version"] + " | SHA256 checked against Microsoft HTTPS metadata")
    config = apt_configuration(q)
    apt_env = dict(os.environ, APT_CONFIG=str(config))
    reserve(q, 700 * MIB)
    emit("PREPARE", "Private APT indexes; dependency download ONLY, no dpkg registration or maintainer scripts")
    command(["apt-get", "update"], env=apt_env, timeout=600)
    install = ["--no-install-recommends", "--no-remove", "install", str(edgedeb), "fonts-liberation"]
    plan = command(["apt-get", "--simulate", *install], capture=True, env=apt_env)
    (report / "dependency-plan.txt").write_text(plan)
    reject_base_upgrades(plan)
    command(["apt-get", "--download-only", "--yes", *install], env=apt_env, timeout=600)
    packages = [edgedeb] + sorted((q / "prep/apt/cache/archives").glob("*.deb"))
    manifest, kib = [], 0
    for package in packages:
        name = command(["dpkg-deb", "-f", package, "Package"], capture=True).strip()
        require(not name.startswith("libc6") and name not in {"libc-bin", "systemd", "systemd-sysv"},
                "Refusing base-runtime package extraction")
        size = command(["dpkg-deb", "-f", package, "Installed-Size"], capture=True).strip()
        require(size.isdigit(), "Missing package installed-size metadata")
        kib += int(size)
        manifest.append({"package": name, "sha256": file_hash(package), "bytes": package.stat().st_size})
    reserve(q, kib * 1024 + 256 * MIB)
    dump(report / "package-manifest.json", manifest)
    for package in packages:
        command(["dpkg-deb", "--extract", package, q / "sysroot"])
    edge = q / "sysroot/opt/microsoft/msedge/msedge"
    require(edge.is_file(), "Extracted Edge executable missing")
    reserve(q)
    emit("PREPARE", "Files provisioned; browser has NOT been launched")


def qa_library_paths(q):
    return ":".join(str(q / "sysroot" / relative) for relative in (
        "usr/lib/x86_64-linux-gnu", "lib/x86_64-linux-gnu",
        "usr/lib/x86_64-linux-gnu/nss", "usr/lib", "lib"))


def evaluate_junit(root, pytest_rc):
    cases = list(root.iter("testcase"))
    counts = collections.Counter(c.get("classname", "").split(".")[-1] for c in cases)
    failures = sum(c.find("failure") is not None for c in cases)
    errors = sum(c.find("error") is not None for c in cases)
    skips = sum(c.find("skipped") is not None for c in cases)
    ids = [(c.get("classname", ""), c.get("name", "")) for c in cases]
    digest = hashlib.sha256(json.dumps(sorted(ids), ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    passed = len(cases) - failures - errors - skips
    okay = (pytest_rc == 0 and counts == MODULE_COUNTS and digest == BROWSER_IDS_SHA256
            and failures == errors == skips == 0 and len(ids) == len(set(ids)))
    return {"passed": passed, "failed": failures, "errors": errors, "skipped": skips,
            "modules": dict(counts), "identity_match": digest == BROWSER_IDS_SHA256,
            "pytest_exit_code": pytest_rc, "gate": "PASS" if okay else "FAILED_OR_INCOMPLETE"}


def test_worker(q, runtime):
    py, report = q / "venv/bin/python", runtime / "reports"
    # Do not use socket.if_nameindex() or ip(8) here: both require AF_NETLINK.
    # The run sandbox intentionally permits only AF_UNIX/AF_INET/AF_INET6.
    # Read namespace-local sysfs/procfs instead, which verifies the same invariants
    # without widening the allowed address-family surface.
    netdir = Path("/sys/class/net")
    require(netdir.is_dir(), "Network namespace sysfs is unavailable")
    interfaces = {entry.name for entry in netdir.iterdir()}
    require(interfaces == {"lo"}, "Test network is not loopback-only")

    route4 = Path("/proc/net/route").read_text().splitlines()[1:]
    has_v4_default = any(
        len(fields := line.split()) >= 2 and fields[1] == "00000000"
        for line in route4
    )
    require(not has_v4_default, "IPv4 default route exists")

    route6 = Path("/proc/net/ipv6_route").read_text().splitlines()
    has_v6_default = any(
        len(fields := line.split()) >= 2
        and fields[0] == "0" * 32
        and fields[1] == "00"
        for line in route6
    )
    require(not has_v6_default, "IPv6 default route exists")
    emit("BROWSER NETWORK", "LOOPBACK_ONLY: no external interface/default route")
    # A loader preflight is not a browser test and never contacts a server.
    library_env = dict(os.environ, LD_LIBRARY_PATH=qa_library_paths(q),
                       FONTCONFIG_FILE=str(q / "fontconfig.xml"))
    ldd = subprocess.run(["ldd", "/opt/microsoft/msedge/msedge"], capture_output=True,
                         text=True, env=library_env, timeout=30)
    (report / "edge-loader.txt").write_text(ldd.stdout + ldd.stderr)
    require(ldd.returncode == 0 and "not found" not in ldd.stdout + ldd.stderr,
            "Edge needs additional libraries; inspect edge-loader.txt; no host installation")
    # Any preflight failure remains NOT RUN, never a successful browser gate.
    version = command(["/opt/microsoft/msedge/msedge", "--version"], capture=True, env=library_env, timeout=30)
    (report / "edge-version.txt").write_text(version)
    (report / "tested-revision.txt").write_text(SHA + "\n")
    (report / "pytest.started").write_text("STARTED\n")
    emit("TESTED COMMIT", SHA)
    emit("BROWSER SCOPE", "19 cases only; no core/targeted rerun")
    emit("WARNING POLICY", "-W error --max-warnings=0")
    rc = subprocess.run([str(py), "-B", "-m", "pytest", *MODULES,
                         "-o", "addopts=", "-q", "-ra", "--tb=short", "-W", "error", "--max-warnings=0",
                         "--durations=10", "-o", "faulthandler_timeout=90",
                         "-o", f"cache_dir={runtime}/cache/pytest", f"--basetemp={runtime}/tmp/pytest",
                         f"--junitxml={report}/pytest.xml"], cwd=q / "repo", env=library_env).returncode
    (report / "pytest.exit-code").write_text(str(rc) + "\n")
    if (report / "pytest.xml").is_file():
        result = evaluate_junit(ET.parse(report / "pytest.xml").getroot(), rc)
        dump(report / "summary.json", result)
        emit("BROWSER JUNIT", "{passed} passed; {failed} failed; {errors} errors; {skipped} skipped".format(**result))
        emit("BROWSER IDENTITY", "MATCH" if result["identity_match"] else "MISMATCH")
    return rc


def memory_budget():
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in ("MemTotal", "MemAvailable"):
            info[key] = int(value.strip().split()[0]) * 1024
    available = info["MemAvailable"]
    # This is an intentionally conservative local QA policy, not a guarantee
    # that production memory use cannot change while the tests run.
    budget = min(512 * MIB, available - 256 * MIB)
    budget = budget // MIB * MIB
    require(budget >= 320 * MIB,
            f"Memory reserve: MemAvailable={available//MIB} MiB; need at least 576 MiB; no swap/host changes")
    return budget, info


def sandbox(q, stage, runtime=None):
    global ACTIVE_UNIT
    budget, memory = memory_budget()
    emit("MEMORY AVAILABLE", f"{memory['MemAvailable']//MIB} MiB")
    emit("QA MEMORY LIMIT", f"{budget//MIB} MiB; swap disabled for QA cgroup")
    unit = "turbot-qa-pr239-browser-" + stage + "-" + uuid.uuid4().hex[:10]
    props = {"Type": "exec", "User": "turbot", "Group": "turbot",
             "WorkingDirectory": str(q / ("repo" if stage == "run" else "prep")),
             "ProtectSystem": "strict", "ProtectHome": "yes",
             "TemporaryFileSystem": "/opt:ro /var/tmp:ro /tmp:rw,size=64M,nodev,nosuid",
             "ProtectProc": "invisible", "PrivateIPC": "yes", "ProtectHostname": "yes",
             "PrivateDevices": "yes", "NoNewPrivileges": "yes", "CapabilityBoundingSet": "",
             "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
             # Do not set RestrictSUIDSGID here. On systemd 259 it blocks openat2(),
             # which current GNU tar and potentially Chromium-family binaries use.
             # The QA worker is still an unprivileged static user with
             # NoNewPrivileges=yes and an empty CapabilityBoundingSet.
             "RuntimeMaxSec": "25min" if stage == "prepare" else "15min",
             "TimeoutStopSec": "15s", "KillMode": "control-group", "CPUQuota": "50%",
             "MemoryMax": str(budget), "MemorySwapMax": "0", "TasksMax": "256", "UMask": "0077"}
    if stage == "prepare":
        # Preparation needs the host resolver (Ubuntu's /etc/resolv.conf normally
        # points into /run/systemd/resolve). Keep sensitive runtime subtrees hidden,
        # but do not hide all of /run or DNS fails before any download can start.
        props["InaccessiblePaths"] = "-/run/credentials -/run/secrets -/run/user"
        props["RestrictAddressFamilies"] = "AF_UNIX AF_INET AF_INET6"
        props["BindPaths"] = str(q)
        props["ReadWritePaths"] = " ".join(str(q / x) for x in
                                           ("prep", "repo", "venv", "sysroot", "home", "tmp", "cache", "reports"))
        env = clean_env(q / "home", q / "tmp", q / "cache")
        log = q / "reports/prepare.log"
        args = ["_prepare", str(q)]
    else:
        props["InaccessiblePaths"] = "/run"
        props["PrivateNetwork"] = "yes"
        props["RestrictAddressFamilies"] = "AF_UNIX AF_INET AF_INET6"
        props["BindReadOnlyPaths"] = f"{q} {q}/sysroot/opt/microsoft:/opt/microsoft"
        props["ReadWritePaths"] = str(runtime)
        env = clean_env(runtime / "home", runtime / "tmp", runtime / "cache",
                        extra={"PYTHONTRACEMALLOC": "1"})
        log = runtime / "reports/pytest.log"
        args = ["_test", str(q), str(runtime)]
    cmd = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--unit=" + unit]
    for key, value in props.items():
        cmd += ["-p", key + "=" + value]
    cmd += ["/usr/bin/env", "-i", *[f"{k}={v}" for k, v in env.items()],
            "/bin/bash", str(q / "launcher.sh"), *args]
    ACTIVE_UNIT = unit
    emit("QA UNIT", unit)
    try:
        with log.open("x", encoding="utf-8") as stream:
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, errors="replace", stdin=subprocess.DEVNULL) as proc:
                for line in proc.stdout:
                    print(line, end="", flush=True)
                    stream.write(line)
                    stream.flush()
                rc = proc.wait()
        return rc
    finally:
        # This unit name was allocated locally, never provided by user input.
        subprocess.run(["systemctl", "stop", unit], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=25)
        ACTIVE_UNIT = None


def prerequisites():
    require(os.geteuid() == 0, "Run in the existing root Bash session")
    data = Path("/etc/os-release").read_text()
    require(re.search(r'^ID=ubuntu$', data, re.M) and re.search(r'^VERSION_ID="?26\.04"?$', data, re.M),
            "This launcher is explicitly for Ubuntu 26.04")
    require(os.uname().machine == "x86_64", "amd64 host required")
    require(Path("/run/systemd/system").is_dir(), "systemd manager unavailable")
    for tool in ("systemd-run", "systemctl", "git", "dpkg-deb", "apt-get", "ip", "ldd", "python3"):
        require(shutil.which(tool), f"Required existing tool missing: {tool}; no host packages will be installed")
    require(sys.version_info >= (3, 14), "Host /usr/bin/python3 must be Python 3.14+; production venv will not be borrowed")
    return pwd.getpwnam("turbot")


def prepare():
    user = prerequisites()
    reserve("/var/tmp", 2 * GIB)
    memory_budget()
    q = Path(tempfile.mkdtemp(prefix="turbot-pr239-browser.", dir="/var/tmp"))
    os.chown(q, 0, user.pw_gid)
    q.chmod(0o750)
    for name in ("prep", "repo", "venv", "sysroot", "home", "tmp", "cache", "reports", "runs"):
        target = q / name
        target.mkdir(mode=0o700)
        os.chown(target, user.pw_uid, user.pw_gid)
    shutil.copyfile(SOURCE_SCRIPT, q / "launcher.sh")
    os.chown(q / "launcher.sh", 0, user.pw_gid)
    (q / "launcher.sh").chmod(0o440)
    # Root-owned configuration; the preparation worker cannot change it.
    (q / "fontconfig.xml").write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f'<dir>{q}/sysroot/usr/share/fonts</dir><dir>/usr/share/fonts</dir>'
        '<cachedir prefix="xdg">fontconfig</cachedir></fontconfig>\n')
    (q / "fontconfig.xml").chmod(0o440)
    os.chown(q / "fontconfig.xml", 0, user.pw_gid)
    emit("QA DIRECTORY", q)
    rc = sandbox(q, "prepare")
    (q / "reports/prepare.exit-code").write_text(str(rc) + "\n")
    if rc:
        emit("PREPARATION GATE", "FAILED_OR_INCOMPLETE")
        emit("BROWSER GATE", "NOT RUN")
        emit("REPORTS", q / "reports")
        return 1
    manifest = {name: tree_hash(q / name) for name in ("repo", "venv", "sysroot")}
    manifest["launcher.sh"] = file_hash(q / "launcher.sh")
    manifest["fontconfig.xml"] = file_hash(q / "fontconfig.xml")
    dump(q / "ready.json", {"source": SHA, "immutable": manifest,
                            "host_python": file_hash(Path("/usr/bin/python3").resolve())})
    (q / "ready.json").chmod(0o440)
    os.chown(q / "ready.json", 0, user.pw_gid)
    emit("PREPARATION GATE", "PASS: dependencies provisioned, no browser test executed")
    emit("REPORTS", q / "reports")
    emit("NEXT COMMAND", f"bash '{q}/launcher.sh' run '{q}'")
    return 0


def validate_q(path):
    require(re.fullmatch(r"/var/tmp/turbot-pr239-browser\.[A-Za-z0-9_]+", path),
            "Use exactly the new QA directory printed by prepare")
    q = Path(path)
    require(q.is_dir() and not q.is_symlink() and q.resolve() == q,
            "QA path must not be a symlink")
    ready = q / "ready.json"
    require(ready.is_file() and not ready.is_symlink() and ready.stat().st_uid == 0,
            "Successful root-owned preparation record missing")
    data = json.loads(ready.read_text())
    require(data.get("source") == SHA, "Preparation revision mismatch")
    return q, data


def verify_immutable(q, ready):
    for name in ("repo", "venv", "sysroot"):
        require(tree_hash(q / name) == ready["immutable"][name], "QA immutable tree changed: " + name)
    for name in ("launcher.sh", "fontconfig.xml"):
        require(file_hash(q / name) == ready["immutable"][name], "QA helper changed: " + name)
    require(file_hash(Path("/usr/bin/python3").resolve()) == ready["host_python"],
            "System Python changed since preparation")


def run(path):
    user = prerequisites()
    q, ready = validate_q(path)
    verify_immutable(q, ready)
    require(not (q / "run.claim").exists(), "This QA environment already has a run claim; do not overwrite/repeat evidence")
    reserve(q, 128 * MIB)
    memory_budget()
    runtime = Path(tempfile.mkdtemp(prefix="run-", dir=q / "runs"))
    os.chown(runtime, user.pw_uid, user.pw_gid)
    for name in ("home", "tmp", "cache", "reports"):
        target = runtime / name
        target.mkdir(mode=0o700)
        os.chown(target, user.pw_uid, user.pw_gid)
    with (q / "run.claim").open("x") as stream:
        stream.write(str(runtime) + "\n")
    emit("BROWSER REPORTS", runtime / "reports")
    rc = sandbox(q, "run", runtime)
    report = runtime / "reports"
    (report / "systemd-run.exit-code").write_text(str(rc) + "\n")
    intact = True
    try:
        verify_immutable(q, ready)
    except Blocked as exc:
        intact = False
        emit("INTEGRITY DETAIL", exc)
    result = json.loads((report / "summary.json").read_text()) if (report / "summary.json").is_file() else {}
    pytest_rc = (report / "pytest.exit-code").read_text().strip() if (report / "pytest.exit-code").is_file() else "NOT RUN_OR_INTERRUPTED"
    okay = rc == 0 and intact and result.get("gate") == "PASS" and pytest_rc == "0"
    verdict = 0 if okay else 1
    emit("BROWSER GATE", "PASS: 19/19 for pinned revision" if okay else "FAILED_OR_INCOMPLETE")
    emit("WARNING GATE", "PASS: strict pytest exit 0" if pytest_rc == "0" else "NOT PASSED_OR_NOT_EVALUATED")
    emit("PYTEST EXIT CODE", pytest_rc)
    emit("QA IMMUTABLE FILES", "UNCHANGED" if intact else "CHANGED")
    emit("VERIFICATION EXIT CODE", verdict)
    emit("REPORTS", report)
    (report / "verification.exit-code").write_text(str(verdict) + "\n")
    return verdict


def interrupted(signum, frame):
    if ACTIVE_UNIT:
        subprocess.run(["systemctl", "stop", ACTIVE_UNIT], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=25)
    raise SystemExit(128 + signum)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Isolated PR239 browser QA; no merge/deploy/production changes")
    parser.add_argument("action", choices=("prepare", "run", "_prepare", "_test"))
    parser.add_argument("directory", nargs="?")
    parser.add_argument("runtime", nargs="?")
    args = parser.parse_args(sys.argv[2:])
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    if args.action.startswith("_"):
        require(os.geteuid() == pwd.getpwnam("turbot").pw_uid and os.environ.get("INVOCATION_ID") is None,
                "Internal action must use the declared clean QA user environment")
        q = Path(args.directory)
        require(re.fullmatch(r"/var/tmp/turbot-pr239-browser\.[A-Za-z0-9_]+", str(q)), "Unexpected internal QA path")
        if args.action == "_prepare":
            provision(q)
            return 0
        runtime = Path(args.runtime or "")
        require(runtime.parent == q / "runs" and runtime.name.startswith("run-")
                and runtime.is_dir() and not runtime.is_symlink(), "Unexpected QA runtime path")
        return test_worker(q, runtime)
    return prepare() if args.action == "prepare" else run(args.directory or "")


if __name__ == "__main__":
    try:
        result = main()
    except (Blocked, OSError, ValueError, subprocess.SubprocessError, KeyError) as exc:
        emit("BLOCKED", str(exc))
        result = 1
    finally:
        emit("DEPLOY", "NOT PERFORMED; existing VPS evidence and production services are not changed")
    sys.exit(result)
PY
