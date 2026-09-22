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
    assert "private" not in result.stdout.lower()


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
