import base64
import json
from pathlib import Path

import pytest

from workflow_engine.db import Repository
from workflow_engine.gmail_pubsub import (
    GmailPubSubReceiver,
    GoogleOidcVerifier,
    parse_pubsub_push,
    persist_pubsub_notification,
)


ROOT = Path(__file__).resolve().parents[1]


def _push(
    *,
    message_id="pubsub-1",
    history_id="123",
):
    data = base64.b64encode(
        json.dumps({
            "emailAddress": (
                "workflow@example.test"
            ),
            "historyId": history_id,
        }).encode("utf-8")
    ).decode("ascii")

    return {
        "message": {
            "messageId": message_id,
            "data": data,
        },
        "subscription": (
            "projects/test/"
            "subscriptions/gmail"
        ),
    }


def test_parse_pubsub_push_keeps_only_delivery_identity():
    parsed = parse_pubsub_push(
        _push()
    )

    assert parsed.message_id == "pubsub-1"
    assert parsed.history_id == "123"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": {}},
        {
            "message": {
                "messageId": "m1",
                "data": "not-base64!",
            }
        },
    ],
)
def test_parse_pubsub_push_rejects_malformed_payload(
    payload,
):
    with pytest.raises(
        ValueError
    ):
        parse_pubsub_push(
            payload
        )


@pytest.mark.asyncio
async def test_pubsub_persistence_is_idempotent(
    tmp_path,
):
    repo = Repository(
        str(tmp_path / "workflow.db")
    )
    await repo.connect()

    try:
        _, inserted = (
            await persist_pubsub_notification(
                repo,
                _push(
                    message_id="m1",
                    history_id="777",
                ),
            )
        )
        assert inserted

        _, inserted = (
            await persist_pubsub_notification(
                repo,
                _push(
                    message_id="m2",
                    history_id="777",
                ),
            )
        )
        assert not inserted

        pending = (
            await repo.pending_gmail_notification()
        )
        assert pending[
            "pubsub_message_id"
        ] == "m1"
        assert pending[
            "history_id"
        ] == "777"
    finally:
        await repo.close()


def test_oidc_verifier_requires_fail_closed_identity():
    with pytest.raises(
        ValueError,
        match="audience",
    ):
        GoogleOidcVerifier(
            audience="",
            service_account=(
                "push@example.test"
            ),
        )

    with pytest.raises(
        ValueError,
        match="service account",
    ):
        GoogleOidcVerifier(
            audience=(
                "https://example.test/"
                "gmail/pubsub"
            ),
            service_account="",
        )


def test_receiver_validates_local_transport_config():
    class Verifier:
        async def verify(
            self,
            headers,
        ):
            return {}

    with pytest.raises(
        ValueError,
        match="path",
    ):
        GmailPubSubReceiver(
            repo=object(),
            verifier=Verifier(),
            path="gmail/pubsub",
        )

    with pytest.raises(
        ValueError,
        match="port",
    ):
        GmailPubSubReceiver(
            repo=object(),
            verifier=Verifier(),
            port=0,
        )


def test_http_receiver_does_not_run_gmail_or_extraction_inline():
    source = (
        ROOT
        / "workflow_engine"
        / "gmail_pubsub.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "GmailSource" not in source
    assert "Engine" not in source
    assert ".fetch(" not in source
    assert "TaskExtractor" not in source
