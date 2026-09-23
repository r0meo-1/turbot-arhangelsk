from io import BytesIO
import os
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


@pytest.mark.parametrize("mode", ["target", "previous"])
@pytest.mark.parametrize("reset_ok", [True, False])
def test_git_reset_clears_bundle_metadata_only_after_success(tmp_path, mode, reset_ok):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is required for deployment regression")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".deployed-commit").write_text(SHA)
    (repo / ".deploy-manifest").write_bytes(b"app.py\n")
    text = Path("deploy/turbot-deploy.sh").read_text(encoding="utf-8")
    start = text.index(f'git reset --hard "${mode}"')
    end = text.index('"$venv/pip"', start)
    snippet = text[start:end]
    script = '''set -euo pipefail
repo="$PWD/repo"
target=target
previous=previous
git() { return RESET_CODE; }
''' .replace("RESET_CODE", "0" if reset_ok else "1") + snippet
    result = subprocess.run([bash, "-c", script], cwd=tmp_path, capture_output=True)
    assert (result.returncode == 0) is reset_ok
    assert (repo / ".deployed-commit").exists() is not reset_ok
    assert (repo / ".deploy-manifest").exists() is not reset_ok


@pytest.mark.parametrize("bundle", [False, True])
def test_git_deploy_same_head_does_not_skip_bundle_checkout(tmp_path, bundle):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is required for deployment regression")
    if bundle:
        (tmp_path / ".deployed-commit").write_text(SHA)
    text = Path("deploy/turbot-deploy.sh").read_text(encoding="utf-8")
    condition = next(line for line in text.splitlines() if line.startswith('if [[ "$target" == "$previous"'))
    script = 'repo="$PWD"; target=same; previous=same\n' + condition + '\n echo skip\nelse\n echo deploy\nfi\n'
    result = subprocess.run([bash, "-c", script], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == ("deploy" if bundle else "skip")


def test_bundle_same_revision_fast_path_requires_both_services_healthy(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is required for deployment regression")
    text = Path("deploy/turbot-deploy.sh").read_text(encoding="utf-8")
    start = text.index("bundle_revision_is_healthy() {")
    end = text.index("\ndeploy_bundle() {", start)
    function = text[start:end]
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = "a" * 40
    (repo / ".deployed-commit").write_text(marker + "\n")
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *127.0.0.1:8000/health*) exit \"${TG_HEALTH:-0}\" ;;\n"
        "  *127.0.0.1:5100/vk/health*) exit \"${VK_HEALTH:-0}\" ;;\n"
        "  *) exit 9 ;;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    script = function + f'\nrepo="$PWD/repo"\nbundle_revision_is_healthy "{marker}"\n'
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}

    healthy = subprocess.run([bash, "-c", script], cwd=tmp_path, env=env)
    telegram_down = subprocess.run(
        [bash, "-c", script], cwd=tmp_path, env={**env, "TG_HEALTH": "1"}
    )
    vk_down = subprocess.run(
        [bash, "-c", script], cwd=tmp_path, env={**env, "VK_HEALTH": "1"}
    )
    mismatched = subprocess.run(
        [bash, "-c", function + '\nrepo="$PWD/repo"\nbundle_revision_is_healthy "' + "b" * 40 + '"\n'],
        cwd=tmp_path,
        env=env,
    )

    assert healthy.returncode == 0
    assert telegram_down.returncode != 0
    assert vk_down.returncode != 0
    assert mismatched.returncode != 0


def test_bundle_same_revision_fast_path_drains_archive_before_returning():
    text = Path("deploy/turbot-deploy.sh").read_text(encoding="utf-8")
    start = text.index('  if bundle_revision_is_healthy "$target_sha"; then')
    end = text.index("\n  fi", start)
    fast_path = text[start:end]

    assert "cat >/dev/null" in fast_path
    assert fast_path.index("cat >/dev/null") < fast_path.index("return 0")
    assert '"$repo/deploy/verify-vk-miniapp.sh"' in fast_path
