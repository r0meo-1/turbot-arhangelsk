import asyncio
import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import EmailMessage


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


@dataclass(slots=True)
class GmailBatch:
    messages: list[EmailMessage]
    next_history_id: str
    mode: str


@dataclass(slots=True)
class GmailWatch:
    history_id: str
    expiration_ms: int


def decode_payload(payload):
    parts = payload.get("parts")

    if parts:
        return "\n".join(
            filter(
                None,
                [
                    decode_payload(p)
                    for p in parts
                ],
            )
        )

    mime = payload.get("mimeType", "")
    raw = payload.get("body", {}).get("data")

    if not raw:
        return ""

    padded = raw + "=" * (-len(raw) % 4)

    try:
        text = base64.urlsafe_b64decode(
            padded
        ).decode(
            "utf-8",
            errors="replace",
        )
    except Exception:
        return ""

    if mime == "text/html":
        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
        )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


class GmailSource:
    def __init__(
        self,
        token_file: str,
        query: str,
    ):
        creds = Credentials.from_authorized_user_file(
            token_file,
            SCOPES,
        )

        self.service = build(
            "gmail",
            "v1",
            credentials=creds,
            cache_discovery=False,
        )

        self.query = query

    async def start_watch(
        self,
        topic_name,
    ):
        return await asyncio.to_thread(
            self._start_watch_sync,
            topic_name,
        )

    def _start_watch_sync(
        self,
        topic_name,
    ):
        topic = str(
            topic_name
        ).strip()

        parts = topic.split("/")

        if (
            len(parts) != 4
            or parts[0] != "projects"
            or parts[2] != "topics"
            or not parts[1]
            or not parts[3]
        ):
            raise ValueError(
                "Invalid Gmail Pub/Sub topic name"
            )

        result = (
            self.service.users()
            .watch(
                userId="me",
                body={
                    "topicName": topic,
                },
            )
            .execute()
        )

        history_id = str(
            result.get(
                "historyId",
                "",
            )
        ).strip()
        expiration = str(
            result.get(
                "expiration",
                "",
            )
        ).strip()

        if (
            not history_id.isdigit()
            or not expiration.isdigit()
        ):
            raise RuntimeError(
                "Gmail watch returned invalid state"
            )

        return GmailWatch(
            history_id=str(
                int(history_id)
            ),
            expiration_ms=int(
                expiration
            ),
        )

    async def fetch(
        self,
        start_history_id=None,
        page_size=100,
    ):
        return await asyncio.to_thread(
            self._fetch_sync,
            start_history_id,
            page_size,
        )

    def _fetch_sync(
        self,
        start_history_id,
        page_size,
    ):
        page_size = max(
            1,
            min(int(page_size), 500),
        )

        if start_history_id:
            try:
                return self._history_sync(
                    str(start_history_id),
                    page_size,
                )
            except HttpError as exc:
                if getattr(
                    exc.resp,
                    "status",
                    None,
                ) != 404:
                    raise

                return self._bootstrap_sync(
                    page_size,
                    mode="recovery",
                )

        return self._bootstrap_sync(
            page_size,
            mode="bootstrap",
        )

    def _bootstrap_sync(
        self,
        page_size,
        mode,
    ):
        profile = (
            self.service.users()
            .getProfile(userId="me")
            .execute()
        )

        checkpoint = str(
            profile.get("historyId", "")
        ).strip()

        if not checkpoint.isdigit():
            raise RuntimeError(
                "Gmail profile returned no valid historyId"
            )

        message_ids = []
        seen = set()
        page_token = None

        while True:
            kwargs = {
                "userId": "me",
                "q": self.query,
                "maxResults": page_size,
            }

            if page_token:
                kwargs["pageToken"] = page_token

            result = (
                self.service.users()
                .messages()
                .list(**kwargs)
                .execute()
            )

            for item in result.get(
                "messages",
                [],
            ):
                message_id = item.get("id")

                if (
                    message_id
                    and message_id not in seen
                ):
                    seen.add(message_id)
                    message_ids.append(
                        message_id
                    )

            page_token = result.get(
                "nextPageToken"
            )

            if not page_token:
                break

        return GmailBatch(
            messages=self._load_messages(
                message_ids
            ),
            next_history_id=checkpoint,
            mode=mode,
        )

    def _history_sync(
        self,
        start_history_id,
        page_size,
    ):
        message_ids = []
        seen = set()
        page_token = None
        checkpoint = str(
            start_history_id
        )

        while True:
            kwargs = {
                "userId": "me",
                "startHistoryId": (
                    start_history_id
                ),
                "historyTypes": [
                    "messageAdded",
                ],
                "maxResults": page_size,
            }

            if page_token:
                kwargs["pageToken"] = page_token

            result = (
                self.service.users()
                .history()
                .list(**kwargs)
                .execute()
            )

            result_history_id = str(
                result.get(
                    "historyId",
                    checkpoint,
                )
            ).strip()

            if result_history_id.isdigit():
                checkpoint = result_history_id

            for history in result.get(
                "history",
                [],
            ):
                for added in history.get(
                    "messagesAdded",
                    [],
                ):
                    message_id = (
                        added.get(
                            "message",
                            {},
                        ).get("id")
                    )

                    if (
                        message_id
                        and message_id not in seen
                    ):
                        seen.add(message_id)
                        message_ids.append(
                            message_id
                        )

            page_token = result.get(
                "nextPageToken"
            )

            if not page_token:
                break

        return GmailBatch(
            messages=self._load_messages(
                message_ids
            ),
            next_history_id=checkpoint,
            mode="history",
        )

    def _load_messages(
        self,
        message_ids,
    ):
        output = []

        for message_id in message_ids:
            try:
                raw = (
                    self.service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=message_id,
                        format="full",
                    )
                    .execute()
                )
            except HttpError as exc:
                if getattr(
                    exc.resp,
                    "status",
                    None,
                ) == 404:
                    continue
                raise

            payload = raw.get(
                "payload",
                {},
            )

            headers = {
                h["name"].lower(): h["value"]
                for h in payload.get(
                    "headers",
                    [],
                )
                if h.get("name")
            }

            ts = int(
                raw.get(
                    "internalDate",
                    "0",
                )
            )

            output.append(
                EmailMessage(
                    source="gmail",
                    external_id=raw["id"],
                    thread_id=raw.get(
                        "threadId",
                        "",
                    ),
                    sender=headers.get(
                        "from",
                        "",
                    ),
                    subject=headers.get(
                        "subject",
                        "",
                    ),
                    body=decode_payload(
                        payload
                    ),
                    received_at=datetime.fromtimestamp(
                        ts / 1000,
                        timezone.utc,
                    ),
                )
            )

        return output
