import json
from pathlib import Path
import subprocess


FIXTURE = Path(__file__).parent / "fixtures" / "application_status.json"


def test_application_status_is_explicitly_local_and_unsubmitted():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["source"] == "local-fixture"
    assert data["status"] == "draft"
    assert data["submitted"] is False
    assert data["employer_response"] is None


def test_owner_session_mock_state_matches_fixture_without_loading_a_key():
    root = Path(__file__).parents[1]
    result = subprocess.run(
        ["node", "-e", "console.log(JSON.stringify(require('./manager-web/owner-session').createOwnerSession().getState()))"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    state = json.loads(result.stdout)
    assert state == {
        "mode": "mock",
        "status": "draft",
        "submitted": False,
        "employerResponse": None,
        "keyPresent": False,
    }
    assert "temporary-private-key" not in result.stdout


def test_owner_session_live_path_requires_explicit_connector_and_scrubs_key():
    root = Path(__file__).parents[1]
    script = """
const {createOwnerSession} = require('./manager-web/owner-session');
let observed = '';
const session = createOwnerSession({
  mode: 'live',
  liveConnector: async (key) => {
    observed = key.toString('utf8');
    return {status: 'http-204'};
  },
});
session.verifyLive('temporary-private-key').then((state) => {
  console.log(JSON.stringify({state, observed}));
});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["observed"] == "temporary-private-key"
    assert output["state"] == {
        "mode": "live",
        "status": "verified-locally",
        "submitted": False,
        "employerResponse": "http-204",
        "keyPresent": False,
    }


def test_live_connector_requires_https_and_an_exact_allowlisted_host():
    root = Path(__file__).parents[1]
    script = """
const {createLiveConnector} = require('./manager-web/owner-session');
const attempts = [];
for (const [name, endpoint, allowedHosts] of [
  ['http', 'http://owner.example/check', 'owner.example'],
  ['credentials', 'https://user:pass@owner.example/check', 'owner.example'],
  ['empty-allowlist', 'https://owner.example/check', ''],
  ['suffix-confusion', 'https://owner.example.evil.test/check', 'owner.example'],
  ['port-confusion', 'https://owner.example:444/check', 'owner.example'],
]) {
  let fetchCalls = 0;
  try {
    createLiveConnector({endpoint, allowedHosts, fetchImpl: async () => { fetchCalls += 1; }});
    attempts.push({name, accepted: true, fetchCalls});
  } catch (error) {
    attempts.push({name, accepted: false, fetchCalls, error: error.message});
  }
}
let captured = null;
const connector = createLiveConnector({
  endpoint: 'https://owner.example/check',
  allowedHosts: ' other.example, OWNER.EXAMPLE ',
  fetchImpl: async (url, options) => {
    captured = {url, method: options.method, authorization: options.headers.Authorization};
    return {ok: true, status: 204};
  },
});
connector(Buffer.from('temporary-private-key')).then((result) => {
  console.log(JSON.stringify({attempts, captured, result}));
});
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=root, check=True, capture_output=True, text=True,
    )
    output = json.loads(result.stdout)
    assert all(not attempt["accepted"] for attempt in output["attempts"])
    assert all(attempt["fetchCalls"] == 0 for attempt in output["attempts"])
    assert output["captured"] == {
        "url": "https://owner.example/check",
        "method": "POST",
        "authorization": "Bearer temporary-private-key",
    }
    assert output["result"] == {"status": "http-204"}


def test_live_validation_hook_emits_placeholder_without_secret_or_fake_result():
    root = Path(__file__).parents[1]
    script = """
const {createOwnerSession} = require('./manager-web/owner-session');
const {createValidationHooks} = require('./manager-web/live-validation');
const events = [];
const hooks = createValidationHooks({
  ownerSession: createOwnerSession(),
  env: {LUNA_ALLOW_LIVE_STAGING: 'true'},
  onManualIntervention: (event) => events.push(event),
});
console.log(JSON.stringify({event: hooks.requestManualCheck('iphone', 'Safari check'), status: hooks.getStatus(), events}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["event"]["phase"] == "awaiting-owner-session"
    assert output["event"]["requiresManualInput"] is True
    assert output["status"]["iphone"] == "not-verified"
    assert output["status"]["privateKey"] == "not-provided"
    assert "temporary-private-key" not in result.stdout


def test_manual_input_transition_is_consumed_in_memory_and_timeout_is_explicit():
    root = Path(__file__).parents[1]
    script = """
const {createOwnerSession} = require('./manager-web/owner-session');
const {createValidationHooks} = require('./manager-web/live-validation');
let seen = '';
const hooks = createValidationHooks({
  ownerSession: createOwnerSession(),
  env: {LUNA_ALLOW_LIVE_STAGING: 'true'},
  onManualInput: async ({inputBuffer}) => { seen = inputBuffer.toString('utf8'); },
});
(async () => {
  const event = hooks.requestManualCheck('private-key');
  const waiting = hooks.waitForManualInput(event.requestId, {timeoutMs: 100});
  await hooks.submitManualInput(event.requestId, 'temporary-private-key');
  const consumed = await waiting;
  const timeoutEvent = hooks.requestManualCheck('iphone');
  const timeout = await hooks.waitForManualInput(timeoutEvent.requestId, {timeoutMs: 1});
  console.log(JSON.stringify({consumed, timeout, seen}));
})();
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["consumed"]["phase"] == "manual-input-consumed"
    assert output["timeout"]["phase"] == "manual-input-timeout"
    assert output["seen"] == "temporary-private-key"


def test_staging_gate_falls_back_without_network_and_probe_is_head_only():
    root = Path(__file__).parents[1]
    script = """
const {main} = require('./tools/check_staging_readiness');
(async () => {
  let calls = 0;
  const fetchImpl = async (_url, options) => {
    calls += 1;
    if (options.method !== 'HEAD' || options.body || options.headers.Authorization) throw new Error('unsafe probe');
    return {status: 204, ok: true};
  };
  const mock = await main({LUNA_ALLOW_LIVE_STAGING: 'false', STAGING_HEALTH_ENDPOINTS: 'https://example.test/health'}, fetchImpl);
  const live = await main({LUNA_ALLOW_LIVE_STAGING: 'true', STAGING_HEALTH_ENDPOINTS: 'https://example.test/health'}, fetchImpl);
  console.log(JSON.stringify({mock, live, calls}));
})();
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)
    assert output["mock"] == {"mode": "mock", "networkAttempted": False, "checks": [], "configError": None}
    assert output["live"]["networkAttempted"] is True
    assert output["live"]["checks"][0]["ok"] is True
    assert output["calls"] == 1


def test_live_input_listener_and_telemetry_never_print_payload():
    root = Path(__file__).parents[1]
    script = """
const {Readable} = require('node:stream');
const {listenForManualInput} = require('./manager-web/live-validation');
const {createRuntimeTelemetry} = require('./manager-web/runtime-telemetry');
(async () => {
  let received = '';
  let screen = '';
  await listenForManualInput({
    requestId: 'req-1',
    input: Readable.from(['temporary-secret\\n']),
    output: {write: (value) => { screen += value; }},
    submit: async (buffer) => { received = buffer.toString('utf8'); },
  });
  let telemetry = '';
  const log = createRuntimeTelemetry({write: (line) => { telemetry += line; }});
  log.emit('manual-input-consumed', {requestId: 'req-1', check: 'private-key', secret: received});
  console.log(JSON.stringify({received, screen, telemetry}));
})();
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=root, check=True, capture_output=True, text=True,
    )
    output = json.loads(result.stdout)
    assert output["received"] == "temporary-secret"
    assert "temporary-secret" not in output["screen"]
    assert "temporary-secret" not in output["telemetry"]
    assert output["telemetry"].count('manual-input-consumed') == 1
