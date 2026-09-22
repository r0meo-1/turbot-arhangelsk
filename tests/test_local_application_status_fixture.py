import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "application_status.json"


def test_application_status_is_explicitly_local_and_unsubmitted():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["source"] == "local-fixture"
    assert data["status"] == "draft"
    assert data["submitted"] is False
    assert data["employer_response"] is None
