from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class EmailMessage:
    source: str
    external_id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime


@dataclass(slots=True)
class TaskCandidate:
    title: str
    project: str
    priority_score: int
    priority: str
    owner: Optional[str]
    due_date: Optional[str]
    confidence: float
    explicit_deadline_language: bool
    dedupe_key: str


@dataclass(slots=True)
class ValidationResult:
    action: str
    reason: str
