from pathlib import Path


def test_live_tour_search_cannot_fall_back_to_curated_demo_offers():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if not combined and DEMO_MODE:" in source
    assert "if not combined:\n        dest_val = snapshot.get(\"destination\")" not in source


def test_vk_tourvisor_requires_a_real_token_when_enabled():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if TOURVISOR_ENABLED and not TOURVISOR_TOKEN:" in source
    assert "TOURVISOR_ENABLED = False" in source
