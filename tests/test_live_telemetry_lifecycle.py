import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def run_scenario(scenario):
    script = """
const {{createOwnerSession}} = require('./manager-web/owner-session');
const {{createValidationHooks}} = require('./manager-web/live-validation');
const {{createRuntimeTelemetry}} = require('./manager-web/runtime-telemetry');
(async () => {{
  const frames = [];
  const telemetry = createRuntimeTelemetry({{write: (line) => frames.push(JSON.parse(line))}});
  const hooks = createValidationHooks({{
    ownerSession: createOwnerSession(),
    env: {{LUNA_ALLOW_LIVE_STAGING: 'true', STAGING_HEALTH_ENDPOINTS: 'https://staging.example/health'}},
    telemetry,
    onManualInput: async () => {{
      if ('SCENARIO' === 'reject') throw new Error('owner rejected');
    }},
  }});
  const event = hooks.requestManualCheck('private-key');
  const waiting = hooks.waitForManualInput(event.requestId, {{timeoutMs: 5}});
  if ('SCENARIO' === 'reject') await hooks.submitManualInput(event.requestId, 'synthetic-key');
  const result = await waiting;
  const next = hooks.requestManualCheck('iphone');
  console.log(JSON.stringify({{result, next, status: hooks.getStatus(), frames, pipelineContinued: true}}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
""".replace('SCENARIO', scenario).replace('{{', '{').replace('}}', '}')
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_rejection_emits_rejection_then_rolls_back_to_mock():
    output = run_scenario("reject")
    assert output["result"]["phase"] == "manual-input-rejected"
    assert output["next"]["phase"] == "mock-fallback"
    assert output["status"]["runtime"]["mode"] == "mock"
    assert [frame["phase"] for frame in output["frames"]] == [
        "awaiting-owner-session",
        "manual-input-rejected",
        "mock-fallback",
        "mock-fallback",
    ]
    assert output["pipelineContinued"] is True


def test_timeout_emits_timeout_then_rolls_back_to_mock():
    output = run_scenario("timeout")
    assert output["result"]["phase"] == "manual-input-timeout"
    assert output["next"]["phase"] == "mock-fallback"
    assert output["status"]["runtime"]["mode"] == "mock"
    assert [frame["phase"] for frame in output["frames"]] == [
        "awaiting-owner-session",
        "manual-input-timeout",
        "mock-fallback",
        "mock-fallback",
    ]
    assert output["pipelineContinued"] is True
