import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "agent-extension"


def test_agent_extension_manifest_is_mv3_side_panel():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["side_panel"]["default_path"] == "sidepanel.html"
    assert "sidePanel" in manifest["permissions"]
    assert "contextMenus" in manifest["permissions"]
    assert manifest["host_permissions"] == ["https://bot.r0meo1.ru/*"]


def test_agent_extension_has_no_embedded_secrets():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in EXT.iterdir()
        if path.suffix in {".json", ".js", ".html", ".css", ".md"}
    )
    assert "TOURVISOR_TOKEN" not in source
    assert "SLETAT_PASSWORD" not in source
    assert "MDT_API_KEY" not in source
    assert "AGENT_EXTENSION_TOKEN=<" not in source
    assert "/agentdesk" in source
    assert "Authorization" in source


def test_agent_extension_points_only_to_protected_agent_endpoint():
    js = (EXT / "sidepanel.js").read_text(encoding="utf-8")
    assert "https://bot.r0meo1.ru/agent-extension/lead" in js
    assert "https://bot.r0meo1.ru/agent-extension/leads" in js
    assert "https://bot.r0meo1.ru/agent-extension/status" in js
    assert "https://bot.r0meo1.ru/agent-extension/export.csv" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/today" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/timeline" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/quote" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/task" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/activity" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/outcome" in js
    assert "followUpOn" in js
    assert "Что делать сегодня" in (EXT / "sidepanel.html").read_text(encoding="utf-8")
    assert "История заявки" in (EXT / "sidepanel.html").read_text(encoding="utf-8")
    assert "/website/lead" not in js
