from pathlib import Path


# Regression guard for the production VK funnel: a failed/empty Tourvisor
# response must never be replaced with the curated demo hotel catalogue.
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
    import base64
    import json
    import time

    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if TOURVISOR_ENABLED and _tourvisor_jwt_expired(TOURVISOR_TOKEN):" in source
    assert 'logger.warning("VK Tourvisor JWT is expired; disabling live search UI")' in source

    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) - 60}).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    namespace = {}
    helper_source = source[
        source.index("def _tourvisor_jwt_expired"):
        source.index("if TOURVISOR_ENABLED and _tourvisor_jwt_expired")
    ]
    exec(helper_source, {"json": json, "base64": base64, "time": time}, namespace)
    assert namespace["_tourvisor_jwt_expired"](token) is True

