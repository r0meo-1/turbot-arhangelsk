import io
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux deployment shell contract; run in Ubuntu CI")
SENDER = Path("deploy/send-tested-bundle.sh").resolve()
SHA = "a" * 40


def prepare(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    scripts = {
        "git": """#!/usr/bin/env bash
case "$1" in
  rev-parse) echo "$CHECKOUT_SHA" ;;
  diff) exit "${DIRTY:-0}" ;;
  ls-remote) printf '%s\trefs/heads/main\n' "$REMOTE_SHA" ;;
  ls-files) echo app.py ;;
  *) exit 8 ;;
esac
""",
        "ssh": "#!/usr/bin/env bash\ncat > \"$CAPTURE\"\n",
    }
    for name, content in scripts.items():
        path = binaries / name
        path.write_text(content)
        path.chmod(0o755)
    (tmp_path / "app.py").write_text("tested source\n")
    env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
           "CHECKOUT_SHA": SHA, "REMOTE_SHA": SHA, "DEPLOY_HOST": "synthetic.invalid",
           "CAPTURE": str(tmp_path / "sent")}
    return env


@pytest.mark.parametrize("change", ["stale", "wrong_checkout", "dirty", "missing_host", "bad_sha"])
def test_deploy_rejects_unverified_code_before_ssh(tmp_path, change):
    env = prepare(tmp_path)
    expected = SHA
    if change == "stale":
        env["REMOTE_SHA"] = "b" * 40
    elif change == "wrong_checkout":
        env["CHECKOUT_SHA"] = "b" * 40
    elif change == "dirty":
        env["DIRTY"] = "1"
    elif change == "missing_host":
        env["DEPLOY_HOST"] = ""
    else:
        expected = "main"
    result = subprocess.run(["bash", str(SENDER), expected], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode != 0
    assert not (tmp_path / "sent").exists()


def test_deploy_transfers_matching_revision_and_manifest(tmp_path):
    env = prepare(tmp_path)
    result = subprocess.run(["bash", str(SENDER), SHA], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    marker, revision, archive = (tmp_path / "sent").read_bytes().split(b"\n", 2)
    assert marker == b"TURBOT_DEPLOY_BUNDLE_V1"
    assert revision.decode() == SHA
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        assert bundle.extractfile("app.py").read() == b"tested source\n"
        assert bundle.extractfile(".deploy-manifest").read() == b"app.py\n"
    assert not (tmp_path / ".deploy-manifest").exists()
