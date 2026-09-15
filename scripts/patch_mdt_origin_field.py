from pathlib import Path

mdt_path = Path("shared/mdt.py")
text = mdt_path.read_text(encoding="utf-8")
anchor = '''    if info.get("destination"):\n        fields.append({"name": "Направление", "values": [info["destination"]]})\n    if info.get("dates"):\n'''
replacement = '''    if info.get("destination"):\n        fields.append({"name": "Направление", "values": [info["destination"]]})\n    if info.get("origin"):\n        fields.append({"name": "Вылет", "values": [str(info["origin"])]})\n    if info.get("dates"):\n'''
if '{"name": "Вылет"' not in text:
    if anchor not in text:
        raise SystemExit("MDT origin anchor not found")
    text = text.replace(anchor, replacement, 1)
mdt_path.write_text(text, encoding="utf-8")

test_path = Path("tests/test_shared.py")
tests = test_path.read_text(encoding="utf-8")
old = '''            "destination": "Шри-Ланка",\n            "dates": "2026-10-15",\n            "people": "2",\n'''
new = '''            "destination": "Шри-Ланка",\n            "origin": "Архангельск",\n            "dates": "2026-10-15",\n            "people": "2",\n'''
if '"origin": "Архангельск"' not in tests:
    if old not in tests:
        raise SystemExit("shared test payload anchor not found")
    tests = tests.replace(old, new, 1)

old_assert = '''    params = captured["params"]\n    assert params["phone"] == ""\n    assert params["external_lead_id"] == "vk-lead-35"\n'''
new_assert = '''    params = captured["params"]\n    assert params["name"] == "Тест VK"\n    assert params["source"] == "VK Bot"\n    assert params["phone"] == ""\n    fields = {field["name"]: field["values"][0] for field in params["fields"]}\n    assert fields["Вылет"] == "Архангельск"\n    assert params["external_lead_id"] == "vk-lead-35"\n'''
if 'assert fields["Вылет"] == "Архангельск"' not in tests:
    if old_assert not in tests:
        raise SystemExit("shared test assertion anchor not found")
    tests = tests.replace(old_assert, new_assert, 1)

test_path.write_text(tests, encoding="utf-8")
