import pytest
from shared.search_errors import common_error_code
from shared.tourvisor import SearchResult


@pytest.mark.parametrize('other,expected', [
    (SearchResult(error='missing', error_code='departure_unsupported'), 'departure_unsupported'),
    (SearchResult(error='timeout'), ''),
    (SearchResult(), ''),
    (SearchResult(error='destination', error_code='destination_unsupported'), ''),
])
def test_never_claim_departure_unsupported_when_another_source_did_not_confirm(other, expected):
    first = SearchResult(error='missing', error_code='departure_unsupported')
    assert common_error_code([first, other]) == expected
