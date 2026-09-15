from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"anchor not found: {label}")
    return text.replace(old, new, 1)


# shared/mdt.py
path = Path("shared/mdt.py")
text = path.read_text(encoding="utf-8")

anchor = '''def _budget_field_value(info: Dict[str, Any]) -> str:\n    suffix = " ₽ на всю поездку" if info.get("budget_scope") == "total" else ""\n    return f"{info.get('budget')}{suffix}"\n\n\ndef add_tourist_temp(\n'''
insert = '''def _budget_field_value(info: Dict[str, Any]) -> str:\n    suffix = " ₽ на всю поездку" if info.get("budget_scope") == "total" else ""\n    return f"{info.get('budget')}{suffix}"\n\n\ndef _manager_rows(result: Any) -> List[Dict[str, Any]]:\n    """Normalize get-manager-list response shapes without exposing identities."""\n    if result is None:\n        return []\n    data = result.get("data", result) if isinstance(result, dict) else result\n    rows: List[Dict[str, Any]] = []\n    if isinstance(data, dict):\n        for key, value in data.items():\n            if not isinstance(value, dict):\n                continue\n            row = dict(value)\n            row.setdefault("id", key)\n            rows.append(row)\n    elif isinstance(data, list):\n        rows = [dict(value) for value in data if isinstance(value, dict)]\n    return rows\n\n\ndef _mdt_flag(value: Any) -> bool:\n    if isinstance(value, str):\n        return value.strip().lower() in {"1", "true", "yes", "y", "on"}\n    return bool(value)\n\n\ndef resolve_manager_id(\n    settings: MDTSettings,\n    request_fn: RequestFn,\n    log: Optional[logging.Logger] = None,\n) -> Optional[int]:\n    """Resolve an explicit manager, or the sole active MDT manager.\n\n    Automatic resolution is deliberately conservative: if MDT has more than\n    one active manager we leave the request unassigned instead of guessing.\n    """\n    log = log or logger\n    for raw in settings.manager_ids:\n        try:\n            manager_id = int(raw)\n        except (TypeError, ValueError):\n            continue\n        if manager_id > 0:\n            return manager_id\n\n    result = request_fn(\n        "get-manager-list",\n        {\n            "count": 100,\n            "offset": 0,\n            "fields": ["id", "dismissed", "office_id"],\n        },\n    )\n    active: List[int] = []\n    for row in _manager_rows(result):\n        if _mdt_flag(row.get("dismissed")):\n            continue\n        try:\n            manager_id = int(row.get("id"))\n        except (TypeError, ValueError):\n            continue\n        if manager_id > 0 and manager_id not in active:\n            active.append(manager_id)\n\n    if len(active) == 1:\n        log.info("Resolved sole active MDT manager")\n        return active[0]\n    if not active:\n        log.warning("MDT manager auto-assignment skipped: no active managers")\n    else:\n        log.warning(\n            "MDT manager auto-assignment skipped: %s active managers",\n            len(active),\n        )\n    return None\n\n\ndef add_tourist_temp(\n'''
text = replace_once(text, anchor, insert, "manager helpers")

old = '''def add_tourist_temp(\n    settings: MDTSettings,\n    name: str,\n    phone: str,\n    request_fn: RequestFn,\n    log: Optional[logging.Logger] = None,\n) -> Optional[int]:\n    log = log or logger\n    result = request_fn(\n        "add-tourist-temp",\n        {"name": name, "tel": phone, "tags": settings.tourist_tags},\n    )\n    tid = extract_id(result, "id", "tourist_id")\n'''
new = '''def add_tourist_temp(\n    settings: MDTSettings,\n    name: str,\n    phone: str,\n    request_fn: RequestFn,\n    log: Optional[logging.Logger] = None,\n    manager_id: Optional[int] = None,\n) -> Optional[int]:\n    log = log or logger\n    params: Dict[str, Any] = {"name": name, "tel": phone, "tags": settings.tourist_tags}\n    if manager_id is not None:\n        params["manager_id"] = manager_id\n    result = request_fn("add-tourist-temp", params)\n    tid = extract_id(result, "id", "tourist_id")\n'''
text = replace_once(text, old, new, "add_tourist_temp manager")

old = '''    log = log or logger\n    name = client_name or f"{settings.name_prefix} {chat_id}"\n    tourist_id = add_tourist_temp(settings, name, phone, request_fn, log=log)\n    if tourist_id is None:\n'''
new = '''    log = log or logger\n    name = client_name or f"{settings.name_prefix} {chat_id}"\n    manager_id = resolve_manager_id(settings, request_fn, log=log)\n    tourist_id = add_tourist_temp(\n        settings, name, phone, request_fn, log=log, manager_id=manager_id\n    )\n    if tourist_id is None:\n'''
text = replace_once(text, old, new, "preorder manager resolution")

