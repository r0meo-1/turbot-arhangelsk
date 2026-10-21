import json
import sqlite3
from types import SimpleNamespace
import pytest
from tools.closed_https import ingress_app
from tests.test_closed_webhook import runner,message,callback

SECRET='synthetic-private-secret-'*3
HEADERS={'X-Telegram-Bot-Api-Secret-Token':SECRET}


def setup(tmp_path,fail=False):
    actual=runner(tmp_path);actual.state['owned_messages']=[9];calls=[]
    def handle(update):
        calls.append(update)
        if fail:raise RuntimeError('synthetic interruption')
    app=ingress_app(SimpleNamespace(allowed=actual.allowed,handle=handle),SECRET,tmp_path/'ingress.sqlite')
    return app.test_client(),calls,tmp_path/'ingress.sqlite'


@pytest.mark.parametrize('headers',[{}, {'X-Telegram-Bot-Api-Secret-Token':'wrong'}])
def test_auth_before_processing_or_database(tmp_path,headers):
    client,calls,db=setup(tmp_path)
    r=client.post('/closed-webhook',json=message(),headers=headers)
    assert r.status_code==403 and r.json['error']=='authentication'
    assert calls==[] and not db.exists()
    assert client.get('/closed-health',headers=headers).status_code==403


@pytest.mark.parametrize('data',[None,[],{'message':[]},{'update_id':-1},
    {'update_id':1}, {'update_id':1,'callback_query':[]},
    {'message':{'chat':{'id':42}}}, {'update_id':True,'message':{'chat':{'id':42}}}])
def test_malformed_rejected_before_database(tmp_path,data):
    client,calls,db=setup(tmp_path)
    assert client.post('/closed-webhook',data=json.dumps(data),content_type='application/json',headers=HEADERS).status_code==400
    assert calls==[] and not db.exists()


@pytest.mark.parametrize('data',[message(uid=43,chat=43),message(chat=-1),
    callback(uid=43),callback(chat=43),callback(mid=999)])
def test_foreign_account_callback_group_rejected(tmp_path,data):
    client,calls,db=setup(tmp_path)
    r=client.post('/closed-webhook',json=data,headers=HEADERS)
    assert r.status_code==403 and r.json['error']=='account_gate'
    assert calls==[] and not db.exists()


def test_content_method_and_size_limits(tmp_path):
    client,calls,db=setup(tmp_path)
    assert client.get('/closed-webhook',headers=HEADERS).status_code==405
    assert client.post('/closed-webhook',data='plain',headers=HEADERS).status_code==415
    assert client.post('/closed-webhook',data='{broken',content_type='application/json',headers=HEADERS).status_code==400
    assert client.post('/closed-webhook',data='x'*17000,content_type='application/json',headers=HEADERS).status_code==413
    assert calls==[] and not db.exists()


def test_durable_duplicate_conflict_and_no_raw_payload(tmp_path):
    client,calls,db=setup(tmp_path);data=message('/ask unique-synthetic-probe')
    assert client.post('/closed-webhook',json=data,headers=HEADERS).status_code==200
    assert client.post('/closed-webhook',json=data,headers=HEADERS).status_code==200
    other,_,_=setup(tmp_path)
    assert other.post('/closed-webhook',json=data,headers=HEADERS).status_code==200
    changed=message('/ask changed')
    assert client.post('/closed-webhook',json=changed,headers=HEADERS).status_code==409
    assert len(calls)==1
    assert b'unique-synthetic-probe' not in db.read_bytes() and SECRET.encode() not in db.read_bytes()
    assert client.get('/closed-health',headers=HEADERS).json['ready'] is True


def test_failed_and_interrupted_processing_not_replayed(tmp_path):
    client,calls,db=setup(tmp_path,fail=True)
    for _ in range(2):assert client.post('/closed-webhook',json=message(),headers=HEADERS).status_code==503
    assert len(calls)==1
    with sqlite3.connect(db) as c:
        assert c.execute('select status from https_ingress_receipts').fetchone()[0]=='uncertain'
        c.execute("update https_ingress_receipts set status='processing'")
    assert client.post('/closed-webhook',json=message(),headers=HEADERS).status_code==503
    assert len(calls)==1


def test_private_tls_verifies_certificate_and_uses_unix_socket(tmp_path):
    import ssl
    from tools.closed_https import PrivateTLS,HOST
    connection=PrivateTLS(tmp_path/'tls.sock')
    assert connection.host==HOST
    assert connection._context.check_hostname is True
    assert connection._context.verify_mode==ssl.CERT_REQUIRED
    assert connection.socket_path==str(tmp_path/'tls.sock')
    connection.close()


def test_nginx_template_has_no_tcp_listener():
    from pathlib import Path
    text=(Path(__file__).resolve().parents[1]/'deploy/closed-https-nginx.conf.example').read_text(encoding='utf-8-sig')
    assert 'listen unix:' in text and text.count('listen ')==1
    assert 'server unix:' in text
    assert 'access_log off;' in text and 'ssl_protocols TLSv1.2 TLSv1.3;' in text
