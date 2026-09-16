from pathlib import Path

path = Path("vk_bot.py")
text = path.read_text(encoding="utf-8")

import_anchor = "import os\nimport re\nimport json\n"
if text.count(import_anchor) != 1:
    raise SystemExit("expected import anchor once")
text = text.replace(import_anchor, "import os\nimport re\nimport json\nimport base64\n", 1)

config_anchor = '''if TOURVISOR_ENABLED and not TOURVISOR_TOKEN:
    logger.warning("VK Tourvisor requested but TOURVISOR_TOKEN is empty; disabling live search")
    TOURVISOR_ENABLED = False
'''
config_replacement = config_anchor + '''

def _tourvisor_jwt_expired(token: str) -> bool:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        payload_raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_raw).decode("utf-8"))
        exp = payload.get("exp")
        return isinstance(exp, (int, float)) and float(exp) <= time.time()
    except Exception:
        return False


if TOURVISOR_ENABLED and _tourvisor_jwt_expired(TOURVISOR_TOKEN):
    logger.warning("VK Tourvisor JWT is expired; disabling live search UI")
    TOURVISOR_ENABLED = False
'''
if text.count(config_anchor) != 1:
    raise SystemExit("expected Tourvisor config anchor once")
text = text.replace(config_anchor, config_replacement, 1)
path.write_text(text, encoding="utf-8")

test_path = Path("tests/test_vk_live_tour_safety.py")
tests = test_path.read_text(encoding="utf-8")
addition = '''

def test_expired_tourvisor_jwt_disables_live_search_ui():
    import base64
    import json
    import time

    source = Path("vk_bot.py").read_text(encoding="utf-8")
    assert "if TOURVISOR_ENABLED and _tourvisor_jwt_expired(TOURVISOR_TOKEN):" in source
    assert 'logger.warning("VK Tourvisor JWT is expired; disabling live search UI")' in source

    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) - 60}).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    namespace = {}
    helper_source = source[
        source.index("def _tourvisor_jwt_expired"):
        source.index("if TOURVISOR_ENABLED and _tourvisor_jwt_expired")
    ]
    exec(helper_source, {"json": json, "base64": base64, "time": time}, namespace)
    assert namespace["_tourvisor_jwt_expired"](token) is True
'''
if "test_expired_tourvisor_jwt_disables_live_search_ui" not in tests:
    test_path.write_text(tests.rstrip() + addition + "\n", encoding="utf-8")
