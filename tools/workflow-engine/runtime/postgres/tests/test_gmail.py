from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from postgres.gmail import GmailStore, GmailStateError, _history


@pytest.mark.parametrize('value', ['-1', '1.5', '1e3', '١٢', '', None, True, '1' * 21])
def test_invalid_history_is_rejected(value):
    with pytest.raises(ValueError):
        _history(value)


def test_history_normalizes_without_floating_point_loss():
    assert str(_history(' 00018446744073709551615 ')) == '18446744073709551615'
    assert str(_history('000')) == '0'


@pytest.fixture
def store(engine, empty_database):
    return GmailStore(engine)


def test_checkpoint_survives_new_store_and_never_moves_backwards(store, engine):
    assert store.gmail_history_id() is None
    assert store.advance_gmail_history_id('18446744073709551614')
    assert store.advance_gmail_history_id('18446744073709551615')
    assert not store.advance_gmail_history_id('18446744073709551614')
    assert not store.advance_gmail_history_id('18446744073709551615')
    assert GmailStore(engine).gmail_history_id() == '18446744073709551615'
    assert store.gmail_history_id('other') is None


def test_concurrent_checkpoint_updates_keep_maximum(store, engine):
    barrier = Barrier(4)
    values = ['18446744073709551615', '9', '100', '18446744073709551614']
    def advance(value):
        barrier.wait(timeout=10)
        return GmailStore(engine).advance_gmail_history_id(value)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(advance, value) for value in values]
        results = [future.result(timeout=20) for future in futures]
        assert any(results)
    assert store.gmail_history_id() == max(values, key=int)


def test_watch_does_not_advance_checkpoint_and_stale_renewal_cannot_replace_it(store):
    assert store.gmail_watch_state() is None
    assert store.record_gmail_watch(topic_name='new', history_id='20', expiration_ms=2000)
    before = store.gmail_watch_state()
    assert not store.record_gmail_watch(topic_name='old', history_id='10', expiration_ms=1000)
    assert not store.record_gmail_watch(topic_name='old', history_id='10', expiration_ms=2000)
    assert store.gmail_watch_state() == before
    assert before['history_id'] == '20'
    assert store.gmail_history_id() is None
    assert store.gmail_watch_state('other') is None


def test_concurrent_watch_renewals_keep_latest_expiration(store, engine):
    barrier = Barrier(3)
    def renew(expiration):
        barrier.wait(timeout=10)
        return GmailStore(engine).record_gmail_watch(topic_name=f'topic-{expiration}',
                    history_id=str(expiration), expiration_ms=expiration)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(renew, value) for value in (3000, 1000, 2000)]
        results = [future.result(timeout=20) for future in futures]
        assert any(results)
    watch = store.gmail_watch_state()
    assert (watch['expiration_ms'], watch['history_id'], watch['topic_name']) == (3000, '3000', 'topic-3000')


@pytest.mark.parametrize('message_id', ['', 'x' * 257, None])
def test_invalid_notification_identity_does_not_persist(store, message_id):
    with pytest.raises(ValueError):
        store.record_gmail_notification(pubsub_message_id=message_id, history_id='1')
    assert store.pending_gmail_notification() is None


@pytest.mark.parametrize('expiration', [0, -1, True, 1.5, 2**63])
def test_invalid_watch_expiration_does_not_persist(store, expiration):
    with pytest.raises(ValueError):
        store.record_gmail_watch(topic_name='synthetic', history_id='1', expiration_ms=expiration)
    assert store.gmail_watch_state() is None


def test_duplicate_notification_delivery_and_history_have_one_row(store, engine):
    assert store.record_gmail_notification(pubsub_message_id='p1', history_id='0009')
    assert not store.record_gmail_notification(pubsub_message_id='p1', history_id='9')
    assert not store.record_gmail_notification(pubsub_message_id='p2', history_id='9')
    assert not store.record_gmail_notification(pubsub_message_id='p1', history_id='10')
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM gmail_notification')) == 1
        assert db.scalar(text('SELECT count(*) FROM email_event')) == 0
    assert store.gmail_history_id() is None


def test_acknowledgement_is_bounded_and_mailbox_scoped(store):
    for index, history in enumerate(['9', '10', '18446744073709551615']):
        store.record_gmail_notification(pubsub_message_id=f'p{index}', history_id=history)
    store.record_gmail_notification(pubsub_message_id='other', history_id='9', mailbox='other')
    assert store.pending_gmail_notification()['history_id'] == '18446744073709551615'
    assert store.mark_gmail_notifications_through('10') == 2
    assert store.mark_gmail_notifications_through('10') == 0
    assert store.pending_gmail_notification('other')['history_id'] == '9'
    assert store.pending_gmail_notification()['history_id'] == '18446744073709551615'
    assert store.mark_gmail_notifications_through('18446744073709551615') == 1
    assert store.pending_gmail_notification() is None
    # Late notifications stay durable until the ingestion loop acknowledges them.
    assert store.record_gmail_notification(pubsub_message_id='late', history_id='8')
    assert store.mark_gmail_notifications_through('10') == 1
    assert store.gmail_history_id() is None


def test_concurrent_duplicate_notifications_are_durable_once(store, engine):
    barrier = Barrier(4)
    def receive(index):
        barrier.wait(timeout=10)
        return GmailStore(engine).record_gmail_notification(pubsub_message_id=f'p{index}', history_id='999')
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(receive, index) for index in range(4)]
        results = [future.result(timeout=20) for future in futures]
    assert sum(results) == 1
    assert GmailStore(engine).pending_gmail_notification()['history_id'] == '999'


def test_failed_checkpoint_write_preserves_old_value_and_sanitizes_error(store, engine):
    store.advance_gmail_history_id('10')
    with engine.begin() as db:
        db.execute(text('ALTER TABLE gmail_mailbox ADD CONSTRAINT synthetic_limit CHECK (history_id <= 10)'))
    try:
        with pytest.raises(GmailStateError) as error:
            store.advance_gmail_history_id('20')
        assert str(error.value) == 'Gmail state operation failed; transaction rolled back'
        assert store.gmail_history_id() == '10'
    finally:
        with engine.begin() as db:
            db.execute(text('ALTER TABLE gmail_mailbox DROP CONSTRAINT synthetic_limit'))
