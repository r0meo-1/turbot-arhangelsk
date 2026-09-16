from pathlib import Path

path = Path("vk_bot.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one anchor, got {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "from shared import tutu as _tutu\nfrom shared import tourvisor as _tourvisor\nfrom shared import version as _version\n",
    "from shared import tutu as _tutu\n"
    "from shared import tourvisor as _tourvisor\n"
    "from shared import travelata as _travelata\n"
    "from shared import tour_providers as _tour_providers\n"
    "from shared import version as _version\n",
)

settings_anchor = '''def _tourvisor_settings() -> "_tourvisor.TourvisorSettings":
    return _tourvisor.TourvisorSettings(
        enabled=TOURVISOR_ENABLED,
        token=TOURVISOR_TOKEN,
        base_url=TOURVISOR_BASE_URL,
        timeout=TOURVISOR_TIMEOUT,
        poll_interval=TOURVISOR_POLL_INTERVAL,
        max_wait=TOURVISOR_MAX_WAIT,
        max_offers=TOURVISOR_MAX_OFFERS,
    )


'''
settings_insert = settings_anchor + '''# --- Alternative package-tour providers ------------------------------------
# Travelata partner API (June 2026+). Access is granted individually through
# Travelpayouts; keep credentials only in the server environment.
TRAVELATA_USERNAME = os.getenv("TRAVELATA_USERNAME", "").strip()
TRAVELATA_PASSWORD = os.getenv("TRAVELATA_PASSWORD", "").strip()
TRAVELATA_ENABLED = os.getenv(
    "VK_TRAVELATA_ENABLED",
    "true" if TRAVELATA_USERNAME and TRAVELATA_PASSWORD else "false",
).lower().strip() in ("1", "true", "yes")
TRAVELATA_BASE_URL = os.getenv(
    "TRAVELATA_BASE_URL", "https://api-gateway.travelata.ru"
).strip()
TRAVELATA_TIMEOUT = _env_int("TRAVELATA_TIMEOUT", 15)
TRAVELATA_MAX_OFFERS = _env_int("TRAVELATA_MAX_OFFERS", 15)


def _travelata_settings() -> "_travelata.TravelataSettings":
    return _travelata.TravelataSettings(
        enabled=TRAVELATA_ENABLED,
        username=TRAVELATA_USERNAME,
        password=TRAVELATA_PASSWORD,
        base_url=TRAVELATA_BASE_URL,
        timeout=TRAVELATA_TIMEOUT,
        max_offers=TRAVELATA_MAX_OFFERS,
    )


TOUR_PROVIDER_ORDER = tuple(
    name.strip().lower()
    for name in os.getenv("TOUR_PROVIDER_ORDER", "travelata,tourvisor").split(",")
    if name.strip()
)
TOUR_SEARCH_MAX_OFFERS = max(1, _env_int("TOUR_SEARCH_MAX_OFFERS", 15))


def _tour_provider_settings() -> "_tour_providers.ProviderSettings":
    return _tour_providers.ProviderSettings(
        order=TOUR_PROVIDER_ORDER,
        travelata=_travelata_settings(),
        tourvisor=_tourvisor_settings(),
    )


TOUR_SEARCH_ENABLED = _tour_provider_settings().enabled


'''
replace_once(settings_anchor, settings_insert)

replace_once(
    '''def _review_keyboard() -> str:
    rows: List[List[Dict[str, Any]]] = []
    if TOURVISOR_ENABLED:
''',
    '''def _review_keyboard() -> str:
    rows: List[List[Dict[str, Any]]] = []
    if TOUR_SEARCH_ENABLED:
''',
)

replace_once(
    '''    hotel_line = f"\\n🏨 Отель: {info['hotel_query']}" if info.get("hotel_query") else ""
    summary = (
''',
    '''    hotel_line = f"\\n🏨 Отель: {info['hotel_query']}" if info.get("hotel_query") else ""
    action_hint = (
        "✨ Нажмите кнопку «🔎 Показать отели и цены», чтобы увидеть актуальные варианты 👇"
        if TOUR_SEARCH_ENABLED else
        "✨ Автопоиск цен сейчас недоступен. Проверьте параметры и отправьте заявку менеджеру 👇"
    )
    summary = (
''',
)
replace_once(
    '''        "✨ Нажмите кнопку «🔎 Показать отели и цены», чтобы мгновенно увидеть доступные варианты со скидками и рейтингом 👇"
''',
    '''        f"{action_hint}"
''',
)

