import json
from pathlib import Path

from hotel_recommendation.pipeline import SCORING_VERSION, run_vertical_slice

FIXTURES = Path(__file__).parent / "fixtures" / "hotel_recommendation"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_vertical_slice_0_is_deterministic_and_matches_golden_result():
    request = _load("phuket_family_v1.request.json")
    snapshot = _load("phuket_family_v1.snapshot.json")
    expected = _load("phuket_family_v1.expected.json")

    result_1 = run_vertical_slice(request, snapshot, selected_hotel_id="1634352")
    result_2 = run_vertical_slice(request, snapshot, selected_hotel_id="1634352")

    assert result_1 == result_2
    assert result_1 == expected
    assert result_1["scoring_version"] == SCORING_VERSION == "hotel-v1.0"
    assert len(result_1["recommendations"]) <= 3

    roles = [item["role"] for item in result_1["recommendations"]]
    assert len(roles) == len(set(roles))
    assert roles == ["best_match", "best_value", "alternative"]

    for recommendation in result_1["recommendations"]:
        assert sum(recommendation["score_breakdown"].values()) == recommendation["match_score"]
        assert recommendation["explanation"]["mode"] == "fallback"

    selected = next(
        item
        for item in result_1["recommendations"]
        if item["hotel_id"] == result_1["selected_hotel_id"]
    )
    assert result_1["qualified_lead"]["recommendation_id"] == selected["recommendation_id"]
    assert result_1["qualified_lead"]["hotel_id"] == "1634352"
    assert result_1["qualified_lead"]["selection_role"] == "best_match"
    assert result_1["qualified_lead"]["provider_response_hash"] == result_1["provider_trace"]["provider_response_hash"]
