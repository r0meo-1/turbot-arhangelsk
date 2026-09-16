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


def test_deploy_v3_remains_supported_and_v4_extends_it():
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
    assert 'marker in {"TURBOT_DEPLOY_CONFIG_V3", "TURBOT_DEPLOY_CONFIG_V4"} and travelata_supplied' in source
    assert "Travelata deploy credentials not supplied; existing server values preserved" in source


def test_supplied_travelata_pair_enables_provider_first():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert '"TRAVELATA_USERNAME": quote_env(travelata_username)' in source
    assert '"TRAVELATA_PASSWORD": quote_env(travelata_password)' in source
    assert '"VK_TRAVELATA_ENABLED": "true"' in source
    assert '"TOUR_PROVIDER_ORDER": quote_env("travelata,tourvisor")' in source


def test_v4_can_install_optional_travelpayouts_token_without_erasing_existing_value():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'travelpayouts_api_token = decode(6) if marker == "TURBOT_DEPLOY_CONFIG_V4" else ""' in source
    assert 'travelpayouts_supplied = bool(travelpayouts_api_token)' in source
    assert 'values["TRAVELPAYOUTS_API_TOKEN"] = quote_env(travelpayouts_api_token)' in source
    assert "Travelpayouts deploy token not supplied; existing server value preserved" in source


def test_deploy_workflow_still_uses_compatible_v3_payload():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "TRAVELATA_USERNAME: ${{ secrets.TRAVELATA_USERNAME }}" in source
    assert "TRAVELATA_PASSWORD: ${{ secrets.TRAVELATA_PASSWORD }}" in source
    assert "TURBOT_DEPLOY_CONFIG_V3" in source
    assert "Travelata GitHub secrets must be configured as a complete pair" in source
    assert "travelata_user_b64" in source
    assert "travelata_password_b64" in source
