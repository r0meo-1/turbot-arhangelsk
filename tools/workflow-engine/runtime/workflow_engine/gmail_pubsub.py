import asyncio
import base64
import json
from dataclasses import dataclass

from aiohttp import web
from google.auth.transport.requests import (
    Request as GoogleRequest,
)
from google.oauth2 import id_token


@dataclass(slots=True)
class PubSubNotification:
    message_id: str
    history_id: str


def parse_pubsub_push(
    payload,
):
    if not isinstance(payload, dict):
        raise ValueError(
            "Pub/Sub payload must be an object"
        )

    message = payload.get(
        "message"
    )

    if not isinstance(message, dict):
        raise ValueError(
            "Pub/Sub message is missing"
        )

    message_id = str(
        message.get("messageId")
        or message.get("message_id")
        or ""
    ).strip()

    if (
        not message_id
        or len(message_id) > 256
    ):
        raise ValueError(
            "Invalid Pub/Sub message id"
        )

    encoded = message.get("data")

    if not isinstance(encoded, str):
        raise ValueError(
            "Pub/Sub message data is missing"
        )

    padded = (
        encoded
        + "=" * (-len(encoded) % 4)
    )

    try:
        decoded = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        ).decode(
            "utf-8"
        )
        event = json.loads(
            decoded
        )
    except Exception as exc:
        raise ValueError(
            "Invalid Pub/Sub message data"
        ) from exc

    if not isinstance(event, dict):
        raise ValueError(
            "Gmail notification must be an object"
        )

    history_id = str(
        event.get(
            "historyId",
            "",
        )
    ).strip()

    if not history_id.isdigit():
        raise ValueError(
            "Invalid Gmail historyId"
        )

    return PubSubNotification(
        message_id=message_id,
        history_id=str(
            int(history_id)
        ),
    )


async def persist_pubsub_notification(
    repo,
    payload,
):
    notification = (
        parse_pubsub_push(
            payload
        )
    )

    inserted = (
        await repo.record_gmail_notification(
            pubsub_message_id=(
                notification.message_id
            ),
            history_id=(
                notification.history_id
            ),
        )
    )

    return notification, inserted


class GoogleOidcVerifier:
    def __init__(
        self,
        *,
        audience,
        service_account,
    ):
        self.audience = str(
            audience
        ).strip()
        self.service_account = str(
            service_account
        ).strip()

        if not self.audience:
            raise ValueError(
                "Pub/Sub OIDC audience is required"
            )

        if not self.service_account:
            raise ValueError(
                "Pub/Sub service account is required"
            )

    async def verify(
        self,
        headers,
    ):
        authorization = str(
            headers.get(
                "Authorization",
                "",
            )
        ).strip()

        if not authorization.startswith(
            "Bearer "
        ):
            raise web.HTTPUnauthorized()

        token = authorization[
            len("Bearer "):
        ].strip()

        if not token:
            raise web.HTTPUnauthorized()

        try:
            claims = await asyncio.to_thread(
                id_token.verify_oauth2_token,
                token,
                GoogleRequest(),
                self.audience,
            )
        except Exception as exc:
            raise web.HTTPUnauthorized() from exc

        if (
            claims.get("email")
            != self.service_account
            or claims.get(
                "email_verified"
            ) is not True
        ):
            raise web.HTTPForbidden()

        return claims


class GmailPubSubReceiver:
    def __init__(
        self,
        *,
        repo,
        verifier,
        host="127.0.0.1",
        port=8091,
        path="/gmail/pubsub",
    ):
        self.repo = repo
        self.verifier = verifier
        self.host = str(
            host
        ).strip()
        self.port = int(
            port
        )
        self.path = str(
            path
        ).strip()
        self.runner = None

        if (
            not self.path.startswith("/")
            or "?" in self.path
        ):
            raise ValueError(
                "Invalid Pub/Sub receiver path"
            )

        if not (
            1 <= self.port <= 65535
        ):
            raise ValueError(
                "Invalid Pub/Sub receiver port"
            )

    async def handle(
        self,
        request,
    ):
        await self.verifier.verify(
            request.headers
        )

        try:
            payload = await request.json()
            await persist_pubsub_notification(
                self.repo,
                payload,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest() from exc

        return web.Response(
            status=204
        )

    async def run(self):
        app = web.Application(
            client_max_size=64 * 1024
        )
        app.router.add_post(
            self.path,
            self.handle,
        )

        self.runner = web.AppRunner(
            app,
            access_log=None,
        )
        await self.runner.setup()

        site = web.TCPSite(
            self.runner,
            self.host,
            self.port,
        )
        await site.start()

        try:
            await asyncio.Event().wait()
        finally:
            await self.runner.cleanup()
            self.runner = None