old = '''    persons = _parse_persons(info.get("people"))\n    budget = _parse_budget(info.get("budget", 0))\n\n    comment_parts = []\n    if info.get("destination"):\n        comment_parts.append(f"Направление: {info['destination']}")\n    if info.get("dates"):\n'''
new = '''    persons = _parse_persons(info.get("people"))\n    budget = _parse_budget(info.get("budget", 0))\n    try:\n        children = max(0, int(info.get("kids") or 0))\n    except (TypeError, ValueError):\n        children = 0\n    child_ages: List[int] = []\n    for value in info.get("kids_ages") or []:\n        try:\n            child_ages.append(int(value))\n        except (TypeError, ValueError):\n            continue\n    night_values = [int(value) for value in re.findall(r"\\d+", str(info.get("nights") or ""))[:2]]\n\n    comment_parts = []\n    if info.get("destination"):\n        comment_parts.append(f"Направление: {info['destination']}")\n    if info.get("origin"):\n        comment_parts.append(f"Вылет: {info['origin']}")\n    if info.get("dates"):\n'''
text = replace_once(text, old, new, "preorder travel details")

old = '''    if budget:\n        comment_parts.append(f"Бюджет: {_budget_label(info)}")\n\n    params: Dict[str, Any] = {\n'''
new = '''    if budget:\n        comment_parts.append(f"Бюджет: {_budget_label(info)}")\n    if settings.source:\n        comment_parts.append(f"Источник: {settings.source}")\n    selected = info.get("selected_tour")\n    if isinstance(selected, dict):\n        selected_text = " · ".join(\n            str(value)\n            for value in (\n                selected.get("hotel"),\n                selected.get("date"),\n                f"{selected.get('nights')} ночей" if selected.get("nights") else "",\n                selected.get("meal"),\n                str(selected.get("price") or "") + " ₽" if selected.get("price") else "",\n                f"ID {selected.get('tour_id')}" if selected.get("tour_id") else "",\n            )\n            if value\n        )\n        if selected_text:\n            comment_parts.append(f"Выбранный тур: {selected_text}")\n\n    params: Dict[str, Any] = {\n'''
text = replace_once(text, old, new, "preorder source and selected tour")

text = text.replace('''        "children": 0,\n        "children_ages": [],\n''', '''        "children": children,\n        "children_ages": child_ages,\n''', 1)

old = '''    if date_from:\n        params["flightdate_from"] = date_from\n    if date_to:\n        params["flightdate_to"] = date_to\n\n    result = request_fn("create-preorder", params)\n'''
new = '''    if date_from:\n        params["flightdate_from"] = date_from\n    if date_to:\n        params["flightdate_to"] = date_to\n    if night_values:\n        params["nights_from"] = night_values[0]\n        params["nights_to"] = night_values[-1]\n    if manager_id is not None:\n        params["preorder_manager_id"] = manager_id\n    if settings.name_prefix.strip().casefold() == "vk":\n        params["link"] = f"https://vk.com/id{chat_id}"\n\n    result = request_fn("create-preorder", params)\n'''
text = replace_once(text, old, new, "preorder manager/link/nights")
path.write_text(text, encoding="utf-8")


# vk_bot.py: VK gets its own CRM mode so Telegram stays on add-lead.
path = Path("vk_bot.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    'MDT_MODE       = os.getenv("MDT_MODE", "lead").lower().strip()\n',
    'MDT_MODE       = os.getenv("VK_MDT_MODE", os.getenv("MDT_MODE", "lead")).lower().strip()\n',
    "VK MDT mode override",
)
path.write_text(text, encoding="utf-8")


# deploy/turbot-deploy.sh: preserve Telegram lead mode, route VK via preorder.
path = Path("deploy/turbot-deploy.sh")
text = path.read_text(encoding="utf-8")
old = '''            "MDT_ENABLED": "true",\n            "MDT_MODE": "lead",\n            "MDT_ACCOUNT": quote_env("apreltour"),\n'''
new = '''            "MDT_ENABLED": "true",\n            "MDT_MODE": "lead",\n            "VK_MDT_MODE": "preorder",\n            "MDT_ACCOUNT": quote_env("apreltour"),\n'''
text = replace_once(text, old, new, "deploy VK preorder mode")
path.write_text(text, encoding="utf-8")


