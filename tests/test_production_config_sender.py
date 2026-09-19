import os
from pathlib import Path
import subprocess


SENDER = Path("deploy/send-production-config.sh")


def _fake_ssh(tmp_path: Path) -> tuple[Path, dict]:
    bin_dir = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    bin_dir.mkdir()
    capture_dir.mkdir()
    fake = bin_dir / "ssh"
    fake.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
count_file="$CAPTURE_DIR/count"
n=0
if [[ -f "$count_file" ]]; then n="$(cat "$count_file")"; fi
n=$((n + 1))
printf '%s' "$n" > "$count_file"
cat > "$CAPTURE_DIR/payload.$n"
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + env["PATH"],
            "CAPTURE_DIR": str(capture_dir),
            "DEPLOY_HOST": "example.test",
            "VK_MINI_APP_ID": "12345",
            "VK_MINI_APP_SECRET": "vk-secret",
            "MDT_API_KEY": "mdt-secret",
            "TRAVELPAYOUTS_API_TOKEN": "tp-secret",
            "TRAVELATA_USERNAME": "",
            "TRAVELATA_PASSWORD": "",
            "SLETAT_LOGIN": "",
            "SLETAT_PASSWORD": "",
        }
    )
    return capture_dir, env


def _run(env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SENDER)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _markers(capture_dir: Path) -> list[str]:
    payloads = sorted(capture_dir.glob("payload.*"), key=lambda p: int(p.name.split(".")[-1]))
    return [
        p.read_text(encoding="utf-8").splitlines()[0]
        for p in payloads
    ]


def test_sender_shell_syntax_is_valid():
    result = subprocess.run(
        ["bash", "-n", str(SENDER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_sender_bootstraps_with_v4_when_sletat_is_not_configured(tmp_path):
    capture_dir, env = _fake_ssh(tmp_path)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert _markers(capture_dir) == [
        "TURBOT_DEPLOY_CONFIG_V1",
        "TURBOT_DEPLOY_CONFIG_V4",
    ]


def test_sender_uses_v5_when_complete_sletat_pair_exists(tmp_path):
    capture_dir, env = _fake_ssh(tmp_path)
    env["SLETAT_LOGIN"] = "agency"
    env["SLETAT_PASSWORD"] = "secret"

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert _markers(capture_dir) == [
        "TURBOT_DEPLOY_CONFIG_V1",
        "TURBOT_DEPLOY_CONFIG_V5",
    ]


def test_sender_rejects_partial_sletat_pair_before_any_ssh(tmp_path):
    capture_dir, env = _fake_ssh(tmp_path)
    env["SLETAT_LOGIN"] = "agency"

    result = _run(env)

    assert result.returncode != 0
    assert "complete pair" in result.stderr
    assert _markers(capture_dir) == []
