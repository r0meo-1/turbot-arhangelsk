from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import verify_campaign_evidence as verifier


def test_canonical_tag():
    assert verifier.canonical_tag(" VIDEO_PAIN ") == "video_pain"


@pytest.mark.parametrize("value", ["", "bad tag", "x" * 65, "кириллица"])
def test_canonical_tag_rejects_invalid(value):
    with pytest.raises(verifier.EvidenceError):
        verifier.canonical_tag(value)


def test_manager_marker_supports_html_and_plain_text():
    assert verifier.manager_has_tag("📊 Источник: <code>video_pain</code>", "video_pain")
    assert verifier.manager_has_tag("📊 Источник: video_pain\n", "video_pain")
    assert not verifier.manager_has_tag("📊 Источник: video_pain_2", "video_pain")


def test_export_marker_is_exact():
    assert verifier.export_has_tag("... | src=video_pain | +79990000000", "video_pain")
    assert not verifier.export_has_tag("... | src=video_pain_extra | +79990000000", "video_pain")


def test_cli_outputs_no_customer_data(tmp_path: Path):
    manager = tmp_path / "manager.txt"
    export = tmp_path / "export.txt"

    manager.write_text(
        "Новая заявка\n"
        "📊 Источник: <code>video_pain</code>\n"
        "Клиент: Secret Name\n"
        "Телефон: +79990000000\n",
        encoding="utf-8",
    )
    export.write_text(
        "1. [20.09.2026] Secret Name | Вьетнам | январь | "
        "2 чел | 250000₽ | src=video_pain | +79990000000",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_campaign_evidence.py",
            "--source-tag",
            "video_pain",
            "--manager-file",
            str(manager),
            "--export-file",
            str(export),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "campaign_evidence_status=PASS" in result.stdout
    assert "source_tag=video_pain" in result.stdout
    assert "manager_marker=present" in result.stdout
    assert "export_marker=present" in result.stdout
    assert "Secret Name" not in result.stdout
    assert "+79990000000" not in result.stdout
    assert "Secret Name" not in result.stderr
    assert "+79990000000" not in result.stderr


def test_cli_fails_when_manager_tag_is_missing(tmp_path: Path):
    manager = tmp_path / "manager.txt"
    export = tmp_path / "export.txt"
    manager.write_text("📊 Источник: video_dream", encoding="utf-8")
    export.write_text("src=video_pain", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_campaign_evidence.py",
            "--source-tag",
            "video_pain",
            "--manager-file",
            str(manager),
            "--export-file",
            str(export),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "campaign_evidence_status=FAIL" in result.stdout
    assert "manager_marker=missing" in result.stdout
