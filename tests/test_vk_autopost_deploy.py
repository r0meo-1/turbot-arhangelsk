from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vk_autopost_systemd_units_are_guarded_and_scheduled():
    service = (ROOT / "deploy" / "vk-autopost.service").read_text(encoding="utf-8")
    timer = (ROOT / "deploy" / "vk-autopost.timer").read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "User=turbot" in service
    assert "Group=turbot" in service
    assert "EnvironmentFile=/opt/turbot/.env" in service
    assert "Environment=VK_AUTOPOST_ENABLED=true" in service
    assert "/opt/turbot/venv/bin/python /opt/turbot/vk_autopost.py run" in service

    assert "OnCalendar=Tue *-*-* 16:30:00 UTC" in timer
    assert "OnCalendar=Thu *-*-* 09:30:00 UTC" in timer
    assert "OnCalendar=Sun *-*-* 15:30:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "Unit=vk-autopost.service" in timer


def test_production_deployer_installs_and_enables_vk_autopost_timer():
    deploy = (ROOT / "deploy" / "turbot-deploy.sh").read_text(encoding="utf-8")

    assert '"$repo/deploy/vk-autopost.service"' in deploy
    assert '"$repo/deploy/vk-autopost.timer"' in deploy
    assert "/etc/systemd/system/vk-autopost.service" in deploy
    assert "/etc/systemd/system/vk-autopost.timer" in deploy
    assert "systemctl enable --now vk-autopost.timer" in deploy
    assert "systemctl disable --now vk-autopost.timer" in deploy


def test_github_vk_autopost_workflow_is_manual_only():
    workflow = (ROOT / ".github" / "workflows" / "vk-autopost.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
