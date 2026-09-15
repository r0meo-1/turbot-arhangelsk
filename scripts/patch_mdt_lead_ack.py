from pathlib import Path

mdt_path = Path("shared/mdt.py")
text = mdt_path.read_text(encoding="utf-8")
old = '''    params = {
        "name": client_name or f"{settings.name_prefix} {chat_id}",
        "phone": phone,
        "email": "",
        "source": settings.source,
        "fields": fields,
    }
    result = request_fn("add-lead", params)
    if result is not None:
        log.info("Lead sent to MDT for chat %s", chat_id)
        return True
    log.warning("Failed to send lead to MDT for chat %s", chat_id)
    return False
'''
new = '''    raw_contact = str(phone or "").strip()
    compact_contact = re.sub(r"[\\s().-]+", "", raw_contact)
    is_phone = bool(re.fullmatch(r"\\+?\\d{7,15}", compact_contact))

    params = {
        "name": client_name or f"{settings.name_prefix} {chat_id}",
        "phone": raw_contact if is_phone else "",
        "email": "",
        "source": settings.source,
        "fields": fields,
    }
    if delivery_key:
        # MDT exposes this as the stable external CRM key. Keep the custom
        # field above for managers while also giving the API a machine key.
        params["external_lead_id"] = delivery_key
    if raw_contact and not is_phone:
        # Messenger contacts are not phone numbers. Sending e.g. "VK (chat id
        # ...)" in `phone` can be rejected by MDT while still returning JSON.
        params["content"] = f"Контакт: {raw_contact}"
    if settings.name_prefix.strip().casefold() == "vk":
        params["url"] = f"https://vk.com/id{chat_id}"

    result = request_fn("add-lead", params)
    lead_id = extract_id(result, "id", "lead_id")
    if lead_id is not None:
        log.info("Lead sent to MDT for chat %s (ID: %s)", chat_id, lead_id)
        return True
    if isinstance(result, dict):
        # Never log the response body: validation errors can echo submitted
        # customer data. Keys are enough to distinguish an API rejection from
        # a transport failure without leaking PII.
        shape = ",".join(sorted(str(key) for key in result.keys())) or "empty-object"
        log.warning(
            "MDT add-lead returned no lead ID for chat %s (response keys: %s)",
            chat_id,
            shape,
        )
    elif result is not None:
        log.warning(
            "MDT add-lead returned no lead ID for chat %s (response type: %s)",
            chat_id,
            type(result).__name__,
        )
    else:
        log.warning("Failed to send lead to MDT for chat %s", chat_id)
    return False
'''
if old not in text:
    if 'lead_id = extract_id(result, "id", "lead_id")' not in text:
        raise SystemExit("create_lead anchor not found")
else:
    text = text.replace(old, new, 1)
    mdt_path.write_text(text, encoding="utf-8")


test_path = Path("tests/test_shared.py")
tests = test_path.read_text(encoding="utf-8")
marker = "def test_create_lead_rejects_error_json_without_id():"
if marker not in tests:
    tests += '''


def test_create_lead_rejects_error_json_without_id():
    settings = MDTSettings(enabled=True, mode="lead", source="VK", name_prefix="VK")

    ok = create_lead(
        settings,
        424242,
        {"destination": "Шри-Ланка", "_mdt_delivery_key": "vk-lead-35"},
        "VK (чат id 424242) · Тест",
        "Тест VK",
        request_fn=lambda method, params: {"error": "validation_failed"},
    )

    assert ok is False


def test_create_lead_maps_vk_contact_outside_phone_field():
    captured = {}

    def req(method, params):
        captured["method"] = method
        captured["params"] = params
        return {"data": {"id": 654}}

    settings = MDTSettings(enabled=True, mode="lead", source="VK Bot", name_prefix="VK")
    ok = create_lead(
        settings,
        424242,
        {
            "destination": "Шри-Ланка",
            "dates": "2026-10-15",
            "people": "2",
            "budget": 270000,
            "_mdt_delivery_key": "vk-lead-35",
        },
        "VK (чат id 424242) · Тест",
        "Тест VK",
        request_fn=req,
    )

    assert ok is True
    assert captured["method"] == "add-lead"
    params = captured["params"]
    assert params["phone"] == ""
    assert params["external_lead_id"] == "vk-lead-35"
    assert params["url"] == "https://vk.com/id424242"
    assert params["content"] == "Контакт: VK (чат id 424242) · Тест"


def test_create_lead_keeps_real_phone_in_phone_field():
    captured = {}

    def req(method, params):
        captured.update(params)
        return {"id": 777}

    settings = MDTSettings(enabled=True, mode="lead", source="Test")
    ok = create_lead(
        settings,
        7,
        {"destination": "Турция"},
        "+7 (900) 123-45-67",
        "Тест",
        request_fn=req,
    )

    assert ok is True
    assert captured["phone"] == "+7 (900) 123-45-67"
    assert "content" not in captured
'''
    test_path.write_text(tests, encoding="utf-8")
