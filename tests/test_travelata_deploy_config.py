from pathlib import Path
import subprocess


DEPLOY_SCRIPT = Path("deploy/turbot-deploy.sh")
WORKFLOW = Path(".github/workflows/deploy.yml")
CONFIG_SENDER = Path("deploy/send-production-config.sh")


def test_deploy_shell_syntax_is_valid():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_v3_v4_remain_supported_and_v5_extends_them():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert '"TURBOT_DEPLOY_CONFIG_V3": 6' in source
    assert '"TURBOT_DEPLOY_CONFIG_V4": 7' in source
    assert '"TURBOT_DEPLOY_CONFIG_V5": 9' in source
    assert '"$marker" != "TURBOT_DEPLOY_CONFIG_V3"' in source
    assert '"$marker" != "TURBOT_DEPLOY_CONFIG_V4"' in source
    assert '"$marker" != "TURBOT_DEPLOY_CONFIG_V5"' in source


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


def test_v4_can_install_optional_travelpayouts_token_without_erasing_existing_value():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'travelpayouts_api_token = decode(6) if marker in {"TURBOT_DEPLOY_CONFIG_V4", "TURBOT_DEPLOY_CONFIG_V5"} else ""' in source
    assert 'travelpayouts_supplied = bool(travelpayouts_api_token)' in source
    assert 'values["TRAVELPAYOUTS_API_TOKEN"] = quote_env(travelpayouts_api_token)' in source
    assert "Travelpayouts deploy token not supplied; existing server value preserved" in source


def test_deploy_workflow_delegates_production_config_to_sender():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "DEPLOY_HOST: ${{ secrets.DEPLOY_HOST }}" in source
    assert "TRAVELATA_USERNAME: ${{ secrets.TRAVELATA_USERNAME }}" in source
    assert "TRAVELATA_PASSWORD: ${{ secrets.TRAVELATA_PASSWORD }}" in source
    assert "TRAVELPAYOUTS_API_TOKEN: ${{ secrets.TRAVELPAYOUTS_API_TOKEN }}" in source
    assert "SLETAT_LOGIN: ${{ secrets.SLETAT_LOGIN }}" in source
    assert "SLETAT_PASSWORD: ${{ secrets.SLETAT_PASSWORD }}" in source
    assert "deploy/send-production-config.sh" in source
    assert "TURBOT_DEPLOY_CONFIG_V5" not in source


def test_deploy_workflow_smokes_live_booking_partner_link():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'POST "$vk_base/miniapp/booking-link"' in source
    assert '"destination": "Phuket"' in source
    assert 'booking_status="$(curl' in source
    assert 'VK Booking.com partner-link smoke returned HTTP' in source
    assert 'VK Booking.com partner-link smoke: ok host=' in source
    assert 'parsed.scheme != "https"' in source


def test_v5_can_install_optional_sletat_pair_without_erasing_existing_values():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'sletat_login = decode(7) if marker == "TURBOT_DEPLOY_CONFIG_V5" else ""' in source
    assert 'sletat_password = decode(8) if marker == "TURBOT_DEPLOY_CONFIG_V5" else ""' in source
    assert "Sletat credentials must be supplied as a complete pair" in source
    assert "sletat_supplied = bool(sletat_login and sletat_password)" in source
    assert '"SLETAT_LOGIN": quote_env(sletat_login)' in source
    assert '"SLETAT_PASSWORD": quote_env(sletat_password)' in source
    assert '"VK_SLETAT_ENABLED": "true"' in source
    assert '"TOUR_PROVIDER_ORDER": quote_env("sletat,travelata,tourvisor")' in source
    assert "Sletat deploy credentials not supplied; existing server values preserved" in source


def test_config_sender_contains_bootstrap_safe_v4_v5_selection():
    source = CONFIG_SENDER.read_text(encoding="utf-8")
    assert "TURBOT_DEPLOY_CONFIG_V1" in source
    assert "TURBOT_DEPLOY_CONFIG_V4" in source
    assert "TURBOT_DEPLOY_CONFIG_V5" in source
    assert 'if [[ -n "$SLETAT_LOGIN" && -n "$SLETAT_PASSWORD" ]]; then' in source
    assert "Sletat credentials must be configured as a complete pair" in source
    assert "Travelata credentials must be configured as a complete pair" in source
