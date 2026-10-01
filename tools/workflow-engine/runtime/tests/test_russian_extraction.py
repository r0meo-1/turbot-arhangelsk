from datetime import datetime, timezone

import pytest

from workflow_engine.logic import PriorityEngine, TaskExtractor, Validator
from workflow_engine.models import EmailMessage


def message(subject="Рабочее поручение", body=""):
    return EmailMessage(
        source="gmail", external_id="synthetic", thread_id="synthetic",
        sender="manager@example.test", subject=subject, body=body,
        received_at=datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("body", [
    "Пожалуйста, проверьте договор до 02.10.2026.",
    "Просим вас подтвердить получение заявки.",
    "Вам необходимо подготовить отчёт.",
    "Нужно отправить документы.",
    "Требуется обновить данные.",
    "Отправьте отчёт.",
])
def test_russian_explicit_requests_create_candidates(body):
    email = message(body=body)
    score = PriorityEngine().score(email)
    candidates = TaskExtractor().extract(email, score)
    assert len(candidates) == 1
    assert candidates[0].title == email.subject
    assert candidates[0].priority_score >= 15


@pytest.mark.parametrize("subject,body", [
    ("Free Tier заканчивается завтра", "Сервер будет переведен на платный тариф."),
    ("Ваш запрос принят", "Заявка зарегистрирована, ожидайте ответа."),
    ("Новости", "Мы подготовили отчёт и отправили документы."),
    ("Работа", "Не нужно отправить документы."),
    ("Работа", "Не требуется обновить данные."),
    ("Код для входа", "Пожалуйста, подтвердите вход. Код 123456."),
    ("Одноразовый пароль", "Подтвердите авторизацию."),
    ("Код подтверждения", "Пожалуйста, проверьте код."),
])
def test_notifications_negated_requests_and_login_codes_are_not_tasks(subject, body):
    assert TaskExtractor().extract(message(subject, body), 50) == []


@pytest.mark.parametrize("body,expected", [
    ("Пожалуйста, отправьте отчёт до 02.10.2026.", "2026-10-02"),
    ("Отправьте завтра отчёт.", "2026-10-02"),
    ("Просим отправить сегодня отчёт.", "2026-10-01"),
    ("Проверьте договор. Срок: завтра.", "2026-10-02"),
    ("Please send the report tomorrow.", "2026-10-02"),
])
def test_deadlines_use_original_message_date_even_on_replay(body, expected):
    extractor = TaskExtractor()
    candidate = extractor.extract(message(body=body), 50)[0]
    assert candidate.due_date == expected
    assert extractor.extract(message(body=body), 50)[0].dedupe_key == candidate.dedupe_key
    assert Validator().validate(candidate, None).action == "ACCEPT"


@pytest.mark.parametrize("body", [
    "Пожалуйста, отправьте отчёт до 31.02.2026.",
    "Проверьте договор. Срок: конец месяца.",
    "Просим ответить до 02.10.",
])
def test_unresolved_russian_deadlines_require_review(body):
    candidate = TaskExtractor().extract(message(body=body), 50)[0]
    assert candidate.due_date is None
    assert Validator().validate(candidate, None).action == "REVIEW"


def test_unrelated_tomorrow_is_not_russian_due_date():
    candidate = TaskExtractor().extract(message(body=(
        "Просим проверить договор. Завтра офис закрыт."
    )), 50)[0]
    assert candidate.due_date is None
    assert not candidate.explicit_deadline_language


@pytest.mark.asyncio
async def test_russian_mail_replay_keeps_one_durable_task_and_outbox(tmp_path):
    from workflow_engine.db import Repository
    from workflow_engine.service import Engine

    repo = Repository(str(tmp_path / "synthetic.db"))
    await repo.connect()
    try:
        engine = Engine(repo)
        email = message(body="Просим отправить завтра отчёт.")
        assert await engine.process(email)
        assert not await engine.process(email)
        counts = await repo.counts()
        assert counts["messages"] == counts["tasks"] == counts["outbox"] == 1
        tasks = await repo.tasks()
        assert tasks[0]["due_date"] == "2026-10-02"
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_builtin_selftest_closes_database_on_failure(monkeypatch):
    from workflow_engine.db import Repository
    from workflow_engine.main import selftest
    from workflow_engine.service import Engine

    original_close = Repository.close
    closed = []

    async def close(repo):
        await original_close(repo)
        closed.append(True)

    async def fail_process(*args):
        raise RuntimeError("synthetic selftest failure")

    monkeypatch.setattr(Repository, "close", close)
    monkeypatch.setattr(Engine, "process", fail_process)
    with pytest.raises(RuntimeError, match="synthetic selftest failure"):
        await selftest()
    assert closed == [True]
