import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "agent-extension"


def test_agent_extension_manifest_is_mv3_side_panel():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "0.3.0"
    assert manifest["side_panel"]["default_path"] == "sidepanel.html"
    assert "sidePanel" in manifest["permissions"]
    assert "contextMenus" in manifest["permissions"]
    assert manifest["host_permissions"] == ["https://bot.r0meo1.ru/*"]


def test_agent_extension_load_unpacked_preflight():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    referenced_files = {
        manifest["background"]["service_worker"],
        manifest["side_panel"]["default_path"],
    }
    for relative_path in referenced_files:
        path = EXT / relative_path
        assert path.is_file(), f"manifest references missing file: {relative_path}"
        assert path.resolve().is_relative_to(EXT.resolve())

    sidepanel = (EXT / manifest["side_panel"]["default_path"]).read_text(encoding="utf-8")
    local_assets = set(re.findall(r'(?:src|href)=["\']([^"\']+)["\']', sidepanel))
    assert local_assets == {"sidepanel.css", "sidepanel.js"}
    for relative_path in local_assets:
        assert (EXT / relative_path).is_file(), f"side panel references missing file: {relative_path}"

    forbidden_suffixes = {".env", ".key", ".pem", ".p12", ".pfx"}
    forbidden_names = {"credentials.json", "secrets.json"}
    for path in EXT.rglob("*"):
        if path.is_file():
            assert path.suffix.lower() not in forbidden_suffixes
            assert path.name.lower() not in forbidden_names


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
    assert "https://bot.r0meo1.ru/agent-extension/crm/reaction" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/activity" in js
    assert "https://bot.r0meo1.ru/agent-extension/crm/outcome" in js
    assert "followUpOn" in js
    assert "Что делать сегодня" in (EXT / "sidepanel.html").read_text(encoding="utf-8")
    assert "История заявки" in (EXT / "sidepanel.html").read_text(encoding="utf-8")
    assert "/website/lead" not in js


def test_agent_extension_every_static_button_has_js_wiring():
    html = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    js = (EXT / "sidepanel.js").read_text(encoding="utf-8")

    button_ids = re.findall(r'<button[^>]+id="([^"]+)"', html)
    assert button_ids
    for button_id in button_ids:
        assert f'$("{button_id}").addEventListener' in js, button_id


def test_agent_extension_outcome_ui_is_wired_and_bounded():
    html = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    js = (EXT / "sidepanel.js").read_text(encoding="utf-8")

    assert 'id="outcomeStatus"' in html
    assert 'id="outcomeReason" maxlength="500"' in html
    assert 'id="saveOutcome"' in html
    assert 'const CRM_OUTCOME_API = "https://bot.r0meo1.ru/agent-extension/crm/outcome";' in js
    assert "async function saveOutcome()" in js
    assert '["paused", "won", "lost"].includes(status)' in js
    assert '.trim().slice(0, 500)' in js
    assert '$("saveOutcome").addEventListener("click", saveOutcome);' in js
    assert 'timeline.outcome || {}' in js


def test_agent_extension_version_comes_from_manifest_and_pairing_can_be_cleared():
    html = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    js = (EXT / "sidepanel.js").read_text(encoding="utf-8")

    assert 'id="versionBadge"' in html
    assert "chrome.runtime.getManifest()" in js
    assert '"v" + manifest.version' in js
    assert 'id="clearToken"' in html
    assert 'chrome.storage.local.remove("agentToken")' in js
    assert '$("clearToken").addEventListener("click", clearPairing);' in js


def test_agent_extension_initial_connection_errors_are_visible_and_non_secret():
    html = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    js = (EXT / "sidepanel.js").read_text(encoding="utf-8")

    assert 'id="connectionState"' in html
    assert "Promise.allSettled([loadLeads(), loadToday()])" in js
    assert "Привязка недействительна" in js
    assert "CRM временно недоступна" in js
    assert "setConnectionState(message, \"bad\")" in js
    assert 'loadLeads().catch(() => {})' not in js
    assert 'loadToday().catch(() => {})' not in js
