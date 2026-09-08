from __future__ import annotations

from .config import LABELS, WEIGHTS
from .models import ScoredHotel, SearchRequest


def build_fallback_explanation(request: SearchRequest, hotel: ScoredHotel) -> dict[str, object]:
    ratios = {key: hotel["score_breakdown"][key] / WEIGHTS[key] for key in WEIGHTS}
    ordered = sorted(ratios, key=lambda key: (-ratios[key], list(WEIGHTS).index(key)))
    strengths = [LABELS[key] for key in ordered[:3]]
    watch_out = [LABELS[key] for key in reversed(ordered) if ratios[key] < 0.75][:2]
    return {
        "best_for": "Семья с ребёнком" if request.get("children_ages") else "Взрослые туристы",
        "recommendation_reason": "Сильнее всего совпадает по: " + ", ".join(strengths) + ".",
        "strengths": strengths,
        "watch_out": watch_out,
        "mode": "fallback",
    }
