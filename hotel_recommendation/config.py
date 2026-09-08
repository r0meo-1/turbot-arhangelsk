SCORING_VERSION = "hotel-v1.0"

WEIGHTS = {
    "budget": 30,
    "beach_location": 20,
    "service": 15,
    "reviews": 15,
    "traveler_fit": 10,
    "preferences": 10,
}

LABELS = {
    "budget": "бюджет",
    "beach_location": "пляж и локация",
    "service": "сервис",
    "reviews": "отзывы",
    "traveler_fit": "состав туристов",
    "preferences": "пожелания",
}

TIE_BREAK_DESCRIPTION = [
    "total_score DESC",
    "reviews_score DESC",
    "tripadvisor_review_count DESC",
    "hotel_id ASC",
]