replace_once(
    '''    results = []
    for origin in origins[:2]:
        origin_snapshot = dict(snapshot)
        origin_snapshot["origin"] = origin
        results.append(_tourvisor.search_tours(
            _tourvisor_settings(), http_session, origin_snapshot, log=logger,
        ))
''',
    '''    results = []
    provider_names: List[str] = []
    for origin in origins[:2]:
        origin_snapshot = dict(snapshot)
        origin_snapshot["origin"] = origin
        provider_result, provider_name = _tour_providers.search_tours(
            _tour_provider_settings(), http_session, origin_snapshot, log=logger,
        )
        results.append(provider_result)
        if provider_name:
            provider_names.append(provider_name)
''',
)

replace_once(
    '''            limit=TOURVISOR_MAX_OFFERS,
        )
    combined.sort(key=lambda offer: offer.price + offer.fuel_charge)
    result = _tourvisor.SearchResult(
        offers=combined[:TOURVISOR_MAX_OFFERS],
''',
    '''            limit=TOUR_SEARCH_MAX_OFFERS,
        )
    combined.sort(key=lambda offer: offer.price + offer.fuel_charge)
    result = _tourvisor.SearchResult(
        offers=combined[:TOUR_SEARCH_MAX_OFFERS],
''',
)

replace_once(
    '''            live["_tour_offers_base"] = offers
            live["_tour_offers"] = list(offers)
''',
    '''            live["_tour_offers_base"] = offers
            live["_tour_offers"] = list(offers)
            live["_tour_provider"] = ",".join(dict.fromkeys(provider_names))
''',
)

replace_once(
    '''    logger.info("VK Tourvisor search returned no offers for %s: %s", user_id, result.error)
''',
    '''    logger.info("VK package tour search returned no offers for %s: %s", user_id, result.error)
''',
)

path.write_text(text, encoding="utf-8")

# Document only the current, verified providers. Level.Travel remains a planned
# adapter until partner access supplies its current private search contract.
env_path = Path(".env.example")
env_text = env_path.read_text(encoding="utf-8")
marker = "# ============================================================================\n# Как боту приходят апдейты\n"
if "TRAVELATA_USERNAME=" not in env_text:
    block = '''# ============================================================================
# Package-tour provider router (VK)
# ============================================================================
# Providers are tried left-to-right. Empty/unconfigured providers are skipped.
# TOUR_PROVIDER_ORDER=travelata,tourvisor
# TOUR_SEARCH_MAX_OFFERS=15
#
# Travelata partner API (new API since 2026-06-05). Credentials are issued
# individually via Travelpayouts / Travelata and use HTTP Basic Auth.
TRAVELATA_USERNAME=
TRAVELATA_PASSWORD=
# VK_TRAVELATA_ENABLED=true
# TRAVELATA_BASE_URL=https://api-gateway.travelata.ru
# TRAVELATA_TIMEOUT=15
# TRAVELATA_MAX_OFFERS=15
#
# Level.Travel is intentionally not guessed here: partner access is required
# and its current detailed search contract is supplied after approval. The
# provider router makes it possible to add it without rewriting the VK funnel.

'''
    if env_text.count(marker) != 1:
        raise SystemExit("expected .env.example provider insertion marker once")
    env_text = env_text.replace(marker, block + marker, 1)
    env_path.write_text(env_text, encoding="utf-8")

# Wiring regression tests: the live UI and worker must depend on the router,
# not directly on Tourvisor.
test_path = Path("tests/test_vk_provider_router_wiring.py")
test_path.write_text('''from pathlib import Path\n\n\ndef test_vk_review_ui_uses_any_live_tour_provider():\n    source = Path("vk_bot.py").read_text(encoding="utf-8")\n    assert "if TOUR_SEARCH_ENABLED:" in source\n    assert "TOUR_PROVIDER_ORDER" in source\n    assert "travelata,tourvisor" in source\n\n\ndef test_vk_worker_uses_provider_router_not_tourvisor_directly():\n    source = Path("vk_bot.py").read_text(encoding="utf-8")\n    worker = source[source.index("def _tour_search_worker"):source.index("def _tour_results_active")]\n    assert "_tour_providers.search_tours(" in worker\n    assert "_tourvisor.search_tours(" not in worker\n    assert "TOUR_SEARCH_MAX_OFFERS" in worker\n\n\ndef test_vk_review_copy_is_honest_when_live_search_is_disabled():\n    source = Path("vk_bot.py").read_text(encoding="utf-8")\n    assert "Автопоиск цен сейчас недоступен" in source\n''', encoding="utf-8")
