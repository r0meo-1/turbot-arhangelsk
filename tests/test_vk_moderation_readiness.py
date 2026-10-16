"""Moderation hardening: persistent owner limits, privacy and legal fallbacks."""
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask
from shared import vk_miniapp_limits as limits
from shared.legal_identity import LEGAL_OPERATOR_DISPLAY, LEGAL_PRIVACY_CONTACT
from shared.vk_miniapp import create_blueprint
from test_vk_miniapp import signed, payload, SECRET


def test_budget_is_owner_scoped_persistent_and_expires(tmp_path):
    path = tmp_path / 'limits.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        limits.init_schema(db.cursor())
        for _ in range(30):
            assert limits.admit(db.cursor(), 42, now=120) == 0
    with closing(sqlite3.connect(path)) as db, db:
        assert limits.admit(db.cursor(), 42, now=125) == 55
        assert limits.admit(db.cursor(), 43, now=125) == 0
        assert limits.admit(db.cursor(), 42, now=180) == 0
        limits.admit(db.cursor(), 99, now=360)
        assert db.execute('select count(*) from vk_miniapp_write_limits where user_id=43').fetchone()[0] == 0


@pytest.mark.parametrize('endpoint', ['draft','continue','booking-link'])
def test_limit_prevents_side_effects_binds_owner_and_preserves_retry(endpoint, monkeypatch):
    admitted, side_effects = [], []
    def admit(uid):
        admitted.append(uid)
        return 12
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *a: side_effects.append(a),
        lambda:(SECRET,'123',999), continue_draft=lambda *a: side_effects.append(a),
        cancel_continue=lambda *a: True, admit_write=admit))
    body=dict(launchParams=signed(),payload=payload(),requestId=str(uuid4()))
    with app.test_client() as client:
        response=client.post('/vk/miniapp/'+endpoint,json=body)
        assert response.status_code==429 and response.headers['Retry-After']=='12'
        assert response.json['retryable'] and not response.json['ok']
        assert admitted==[42] and not side_effects
        assert client.post('/vk/miniapp/'+endpoint,json=dict(body,launchParams='invalid')).status_code==401
        assert admitted==[42]
        assert client.post('/vk/miniapp/continue/cancel',json=body).status_code==200
        assert admitted==[42]  # cancellation stays available


def test_full_erasure_removes_only_target_owner_limit():
    import vk_bot as bot
    ids=(89121,89122)
    try:
        for uid in ids:
            bot._admit_miniapp_write(uid)
        bot.delete_user_data(ids[0])
        with bot._db_cursor() as cur:
            assert not cur.execute('select 1 from vk_miniapp_write_limits where user_id=?',(ids[0],)).fetchone()
            assert cur.execute('select 1 from vk_miniapp_write_limits where user_id=?',(ids[1],)).fetchone()
    finally:
        for uid in ids:
            bot.delete_user_data(uid)


@pytest.mark.parametrize('name', ['privacy.html','consent.html'])
def test_no_script_documents_already_identify_canonical_operator(name):
    text=(Path(__file__).resolve().parents[1]/'vk-miniapp'/name).read_text(encoding='utf-8')
    assert f'data-legal-operator>{LEGAL_OPERATOR_DISPLAY}</span>' in text
    assert LEGAL_PRIVACY_CONTACT in text

def test_production_blueprint_enforces_shared_budget(monkeypatch):
    import vk_bot as bot
    monkeypatch.setenv('VK_MINI_APP_SECRET',SECRET)
    monkeypatch.setenv('VK_MINI_APP_ID','123')
    for _ in range(30):
        assert bot._admit_miniapp_write(42)==0
    with bot.app.test_client() as client:
        response=client.post('/vk/miniapp/draft',json=dict(launchParams=signed(),payload=payload()))
        assert response.status_code==429
