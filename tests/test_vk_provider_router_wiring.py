from pathlib import Path


def test_vk_review_ui_uses_any_live_tour_provider():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if TOUR_SEARCH_ENABLED:" in source
    assert "TOUR_PROVIDER_ORDER" in source
    assert "travelata,tourvisor" in source


def test_vk_worker_uses_provider_router_not_tourvisor_directly():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    worker = source[source.index("def _tour_search_worker"):source.index("def _tour_results_active")]
    assert "_tour_providers.search_tours(" in worker
    assert "_tourvisor.search_tours(" not in worker
    assert "TOUR_SEARCH_MAX_OFFERS" in worker


def test_vk_review_copy_is_honest_when_live_search_is_disabled():
    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "Автопоиск цен сейчас недоступен" in source
