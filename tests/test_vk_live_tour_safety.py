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

