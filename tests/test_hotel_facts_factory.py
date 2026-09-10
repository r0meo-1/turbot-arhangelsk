import pytest

from hotel_recommendation.providers import (
    DisabledHotelFactsProvider,
    HotelFactsProvider,
    TripadvisorConfigurationError,
    TripadvisorTerraClient,
    create_hotel_facts_provider,
)


@pytest.mark.parametrize("operation,args", [
    ("search_hotels", ("Phuket",)),
    ("get_location", ("123",)),
    ("get_photos", ("123",)),
])
def test_default_stub_never_creates_a_network_client(monkeypatch, operation, args):
    monkeypatch.delenv("TRIPADVISOR_PROVIDER", raising=False)
    monkeypatch.setenv("TRIPADVISOR_API_KEY", "test-key")
    monkeypatch.setenv("TRIPADVISOR_TIMEOUT_SECONDS", "invalid-but-disabled")

    def unexpected_client(*args, **kwargs):
        pytest.fail("disabled provider must not construct a live client")

    monkeypatch.setattr("hotel_recommendation.providers.factory.TripadvisorTerraClient", unexpected_client)
    provider: HotelFactsProvider = create_hotel_facts_provider()
    assert isinstance(provider, DisabledHotelFactsProvider)
    with pytest.raises(TripadvisorConfigurationError, match="disabled"):
        getattr(provider, operation)(*args)


def test_terra_mode_is_explicit_and_loads_environment(monkeypatch):
    monkeypatch.setenv("TRIPADVISOR_PROVIDER", " Terra ")
    monkeypatch.setenv("TRIPADVISOR_API_KEY", " test-key ")
    monkeypatch.setenv("TRIPADVISOR_TIMEOUT_SECONDS", "4.5")
    provider: HotelFactsProvider = create_hotel_facts_provider()
    assert isinstance(provider, TripadvisorTerraClient)
    assert provider.settings.api_key == "test-key"
    assert provider.settings.timeout_seconds == 4.5


@pytest.mark.parametrize("mode", ["", "plugin", "stub", "unknown"])
def test_unknown_mode_is_a_configuration_error(monkeypatch, mode):
    monkeypatch.setenv("TRIPADVISOR_PROVIDER", mode)
    with pytest.raises(TripadvisorConfigurationError, match="disabled or terra"):
        create_hotel_facts_provider()
