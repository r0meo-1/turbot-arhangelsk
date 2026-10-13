"""Exercise collection in fresh interpreters, before either bot is cached."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("order", [
    ["test_vk_mdt_write.py", "test_website_mdt_preorder.py", "test_vk_bot.py", "test_bot.py"],
    ["test_website_mdt_preorder.py", "test_vk_mdt_write.py", "test_bot.py", "test_vk_bot.py"],
    ["test_bot.py", "test_vk_bot.py", "test_vk_mdt_write.py", "test_website_mdt_preorder.py"],
])
def test_platform_import_order_keeps_databases_and_configuration_isolated(tmp_path, order):
    root = Path(__file__).resolve().parents[1]
    main_sentinel = tmp_path / "configured-main.sqlite"
    vk_sentinel = tmp_path / "configured-vk.sqlite"
    for path in (main_sentinel, vk_sentinel):
        path.write_bytes(b"configured database must remain untouched")
    env = dict(os.environ, DATABASE_PATH=str(main_sentinel), VK_DATABASE_PATH=str(vk_sentinel),
               CONSENT_MODE="soft", DEMO_MODE="true", VK_DEMO_MODE="true",
               SYNC_COMPLETION="false", WEBSITE_LEAD_WORKER_ENABLED="true")
    script = '''
import runpy, sys
from contextlib import closing
import sqlite3
runpy.run_path("tests/conftest.py")
for name in sys.argv[1:]:
    runpy.run_path("tests/" + name)
import bot, vk_bot, website_app
assert bot.DATABASE_PATH != vk_bot.DATABASE_PATH
assert vk_bot.MAIN_DATABASE_PATH == bot.DATABASE_PATH
assert website_app._VK_DATABASE_PATH == vk_bot.DATABASE_PATH
assert bot.CONSENT_MODE == vk_bot.CONSENT_MODE == "strict"
assert bot.SYNC_COMPLETION and vk_bot.SYNC_COMPLETION
assert not bot.DEMO_MODE and not vk_bot.DEMO_MODE
assert not website_app._WORKER_ENABLED
info = {"state": "review", "destination": "Турция", "people": "2", "budget": 200000}
for module in (bot, vk_bot):
    module.set_session(424242, info)
    lead_id = module.save_lead(424242, info, "+70000000000")
    assert lead_id > 0
    with closing(sqlite3.connect(module.DATABASE_PATH)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert module.get_session(424242)["destination"] == "Турция"
'''
    result = subprocess.run([sys.executable, "-c", script, *order], cwd=root, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    for path in (main_sentinel, vk_sentinel):
        assert path.read_bytes() == b"configured database must remain untouched"
