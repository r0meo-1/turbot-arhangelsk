from shared.mdt import match_country_id, parse_country_list


def test_parse_country_list_supports_live_mdt_title_shape():
    result = {
        "result": "ok",
        "count": 5,
        "data": [
            {"id": 205, "title": "Турция", "counter": 0},
            {"id": 196, "title": "Таиланд", "counter": 0},
            {"id": 49, "title": "Вьетнам", "counter": 0},
            {"id": 75, "title": "Египет", "counter": 0},
            {"id": 228, "title": "Шри-Ланка", "counter": 0},
        ],
    }

    cache = parse_country_list(result)

    assert cache == {
        "турция": 205,
        "таиланд": 196,
        "вьетнам": 49,
        "египет": 75,
        "шри-ланка": 228,
    }
    assert match_country_id(cache, "Таиланд, Пхукет") == 196
