from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "vk_wall_cleanup.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "vk-cleanup-safe.yml").read_text(encoding="utf-8")


def test_vk_cleanup_repo_tool_is_read_only():
    assert 'vk_call("wall.get"' in SCRIPT
    for forbidden in (
        'vk_call("wall.delete"',
        'vk_call("photos.delete"',
        '"apply", help=',
        "def cmd_apply",
    ):
        assert forbidden not in SCRIPT


def test_vk_cleanup_workflow_never_applies_deletions():
    assert "workflow_dispatch" in WORKFLOW
    assert "VK_TOKEN: ${{ secrets.VK_TOKEN }}" in WORKFLOW
    for forbidden in ("--delete-attached-photos", "python .\\vk_wall_cleanup.py apply", "--yes"):
        assert forbidden not in WORKFLOW
