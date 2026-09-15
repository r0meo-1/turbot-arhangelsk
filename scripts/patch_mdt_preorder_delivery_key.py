from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"anchor not found: {label}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "shared/mdt.py",
    '''    if settings.source:\n        comment_parts.append(f"Источник: {settings.source}")\n    selected = info.get("selected_tour")\n''',
    '''    if settings.source:\n        comment_parts.append(f"Источник: {settings.source}")\n    delivery_key = str(info.get("_mdt_delivery_key") or "").strip()\n    if delivery_key:\n        comment_parts.append(f"ID заявки бота: {delivery_key}")\n    selected = info.get("selected_tour")\n''',
    "preorder delivery key comment",
)

replace_once(
    "vk_bot.py",
    '''    try:\n        if lead_id is not None and MDT_MODE == "lead":\n            _deliver_mdt_lead(lead_id)\n        elif MDT_ENABLED and not DEMO_MODE:\n            # preorder/both remain one-shot because their multi-call transaction\n            # cannot be retried safely without server-side idempotency.\n            send_lead_to_mdt(user_id, info, phone, client_name)\n''',
    '''    try:\n        delivery_info = info\n        if lead_id is not None:\n            delivery_info = dict(info)\n            delivery_info["_mdt_delivery_key"] = f"vk-lead-{lead_id}"\n        if lead_id is not None and MDT_MODE == "lead":\n            _deliver_mdt_lead(lead_id)\n        elif MDT_ENABLED and not DEMO_MODE:\n            # preorder/both remain one-shot because their multi-call transaction\n            # cannot be retried safely without server-side idempotency.\n            send_lead_to_mdt(user_id, delivery_info, phone, client_name)\n''',
    "VK preorder delivery info",
)

p = Path("tests/test_vk_mdt_write.py")
text = p.read_text(encoding="utf-8")
needle = '''            "budget_scope": "total",\n            "selected_tour": {\n'''
replacement = '''            "budget_scope": "total",\n            "_mdt_delivery_key": "vk-lead-36",\n            "selected_tour": {\n'''
if '"_mdt_delivery_key": "vk-lead-36"' not in text:
    if needle not in text:
        raise SystemExit("VK MDT test delivery-key payload anchor not found")
    text = text.replace(needle, replacement, 1)
assertion = '''    assert "Источник: VK Bot" in preorder["comment"]\n    assert "Mandarava Resort" in preorder["comment"]\n'''
assertion_new = '''    assert "Источник: VK Bot" in preorder["comment"]\n    assert "ID заявки бота: vk-lead-36" in preorder["comment"]\n    assert "Mandarava Resort" in preorder["comment"]\n'''
if 'assert "ID заявки бота: vk-lead-36" in preorder["comment"]' not in text:
    if assertion not in text:
        raise SystemExit("VK MDT test delivery-key assertion anchor not found")
    text = text.replace(assertion, assertion_new, 1)
p.write_text(text, encoding="utf-8")
