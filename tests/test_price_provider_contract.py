from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from hotel_recommendation.identity import ProviderHotelRef
from hotel_recommendation.pricing import (
    HotelPriceQuote,
    HotelPriceSearchResult,
    PriceSearchRequest,
    StaticPriceProvider,
)


def request() -> PriceSearchRequest:
    return PriceSearchRequest(
        check_in=date(2030, 2, 10),
        check_out=date(2030, 2, 20),
        adults=2,
        children_ages=(5,),
        currency="rub",
    )


def ref(hotel_id: str) -> ProviderHotelRef:
    return ProviderHotelRef(provider="fixture_price", provider_hotel_id=hotel_id)


def test_request_is_exact_and_normalizes_currency():
    value = request()

    assert value.check_in == date(2030, 2, 10)
    assert value.check_out == date(2030, 2, 20)
    assert value.adults == 2
    assert value.children == 1
    assert value.children_ages == (5,)
    assert value.currency == "RUB"


def test_request_rejects_invalid_dates_and_child_ages():
    with pytest.raises(ValueError, match="check_out"):
        PriceSearchRequest(
            check_in=date(2030, 2, 20),
            check_out=date(2030, 2, 20),
            adults=2,
            children_ages=(),
            currency="RUB",
        )

    with pytest.raises(ValueError, match="children_ages"):
        PriceSearchRequest(
            check_in=date(2030, 2, 10),
            check_out=date(2030, 2, 20),
            adults=2,
            children_ages=(18,),
            currency="RUB",
        )


def test_result_requires_quotes_from_the_declared_provider():
    wrong = HotelPriceQuote(
        provider_ref=ProviderHotelRef(provider="other", provider_hotel_id="101"),
        total_price=210000,
        currency="RUB",
    )

    with pytest.raises(ValueError, match="result.provider"):
        HotelPriceSearchResult(
            provider="fixture_price",
            request=request(),
            quotes=(wrong,),
            fetched_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )


def test_result_rejects_duplicate_provider_hotel_quotes():
    quote = HotelPriceQuote(provider_ref=ref("101"), total_price=210000, currency="RUB")

    with pytest.raises(ValueError, match="duplicate"):
        HotelPriceSearchResult(
            provider="fixture_price",
            request=request(),
            quotes=(quote, quote),
            fetched_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )


def test_static_provider_does_not_manufacture_missing_quotes():
    first = HotelPriceQuote(
        provider_ref=ref("101"),
        total_price=210000,
        currency="RUB",
        offer_id="offer-101",
    )
    provider = StaticPriceProvider(
        provider_name="fixture_price",
        quotes={ref("101"): first},
    )

    result = provider.search_prices(request(), [ref("101"), ref("102"), ref("103")])

    assert result.request == request()
    assert result.provider == "fixture_price"
    assert result.provider_request_id == "fixture-price-request"
    assert result.stale_cache is False
    assert result.quotes == (first,)
    assert {quote.provider_ref.provider_hotel_id for quote in result.quotes} == {"101"}


def test_invalid_price_is_preserved_for_downstream_degradation_not_repaired():
    invalid = HotelPriceQuote(
        provider_ref=ref("bad-price"),
        total_price=-5000,
        currency="RUB",
    )
    provider = StaticPriceProvider(
        provider_name="fixture_price",
        quotes={ref("bad-price"): invalid},
    )

    result = provider.search_prices(request(), [ref("bad-price")])

    assert result.quotes[0].total_price == -5000


def test_price_quote_has_no_canonical_identity_or_scoring_fields():
    quote = HotelPriceQuote(
        provider_ref=ref("101"),
        total_price=210000,
        currency="RUB",
    )

    assert not hasattr(quote, "canonical_hotel_id")
    assert not hasattr(quote, "signals")
    assert not hasattr(quote, "score")


def test_static_provider_rejects_foreign_provider_refs():
    provider = StaticPriceProvider(provider_name="fixture_price", quotes={})
    foreign = ProviderHotelRef(provider="other", provider_hotel_id="101")

    with pytest.raises(ValueError, match="another provider"):
        provider.search_prices(request(), [foreign])
