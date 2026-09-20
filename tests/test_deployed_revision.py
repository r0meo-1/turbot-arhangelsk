from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from deploy.verify_revision import matches_revision, verify
from shared.version import git_revision


SHA = "a" * 40


def test_bundle_revision_takes_priority_over_stale_git(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("b" * 40)
    (tmp_path / ".deployed-commit").write_text(SHA + "\n")
    assert git_revision(str(tmp_path)) == SHA[:7]


@pytest.mark.parametrize("marker", ["", "bad", "a" * 39, "a" * 41, "g" * 40])
def test_invalid_bundle_marker_does_not_claim_stale_git_revision(tmp_path, marker):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("b" * 40)
    (tmp_path / ".deployed-commit").write_text(marker)
    assert git_revision(str(tmp_path)) == "unknown"


def test_git_checkout_without_bundle_marker_still_reports_head(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(SHA)
    assert git_revision(str(tmp_path)) == SHA[:7]


@pytest.mark.parametrize("revision", [SHA, SHA[:7]])
def test_public_health_accepts_healthy_target(revision):
    assert matches_revision({"status": "ok", "revision": revision}, SHA)


@pytest.mark.parametrize("payload", [
    {"status": "ok", "revision": "b" * 7},
    {"status": "ok", "revision": "unknown"},
    {"status": "degraded", "revision": SHA[:7]},
    {"status": "ok"}, None, [],
])
def test_public_health_rejects_stale_or_unhealthy_runtime(payload):
    assert not matches_revision(payload, SHA)


def test_verifier_retries_old_process_then_accepts_new_process():
    responses = [BytesIO(b'{"status":"ok","revision":"bbbbbbb"}'),
                 BytesIO(b'{"status":"ok","revision":"aaaaaaa"}')]
    with patch("deploy.verify_revision.urlopen", side_effect=responses), \
         patch("deploy.verify_revision.time.sleep") as sleep:
        assert verify("https://example.invalid/health", SHA, attempts=2)
        sleep.assert_called_once_with(5)


def test_verifier_fails_after_bounded_network_errors():
    with patch("deploy.verify_revision.urlopen", side_effect=OSError), \
         patch("deploy.verify_revision.time.sleep") as sleep:
        assert not verify("https://example.invalid/health", SHA, attempts=2)
        sleep.assert_called_once_with(5)


@pytest.mark.parametrize("previous_bundle", [False, True])
def test_bundle_rollback_restores_files_and_revision_metadata(tmp_path, previous_bundle):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is required for deployment archive regression")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("old application")
    (tmp_path / "old-manifest").write_bytes(b"app.py\n")
    if previous_bundle:
        (repo / ".deploy-manifest").write_bytes(b"app.py\n")
        (repo / ".deployed-commit").write_text("b" * 40 + "\n")
    deployer = Path("deploy/turbot-deploy.sh").read_text(encoding="utf-8")
    archive = deployer.split("  # Restore revision and manifest", 1)[1]
    archive = archive[archive.index("  for name"):archive.index('\n\n  "$venv/python"')]
    restore_start = deployer.index('    rm -f "$repo/.deployed-commit"')
    restore_end = deployer.index('    systemctl restart turbot', restore_start)
    restore = deployer[restore_start:restore_end]
    script = '''set -euo pipefail
repo="$PWD/repo"
old_manifest="$PWD/old-manifest"
backup="$PWD/backup.tar.gz"
metadata=()
''' + archive + '''
printf new > "$repo/app.py"
printf new > "$repo/.deploy-manifest"
printf new > "$repo/.deployed-commit"
''' + restore
    subprocess.run([bash, "-c", script], cwd=tmp_path, check=True, capture_output=True)
    assert (repo / "app.py").read_text() == "old application"
    if previous_bundle:
        assert git_revision(str(repo)) == "b" * 7
        assert (repo / ".deploy-manifest").read_text() == "app.py\n"
    else:
        assert not (repo / ".deployed-commit").exists()
        assert not (repo / ".deploy-manifest").exists()
