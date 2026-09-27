"""Exercise the real preorder flow with the observed MDT tourist response."""

import pytest

from shared.mdt import MDTSettings, create_preorder


@pytest.mark.parametrize("response", [
    {"result": "success", "tourist_temp_id": "123"},
    {"data": {"tourist_temp_id": "123"}},
    {"id": 123},
    {"data": {"tourist_id": "123"}},
])
def test_tourist_response_id_is_used_to_create_preorder(response):
    calls = []

    def request(method, params):
        calls.append((method, params))
        if method == "add-tourist-temp":
            return response
        if method == "create-preorder":
            return {"id": 456}
        raise AssertionError(method)

    result = create_preorder(
        MDTSettings(), 42, {"destination": "Test country"},
        "+70000000000", "Synthetic test", {}, request,
    )

    assert result == (456, 123)
    assert [method for method, _ in calls] == ["add-tourist-temp", "create-preorder"]
    assert calls[1][1]["tourist_id"] == 123
    assert calls[1][1]["tourist_type"] == "tourist_temp"


@pytest.mark.parametrize("response", [
    {"result": "success"},
    {"result": "success", "tourist_temp_id": "invalid"},
    None,
])
def test_preorder_is_not_created_without_a_tourist_id(response):
    calls = []

    def request(method, params):
        calls.append(method)
        return response

    assert create_preorder(
        MDTSettings(), 42, {}, "+70000000000", "Synthetic test", {}, request,
    ) == (None, None)
    assert calls == ["add-tourist-temp"]