# deploy/verify-mdt.sh: report both shared and VK-specific modes safely.
path = Path("deploy/verify-mdt.sh")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "mode = (os.getenv('MDT_MODE', 'lead') or 'lead').strip().lower()\n",
    "mode = (os.getenv('MDT_MODE', 'lead') or 'lead').strip().lower()\nvk_mode = (os.getenv('VK_MDT_MODE', mode) or mode).strip().lower()\n",
    "verify VK mode read",
)
text = replace_once(
    text,
    "    f'mode={mode} '\n    f'endpoint_host={endpoint_host} '\n",
    "    f'mode={mode} '\n    f'vk_mode={vk_mode} '\n    f'endpoint_host={endpoint_host} '\n",
    "verify VK mode output",
)
text = replace_once(
    text,
    "if mode not in {'lead', 'preorder', 'both'}:\n    print(f'MDT check: WARNING invalid MDT_MODE={mode!r}')\n    raise SystemExit(0)\n",
    "if mode not in {'lead', 'preorder', 'both'}:\n    print(f'MDT check: WARNING invalid MDT_MODE={mode!r}')\n    raise SystemExit(0)\nif vk_mode not in {'lead', 'preorder', 'both'}:\n    print(f'MDT check: WARNING invalid VK_MDT_MODE={vk_mode!r}')\n    raise SystemExit(0)\n",
    "verify VK mode validation",
)
path.write_text(text, encoding="utf-8")


# docs/vk_mdt_write.md
path = Path("docs/vk_mdt_write.md")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '- `MDT_MODE=lead` sends completed VK requests with `/api/add-lead`.\n',
    '- `MDT_MODE=lead` remains the shared/Telegram default.\n'
    '- `VK_MDT_MODE=preorder` sends VK requests with `/api/create-preorder`, which supports `preorder_manager_id`.\n'
    '- When `MDT_MANAGER_IDS` is empty, VK assigns the sole active MDT manager; if multiple are active it leaves the request unassigned instead of guessing.\n',
    1,
)
text = text.replace(
    '`tests/test_vk_mdt_write.py` covers the exact `add-lead` URL/form payload, selected-tour fields, VK source attribution, and failure isolation without touching the live CRM.',
    '`tests/test_vk_mdt_write.py` covers the VK preorder API sequence, sole-manager assignment, selected-tour/origin attribution, and failure isolation without touching the live CRM.',
    1,
)
path.write_text(text, encoding="utf-8")


# .env.example
path = Path(".env.example")
text = path.read_text(encoding="utf-8")
if "VK_MDT_MODE=" not in text:
    text = text.replace(
        'MDT_MODE=lead\n',
        'MDT_MODE=lead\n# Optional VK-only override: lead | preorder | both\nVK_MDT_MODE=\n',
        1,
    )
path.write_text(text, encoding="utf-8")


# tests/test_shared.py
path = Path("tests/test_shared.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    'settings = MDTSettings(enabled=True, mode="preorder", tourist_tags="Telegram Bot")',
    'settings = MDTSettings(enabled=True, mode="preorder", tourist_tags="Telegram Bot", manager_ids=[5])',
    1,
)
marker = "def test_create_preorder_auto_assigns_sole_active_manager():"
if marker not in text:
    insertion_point = "\n\ndef test_create_lead_includes_selected_tour():\n"
    test = '''\n\ndef test_create_preorder_auto_assigns_sole_active_manager():\n    calls = []\n    payloads = {}\n\n    def req(method, params):\n        calls.append(method)\n        payloads[method] = params\n        if method == "get-manager-list":\n            return {\n                "data": [\n                    {"id": "17", "dismissed": 0, "office_id": 1},\n                    {"id": "99", "dismissed": 1, "office_id": 1},\n                ]\n            }\n        if method == "add-tourist-temp":\n            return {"id": 10}\n        if method == "create-preorder":\n            return {"data": {"preorder_id": 20}}\n        return None\n\n    settings = MDTSettings(\n        enabled=True, mode="preorder", source="VK Bot", name_prefix="VK", tourist_tags="VK Bot"\n    )\n    preorder_id, tourist_id = create_preorder(\n        settings,\n        424242,\n        {\n            "destination": "Таиланд",\n            "origin": "Архангельск",\n            "dates": "2026-10-15",\n            "nights": "10",\n            "people": "2",\n            "kids": 0,\n            "kids_ages": [],\n            "budget": 270000,\n            "budget_scope": "total",\n            "selected_tour": {\n                "hotel": "Mandarava Resort",\n                "date": "2026-10-15",\n                "nights": 10,\n                "meal": "BB",\n                "price": 155000,\n                "tour_id": "th-6",\n            },\n        },\n        "VK (чат id 424242) · Тест",\n        "Тест VK",\n        {"таиланд": 7},\n        request_fn=req,\n    )\n\n    assert preorder_id == 20\n    assert tourist_id == 10\n    assert calls == ["get-manager-list", "add-tourist-temp", "create-preorder"]\n    assert payloads["add-tourist-temp"]["manager_id"] == 17\n    preorder = payloads["create-preorder"]\n    assert preorder["preorder_manager_id"] == 17\n    assert preorder["nights_from"] == 10\n    assert preorder["nights_to"] == 10\n    assert preorder["link"] == "https://vk.com/id424242"\n    assert "Вылет: Архангельск" in preorder["comment"]\n    assert "Источник: VK Bot" in preorder["comment"]\n    assert "Mandarava Resort" in preorder["comment"]\n    assert "ID th-6" in preorder["comment"]\n\n\ndef test_create_preorder_does_not_guess_between_multiple_active_managers():\n    payloads = {}\n\n    def req(method, params):\n        payloads.setdefault(method, []).append(params)\n        if method == "get-manager-list":\n            return {"data": [{"id": 17, "dismissed": 0}, {"id": 18, "dismissed": 0}]}\n        if method == "add-tourist-temp":\n            return {"id": 10}\n        if method == "create-preorder":\n            return {"id": 20}\n        return None\n\n    settings = MDTSettings(enabled=True, mode="preorder", name_prefix="VK")\n    preorder_id, _ = create_preorder(\n        settings, 424242, {"destination": "Таиланд", "people": "2"},\n        "VK chat", "Тест", {"таиланд": 7}, request_fn=req\n    )\n    assert preorder_id == 20\n    assert "manager_id" not in payloads["add-tourist-temp"][0]\n    assert "preorder_manager_id" not in payloads["create-preorder"][0]\n'''
    if insertion_point not in text:
        raise SystemExit("test_shared insertion point missing")
    text = text.replace(insertion_point, test + insertion_point, 1)
