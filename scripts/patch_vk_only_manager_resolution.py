from pathlib import Path

path = Path("shared/mdt.py")
text = path.read_text(encoding="utf-8")
old = '''    name = client_name or f"{settings.name_prefix} {chat_id}"\n    manager_id = resolve_manager_id(settings, request_fn, log=log)\n    tourist_id = add_tourist_temp(\n'''
new = '''    name = client_name or f"{settings.name_prefix} {chat_id}"\n    manager_id = None\n    if settings.manager_ids or settings.name_prefix.strip().casefold() == "vk":\n        manager_id = resolve_manager_id(settings, request_fn, log=log)\n    tourist_id = add_tourist_temp(\n'''
if new not in text:
    if old not in text:
        raise SystemExit("manager resolution anchor not found")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
