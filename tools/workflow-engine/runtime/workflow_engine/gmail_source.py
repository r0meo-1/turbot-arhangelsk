import asyncio
import base64
import re
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import EmailMessage


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


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

    async def fetch(self, limit=50):
        return await asyncio.to_thread(
            self._fetch_sync,
            limit,
        )

    def _fetch_sync(self, limit):
        try:
            result = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=self.query,
                    maxResults=limit,
                )
                .execute()
            )

            output = []

            for item in result.get(
                "messages",
                [],
            ):
                raw = (
                    self.service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=item["id"],
                        format="full",
                    )
                    .execute()
                )

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

        except HttpError:
            raise
