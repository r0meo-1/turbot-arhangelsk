import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import EmailMessage, TaskCandidate, ValidationResult


def utcnow():
    return datetime.now(timezone.utc)


def normalize_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())


def priority_band(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 30:
        return "normal"
    return "low"


class PriorityEngine:
    KEYWORDS = {
        "urgent": 30,
        "asap": 25,
        "production down": 35,
        "incident": 25,
        "security": 25,
        "deadline": 15,
        "today": 20,
        "please review": 10,
        "please send": 10,
        "need you to": 15,
    }

    def score(self, message: EmailMessage) -> int:
        text = (
            f"{message.sender} "
            f"{message.subject} "
            f"{message.body}"
        ).casefold()

        score = 0

        for keyword, weight in self.KEYWORDS.items():
            if keyword in text:
                score += weight

        for pattern, weight in (
            (r"\bсрочно\b", 30),
            (r"\bинцидент\b", 25),
            (r"\b(?:дедлайн|срок)\b", 15),
            (r"\bсегодня\b", 20),
        ):
            if re.search(pattern, text):
                score += weight
        if TaskExtractor.RU_ACTION_RE.search(
            f"{message.subject}. {message.body}"
        ):
            score += 15

        if re.search(
            r"\b(please|need|must|send|review|prepare|fix|deploy|update)\b",
            text,
        ):
            score += 15

        return min(score, 100)


class TaskExtractor:
    IGNORE_SUBJECT_RE = re.compile(
        r"\b(?:"
        r"verification code|"
        r"security code|"
        r"sign[- ]in code|"
        r"login code|"
        r"one[- ]time (?:password|code)|"
        r"otp code|"
        r"код (?:для )?(?:входа|подтверждения|авторизации)|"
        r"одноразовый (?:пароль|код)"
        r")\b",
        re.I,
    )

    ACTION_RE = re.compile(
        r"\b(?:"
        r"action required|"
        r"please\s+(?:review|send|prepare|fix|deploy|update|complete|"
        r"confirm|approve|respond|reply|renew|rotate|configure|check)|"
        r"(?:you\s+)?(?:need|must|have)\s+to\s+(?:review|send|prepare|"
        r"fix|deploy|update|complete|confirm|approve|respond|reply|"
        r"renew|rotate|configure|check|set\s+up)"
        r")\b",
        re.I,
    )

    RU_ACTION_RE = re.compile(
        r"(?:\b(?:пожалуйста[,\s]+|просим\s+(?:вас\s+)?|"
        r"(?<!не )(?<!нет )(?:(?:вам\s+)?(?:нужно|необходимо)|требуется)\s+)"
        r"(?:проверить|отправить|подготовить|исправить|обновить|"
        r"подтвердить|ответить|продлить|настроить|завершить|согласовать|"
        r"проверьте|отправьте|подготовьте|исправьте|обновите|"
        r"подтвердите|ответьте|продлите|настройте|завершите|согласуйте)\b|"
        r"(?:^|[.!?\n])\s*(?:проверьте|отправьте|подготовьте|исправьте|"
        r"обновите|подтвердите|ответьте|продлите|настройте|завершите|"
        r"согласуйте)\b)",
        re.I,
    )

    DEADLINE_RE = re.compile(
        r"\b(?:"
        r"deadline|"
        r"due(?:\s+date)?|"
        r"by\s+(?:today|tomorrow|20\d\d-\d\d-\d\d|"
        r"\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)"
        r"|срок|дедлайн|до\s+(?:сегодня|завтра|20\d\d-\d\d-\d\d|"
        r"\d{1,2}[./]\d{1,2}(?:[./]\d{4})?)|"
        r"(?:проверить|отправить|подготовить|исправить|обновить|"
        r"подтвердить|ответить|продлить|настроить|завершить|согласовать|"
        r"проверьте|отправьте|подготовьте|исправьте|обновите|"
        r"подтвердите|ответьте|продлите|настройте|завершите|согласуйте)"
        r"\s+(?:сегодня|завтра)"
        r")\b",
        re.I,
    )

    def extract(
        self,
        message: EmailMessage,
        priority_score: int,
    ) -> list[TaskCandidate]:

        subject = message.subject.strip()
        text = f"{subject}. {message.body}"

        # Codes / login messages are events, not work items.
        if self.IGNORE_SUBJECT_RE.search(subject):
            return []

        # Require explicit request/obligation language.
        if not (self.ACTION_RE.search(text) or self.RU_ACTION_RE.search(text)):
            return []

        due_date = self._extract_date(text, message.received_at)

        title = (
            subject
            or self._first_sentence(message.body)
        )

        project = "General"

        raw_key = (
            f"{project.lower()}|"
            f"{normalize_text(title)}"
        )

        return [
            TaskCandidate(
                title=title[:240],
                project=project,
                priority_score=priority_score,
                priority=priority_band(priority_score),
                owner=None,
                due_date=due_date,
                confidence=0.85,
                explicit_deadline_language=bool(
                    self.DEADLINE_RE.search(text)
                ),
                dedupe_key=hashlib.sha256(
                    raw_key.encode()
                ).hexdigest(),
            )
        ]

    @staticmethod
    def _first_sentence(body: str) -> str:
        parts = re.split(r"[.!?\n]", body)

        return next(
            (
                part.strip()
                for part in parts
                if part.strip()
            ),
            "Untitled action",
        )

    @staticmethod
    def _extract_date(
        text: str, received_at: Optional[datetime] = None
    ) -> Optional[str]:
        iso = re.search(
            r"\b20\d\d-\d\d-\d\d\b",
            text,
        )

        if iso:
            try:
                return datetime.fromisoformat(
                    iso.group(0)
                ).date().isoformat()
            except ValueError:
                return None

        numeric = re.search(
            r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d)\b",
            text,
        )

        if numeric:
            day, month, year = map(
                int,
                numeric.groups(),
            )

            try:
                return datetime(
                    year,
                    month,
                    day,
                ).date().isoformat()
            except ValueError:
                return None

        lowered = text.casefold()
        # Replayed mail keeps the date of the original request, not processing.
        reference_date = (received_at or utcnow()).date()

        if "tomorrow" in lowered:
            return (
                reference_date
                + timedelta(days=1)
            ).isoformat()

        if "today" in lowered:
            return reference_date.isoformat()

        russian_deadline = re.search(
            r"\b(?:до|срок\s*[:—-]?|дедлайн\s*[:—-]?|"
            r"проверить|отправить|подготовить|исправить|обновить|"
            r"подтвердить|ответить|продлить|настроить|завершить|согласовать|"
            r"проверьте|отправьте|подготовьте|исправьте|обновите|"
            r"подтвердите|ответьте|продлите|настройте|завершите|согласуйте)"
            r"\s+(сегодня|завтра)\b", lowered,
        )
        if russian_deadline:
            offset = 1 if russian_deadline.group(1) == "завтра" else 0
            return (reference_date + timedelta(days=offset)).isoformat()

        return None


class Validator:
    def validate(
        self,
        candidate: TaskCandidate,
        existing: Optional[dict],
    ) -> ValidationResult:

        if candidate.confidence < 0.60:
            return ValidationResult(
                "REVIEW",
                "low extraction confidence",
            )

        if (
            candidate.explicit_deadline_language
            and candidate.due_date is None
        ):
            return ValidationResult(
                "REVIEW",
                "deadline mentioned but date unresolved",
            )

        if not existing:
            return ValidationResult(
                "ACCEPT",
                "new task",
            )

        old_due = existing.get("due_date")
        new_due = candidate.due_date

        if old_due and new_due and old_due != new_due:
            return ValidationResult(
                "REVIEW",
                f"deadline conflict: {old_due} vs {new_due}",
            )

        return ValidationResult(
            "MERGE",
            "compatible task",
        )
