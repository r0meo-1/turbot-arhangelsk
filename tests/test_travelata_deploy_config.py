from pathlib import Path
import subprocess


DEPLOY_SCRIPT = Path("deploy/turbot-deploy.sh")
WORKFLOW = Path(".github/workflows/deploy.yml")


def test_deploy_shell_syntax_is_valid():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_v3_v4_v5_remain_supported():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert '"TURBOT_DEPLOY_CONFIG_V3": 6' in source
    assert '"TURBOT_DEPLOY_CONFIG_V4": 7' in source
    assert '"$marker" != "TURBOT_DEPLOY_CONFIG_V3"' in source
    assert '"$marker" != "TURBOT_DEPLOY_CONFIG_V4"' in source


def test_deploy_v3_rejects_partial_travelata_credentials():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "has_user != has_password" in source
    assert "Travelata credentials must be supplied as a complete pair" in source


def test_empty_travelata_pair_preserves_server_values():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "travelata_supplied = bool(travelata_username and travelata_password)" in source
    assert 'marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} and travelata_supplied' in source
    assert "Travelata deploy credentials not supplied; existing server values preserved" in source


def test_supplied_travelata_pair_enables_provider_first():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert '"TRAVELATA_USERNAME": quote_env(travelata_username)' in source
    assert '"TRAVELATA_PASSWORD": quote_env(travelata_password)' in source
    assert '"VK_TRAVELATA_ENABLED": "true"' in source
    assert '"TOUR_PROVIDER_ORDER": quote_env("travelata,tourvisor")' in source


def test_v4_v5_can_install_optional_travelpayouts_token_without_erasing_existing_value():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'travelpayouts_api_token = decode(6) if marker in {"TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} else ""' in source
    assert 'travelpayouts_supplied = bool(travelpayouts_api_token)' in source
    assert 'values["TRAVELPAYOUTS_API_TOKEN"] = quote_env(travelpayouts_api_token)' in source
    assert "Travelpayouts deploy token not supplied; existing server value preserved" in source


def test_deploy_workflow_uses_v4_with_travelpayouts_secret():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "TRAVELATA_USERNAME: ${{ secrets.TRAVELATA_USERNAME }}" in source
    assert "TRAVELATA_PASSWORD: ${{ secrets.TRAVELATA_PASSWORD }}" in source
    assert "TRAVELPAYOUTS_API_TOKEN: ${{ secrets.TRAVELPAYOUTS_API_TOKEN }}" in source
    assert "TURBOT_DEPLOY_CONFIG_V4" in source
    assert "TURBOT_DEPLOY_CONFIG_V1" not in source
    assert "Travelata GitHub secrets must be configured as a complete pair" in source
    assert "travelata_user_b64" in source
    assert "travelata_password_b64" in source
    assert "travelpayouts_token_b64" in source



def test_deployer_repairs_runtime_directory_before_chdir():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'chown turbot:turbot "$repo"' in source
    assert 'chmod 0750 "$repo"' in source
    assert source.index("ensure_runtime_permissions\ncd") < source.index("apply_stdin_config")


def test_deploy_bootstraps_code_before_protected_config():
    source = WORKFLOW.read_text(encoding="utf-8")

    bootstrap = 'root@${{ secrets.DEPLOY_HOST }} true </dev/null'
    assert bootstrap in source
    assert source.index(bootstrap) < source.index("TURBOT_DEPLOY_CONFIG_V4")


def test_deployer_repairs_only_live_sqlite_state_files():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repair = source.split("ensure_runtime_permissions() {", 1)[1].split("\n}", 1)[0]
    assert '"$repo/bot_state.sqlite"' in repair
    assert '"$repo/vk_bot_state.sqlite"' in repair
    assert '"$repo/bot_state.sqlite-wal"' in repair
    assert '"$repo/vk_bot_state.sqlite-shm"' in repair
    assert 'os.O_NOFOLLOW' in repair
    assert 'stat.S_ISREG' in repair
    assert 'os.fchown(fd, user.pw_uid, group.gr_gid)' in repair
    assert 'os.fchmod(fd, 0o600)' in repair
    assert "Refusing symlinked SQLite state path" in repair
    assert 'chown turbot:turbot "$state_file"' not in repair
    assert "chown -R" not in repair


def test_git_deploy_prints_vk_diagnostics_before_explicit_rollback():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    message = "TurBot VK verification failed; collecting bounded diagnostics"
    status = "systemctl status vk-turbot --no-pager -l || true"
    journal = "journalctl -u vk-turbot -n 120 --no-pager || true"
    start = source.index(message)
    assert source.index(status, start) < source.index("rollback_and_fail", start)
    assert source.index(journal, start) < source.index("rollback_and_fail", start)

    helper = source.split("rollback_and_fail() {", 1)[1].split("\n}", 1)[0]
    assert "trap - ERR" in helper
    assert "rollback || true" in helper
    assert "exit 1" in helper

    unhealthy = source.rsplit('echo "TurBot did not become healthy" >&2', 1)[1]
    assert "rollback_and_fail" in unhealthy
    assert "\nexit 1" not in unhealthy

def test_deployer_installs_main_and_vk_systemd_units_together():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    helper = source.split("install_systemd_units() {", 1)[1].split("\n}", 1)[0]
    assert '"$repo/deploy/turbot.service"' in helper
    assert '"$repo/deploy/vk-turbot.service"' in helper
    assert "/etc/systemd/system/turbot.service" in helper
    assert "/etc/systemd/system/vk-turbot.service" in helper
    assert "systemctl daemon-reload" in helper

    assert source.count("install_systemd_units") >= 5
    assert 'cp "$repo/deploy/vk-turbot.service" /etc/systemd/system/vk-turbot.service' not in source

