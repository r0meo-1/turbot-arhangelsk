import asyncio
from datetime import date, datetime, timezone
import json

import pytest
from sqlalchemy import event, text

from postgres.canonical import CanonicalStore
from postgres.projection import ProjectionStore, _record
from workflow_engine.logic import Validator
from workflow_engine.models import EmailMessage, TaskCandidate
from workflow_engine.projection import MarkdownProjection


def test_record_preserves_renderer_types():
    result = _record({'due_date': date(2026, 10, 5),
                      'updated_at': datetime(2026, 10, 2, tzinfo=timezone.utc),
                      'candidate_json': {'title': 'Synthetic review'}})
    assert result['due_date'] == '2026-10-05'
    assert result['updated_at'] == '2026-10-02T00:00:00+00:00'
    assert json.loads(result['candidate_json']) == {'title': 'Synthetic review'}


@pytest.mark.usefixtures('empty_database')
def test_async_snapshot_renders_tasks_and_only_unresolved_reviews(engine, tmp_path):
    message = EmailMessage('gmail', 'synthetic', 'thread', 'qa@example.invalid',
                           'Synthetic', '', datetime(2026, 10, 2, tzinfo=timezone.utc))
    candidates = [TaskCandidate('Accepted', 'QA', 50, 'normal', None,
                               '2026-10-05', .9, False, 'accepted'),
                  TaskCandidate('Review', 'QA', 50, 'normal', None,
                               None, .1, False, 'review')]
    CanonicalStore(engine).ingest(message, candidates, Validator())
    tasks, reviews = asyncio.run(ProjectionStore(engine).snapshot_async())
    output = MarkdownProjection(str(tmp_path / 'board.md')).render(tasks, reviews)
    assert 'Accepted' in output and '2026-10-05' in output and 'Review' in output
    with engine.begin() as db:
        db.execute(text('UPDATE review_queue SET resolved=true'))
    assert ProjectionStore(engine).snapshot()[1] == []


@pytest.mark.usefixtures('empty_database')
def test_snapshot_is_read_only_and_does_not_mix_concurrent_review(engine):
    observed = []
    def after_query(connection, cursor, statement, parameters, context, many):
        if 'SELECT * FROM task' not in statement:
            return
        observed.append(connection.scalar(text('SHOW transaction_read_only')))
        with engine.begin() as other:
            other.execute(text("""INSERT INTO review_queue
                (id,source,external_id,candidate_json,reason,created_at)
                VALUES ('concurrent','gmail','synthetic','{}','test',now())"""))
    event.listen(engine, 'after_cursor_execute', after_query)
    try:
        assert ProjectionStore(engine).snapshot() == ([], [])
    finally:
        event.remove(engine, 'after_cursor_execute', after_query)
    assert observed == ['on']
    assert len(ProjectionStore(engine).snapshot()[1]) == 1
