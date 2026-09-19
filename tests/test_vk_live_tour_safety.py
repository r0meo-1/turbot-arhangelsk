import base64
import json
import time
from pathlib import Path

from shared import tourvisor


# Regression guards for the production VK funnel: demo inventory must never
# leak into live search, and expired Tourvisor credentials must hide live UI.
def test_live_tour_search_cannot_fall_back_to_curated_demo_offers():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if not combined and DEMO_MODE:" in source
    assert "if not combined:\n        dest_val = snapshot.get(\"destination\")" not in source


def test_vk_tourvisor_requires_a_real_token_when_enabled():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if TOURVISOR_ENABLED and not TOURVISOR_TOKEN:" in source
    assert "TOURVISOR_ENABLED = False" in source


def test_live_hot_tours_entry_point_cannot_show_curated_demo_catalogue():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    anchor = 'origin = info.get("origin") or "Архангельск"\n        if not DEMO_MODE:'
    assert anchor in source
    assert '"🔥 Горящие туры показываем только по актуальным данным.' in source


def test_expired_tourvisor_jwt_disables_live_search_ui():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if TOURVISOR_ENABLED and _tourvisor_jwt_expired(TOURVISOR_TOKEN):" in source
    assert 'logger.warning("VK Tourvisor JWT is expired; disabling live search UI")' in source

    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) - 60}).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    assert tourvisor.jwt_expired(token) is True


def test_deploy_probe_skips_known_expired_tourvisor_jwt():
    source = Path("deploy/verify-vk-miniapp.sh").read_text(encoding="utf-8")
    assert "jwt_status" in source
    assert "probe=skipped reason=expired_jwt" in source



def test_deploy_probe_checks_sletat_without_printing_credentials():
    source = Path("deploy/verify-vk-miniapp.sh").read_text(encoding="utf-8")
    assert "inspect_sletat()" in source
    assert "Sletat config:" in source
    assert "GetDepartCities" in source
    assert "GetCountries" in source
    assert "townFromId" in source
    assert "inspect_sletat\ninspect_travelata\ninspect_tourvisor" in source
    assert "Never print credentials, query strings, URLs, or response bodies." in source