path.write_text(text, encoding="utf-8")


# tests/test_vk_mdt_write.py: keep source test, replace live write contract with preorder sequence.
path = Path("tests/test_vk_mdt_write.py")
text = path.read_text(encoding="utf-8")
text = text.replace('monkeypatch.setattr(bot, "MDT_MODE", "lead")', 'monkeypatch.setattr(bot, "MDT_MODE", "preorder")', 1)
old_start = text.index("def test_vk_send_lead_posts_expected_add_lead_payload(monkeypatch):")
old_end = text.index("\n\ndef test_vk_completion_survives_mdt_failure", old_start)
new_test = '''def test_vk_send_preorder_assigns_sole_manager(monkeypatch):\n    _enable_mdt(monkeypatch)\n    captured = []\n\n    class Response:\n        status_code = 200\n\n        def __init__(self, payload):\n            self.payload = payload\n\n        def raise_for_status(self):\n            return None\n\n        def json(self):\n            return self.payload\n\n    def fake_post(url, data=None, timeout=None, **kwargs):\n        params = json.loads(data["params"])\n        captured.append((url, params, timeout, kwargs))\n        if url.endswith("/api/get-manager-list"):\n            return Response({"data": [{"id": 17, "dismissed": 0, "office_id": 1}]})\n        if url.endswith("/api/add-tourist-temp"):\n            return Response({"id": 10})\n        if url.endswith("/api/create-preorder"):\n            return Response({"id": 20})\n        raise AssertionError(url)\n\n    monkeypatch.setattr(bot.http_session, "post", fake_post)\n\n    ok = bot.send_lead_to_mdt(\n        424242,\n        {\n            "destination": "Таиланд",\n            "origin": "Архангельск",\n            "dates": "15-25 января 2027",\n            "nights": "10",\n            "people": "2",\n            "budget": 270000,\n            "budget_scope": "total",\n            "selected_tour": {\n                "hotel": "Mandarava Resort",\n                "date": "2027-01-15",\n                "nights": 10,\n                "meal": "BB",\n                "price": 245000,\n                "tour_id": "tv-42",\n            },\n        },\n        "VK (чат id 424242) · Тестовый клиент",\n        "Тестовый клиент",\n    )\n\n    assert ok is True\n    assert [item[0].rsplit("/", 1)[-1] for item in captured] == [\n        "get-manager-list", "add-tourist-temp", "create-preorder"\n    ]\n    for _, _, timeout, kwargs in captured:\n        assert timeout == bot.HTTP_TIMEOUT\n        assert kwargs == {}\n\n    tourist = captured[1][1]\n    assert tourist["name"] == "Тестовый клиент"\n    assert tourist["manager_id"] == 17\n\n    preorder = captured[2][1]\n    assert preorder["preorder_manager_id"] == 17\n    assert preorder["country_id1"] == 0  # country cache is lazy and may be empty in this focused test\n    assert preorder["link"] == "https://vk.com/id424242"\n    assert preorder["nights_from"] == 10\n    assert preorder["nights_to"] == 10\n    assert "Вылет: Архангельск" in preorder["comment"]\n    assert "Источник: VK Bot" in preorder["comment"]\n    assert "Mandarava Resort" in preorder["comment"]\n    assert "ID tv-42" in preorder["comment"]\n'''
text = text[:old_start] + new_test + text[old_end:]
path.write_text(text, encoding="utf-8")
