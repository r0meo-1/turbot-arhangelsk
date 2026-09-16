from pathlib import Path

path = Path("tests/test_vk_bot.py")
text = path.read_text(encoding="utf-8")
old = '''    def fake_search(settings, session, info, **kwargs):
        calls.append(info["origin"])
        return bot._tourvisor.SearchResult(offers=[bot._tourvisor.TourOffer(
            hotel=f"Hotel {info['origin']}", category=4, region="Анталья",
            date="2030-09-12", nights=11, meal="AI", room="Family",
            operator="Operator", price=200000, departure=info["origin"],
        )])

    monkeypatch.setattr(bot._tourvisor, "search_tours", fake_search)
'''
new = '''    def fake_search(settings, session, info, **kwargs):
        calls.append(info["origin"])
        result = bot._tourvisor.SearchResult(offers=[bot._tourvisor.TourOffer(
            hotel=f"Hotel {info['origin']}", category=4, region="Анталья",
            date="2030-09-12", nights=11, meal="AI", room="Family",
            operator="Operator", price=200000, departure=info["origin"],
        )])
        return result, "travelata"

    monkeypatch.setattr(bot._tour_providers, "search_tours", fake_search)
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected old two-city search test anchor once, got {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
