import json
from types import SimpleNamespace
import pytest
import requests
from tools.closed_telegram_ai import Settings
from tools.closed_webhook import ClosedWebhookTest


class Transport:
    def __init__(self, fail_manager=False, fail_client=False):
        self.calls=[]; self.callback_ids=set()
        self.fail_manager=fail_manager; self.fail_client=fail_client
    def call(self, method, **payload):
        self.calls.append((method,payload))
        manager='Вопрос клиента требует менеджера' in payload.get('text','')
        if method=='sendMessage' and ((manager and self.fail_manager) or (not manager and self.fail_client)):
            raise requests.Timeout('secret-token-in-URL must not be logged')
        return {'chat':{'id':42},'message_id':len(self.calls)}


def runner(tmp_path,tg=None):
    create=lambda **kw: SimpleNamespace(usage=None,choices=[SimpleNamespace(
        finish_reason='stop',message=SimpleNamespace(content='Возьмите удобную обувь.'))])
    provider=SimpleNamespace(ready=True,model='synthetic',client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return ClosedWebhookTest(Settings('200:synthetic','test',42,100),tg or Transport(),
                             tmp_path/'state.json',provider)


def message(text='/ask Хочу менеджера',uid=42,chat=42,number=1):
    return {'update_id':number,'message':{'message_id':77,'from':{'id':uid},
        'chat':{'id':chat,'type':'private' if chat>0 else 'group'},'text':text}}


def callback(key='packing',uid=42,chat=42,mid=9,number=1):
    return {'update_id':number,'callback_query':{'id':'synthetic-callback','from':{'id':uid},
        'data':'aiq:'+key,'message':{'message_id':mid,'from':{'id':200,'is_bot':True},
            'chat':{'id':chat,'type':'private'}}}}


@pytest.mark.parametrize('uid,chat,mid',[(43,42,9),(42,43,9),(42,-1,9),(42,42,999)])
def test_foreign_callbacks_no_sender_model_or_db(tmp_path,uid,chat,mid):
    r=runner(tmp_path);r.state['owned_messages']=[9]
    r.handle(callback(uid=uid,chat=chat,mid=mid))
    assert r.telegram.calls==[] and r.state['ai_calls']==0
    assert not (tmp_path/'webhook.sqlite').exists()


@pytest.mark.parametrize('uid,chat',[(43,43),(42,-1)])
def test_foreign_message_no_side_effects(tmp_path,uid,chat):
    r=runner(tmp_path);r.handle(message(uid=uid,chat=chat))
    assert r.telegram.calls==[]


@pytest.mark.parametrize('text',['/ask Что взять в поездку?','ИИ: Что взять в поездку?'])
def test_actual_webhook_and_sender_success_replay_restart(tmp_path,text):
    r=runner(tmp_path);u=message(text);r.handle(u)
    assert r.state['ai_calls']==1 and r.state['webhook_updates']==1
    count=len(r.telegram.calls);r.handle(u)
    reopened=ClosedWebhookTest(r.settings,r.telegram,r.path,r.provider)
    reopened.handle(u)
    assert len(r.telegram.calls)==count
    with r.offline() as bot:
        assert bot.user_data[42]['kids_ages']==[9]
        with bot._db_cursor() as cur:
            assert cur.execute('select count(*) from leads').fetchone()[0]==0
            assert cur.execute("select state from webhook_receipts").fetchone()[0]=='complete'


@pytest.mark.parametrize('key,expected_calls',[('packing',1),('hotel',1),('prep',1),('invalid-injection',0)])
def test_actual_callback_ack_keyboard_and_guard(tmp_path,key,expected_calls):
    r=runner(tmp_path);r.state['owned_messages']=[9];r.handle(callback(key))
    assert r.state['ai_calls']==expected_calls
    methods=[m for m,p in r.telegram.calls]
    assert 'answerCallbackQuery' in methods and 'editMessageReplyMarkup' in methods
    assert all(p.get('chat_id',42)==42 for m,p in r.telegram.calls)


@pytest.mark.parametrize('fail_manager,fail_client,expected',[(False,False,'accepted'),(True,False,'uncertain'),(False,True,'accepted')])
def test_manager_transport_receipts_survive_client_failure(tmp_path,caplog,fail_manager,fail_client,expected):
    r=runner(tmp_path,Transport(fail_manager,fail_client));r.handle(message())
    with r.offline() as bot:
        with bot._db_cursor() as cur:
            rows=cur.execute('select outcomes from ai_handoff_receipts').fetchall()
            assert len(rows)==1
            assert expected in json.loads(rows[0][0]).values()
            assert cur.execute('select count(*) from leads').fetchone()[0]==0
    assert 'secret-token-in-URL' not in caplog.text
    assert r.state['ai_calls']==0
    event=next(iter(r.state['handoffs'].values()))
    assert event['api_accepted'] == (not fail_manager)


@pytest.mark.parametrize('header,status',[(None,403),('wrong',403),('closed-local-secret',200)])
def test_real_webhook_auth(tmp_path,header,status):
    r=runner(tmp_path)
    with r.offline() as bot:
        headers={} if header is None else {'X-Telegram-Bot-Api-Secret-Token':header}
        assert bot.app.test_client().post('/webhook',json={'update_id':99},headers=headers).status_code==status
    assert r.telegram.calls==[]


@pytest.mark.parametrize('data',[{'message':{'chat':{'id':'wrong'}}},{'update_id':-1}, {'message':{},'callback_query':{}}])
def test_real_webhook_validation(tmp_path,data):
    r=runner(tmp_path)
    with r.offline() as bot:
        assert bot.app.test_client().post('/webhook',json=data,
            headers={'X-Telegram-Bot-Api-Secret-Token':'closed-local-secret'}).status_code==400


@pytest.mark.parametrize('body,status,expected',[
    ({'ok':True,'result':{'chat':{'id':42},'message_id':1}},200,'accepted'),
    ({'ok':False,'error_code':429},200,'failed'),
    ({'ok':False,'error_code':500},500,'failed'),
    ({'ok':True,'result':{'chat':{'id':43},'message_id':1}},200,'uncertain'),
    ({'ok':True,'result':{}},200,'uncertain'),
])
def test_delivery_requires_matching_api_receipt(tmp_path,body,status,expected):
    r=runner(tmp_path)
    with r.offline() as bot:
        response=requests.Response();response.status_code=status
        response._content=json.dumps(body).encode()
        assert bot._ai_telegram_receipt(response,42)==expected


@pytest.mark.parametrize('status,body,expected',[
    (200,{'ok':False,'error_code':400},'failed'),
    (429,{'ok':False,'error_code':429},'failed'),
    (503,{'error':'synthetic'},'uncertain'),
])
def test_actual_manager_sender_http_failures_are_persisted(tmp_path,monkeypatch,status,body,expected):
    r=runner(tmp_path)
    with r.offline() as bot:
        response=requests.Response();response.status_code=status
        response._content=json.dumps(body).encode()
        monkeypatch.setattr(bot,'telegram_session',SimpleNamespace(post=lambda *a,**k:response))
        result=bot._notify_lead_assist_handoff(42,'Хочу менеджера','human_requested')
        assert result['status']==expected
        with bot._db_cursor() as cur:
            persisted=json.loads(cur.execute('select outcomes from ai_handoff_receipts').fetchone()[0])
            assert expected in persisted.values()


def test_multiple_recipients_have_independent_receipts_without_privacy_salt(tmp_path,monkeypatch):
    r=runner(tmp_path)
    with r.offline() as bot:
        monkeypatch.setattr(bot,'LEAD_NOTIFY_IDS',[42,43])
        monkeypatch.setattr(bot,'_log_correlation',lambda *a,**k:'redacted')
        def post(url,**kw):
            cid=kw['json']['chat_id'];response=requests.Response();response.status_code=200
            response._content=json.dumps({'ok':cid==42,'result':{'chat':{'id':cid},'message_id':1}}).encode()
            return response
        monkeypatch.setattr(bot,'telegram_session',SimpleNamespace(post=post))
        result=bot._notify_lead_assist_handoff(42,'Хочу менеджера','human_requested')
        assert result['channels']=={'telegram:0':'accepted','telegram:1':'failed'}
        assert result['status']=='accepted'


def test_webhook_transport_rejects_unobserved_callback_or_foreign_chat():
    from tools.closed_webhook import WebhookTelegram
    tg=WebhookTelegram(Settings('200:synthetic','test',42,100))
    try:
        with pytest.raises(ValueError):tg.call('answerCallbackQuery',callback_query_id='unknown')
        with pytest.raises(ValueError):tg.call('editMessageReplyMarkup',chat_id=43,message_id=1)
    finally:tg.session.close()


def test_failed_webhook_is_fenced_instead_of_replayed(tmp_path,monkeypatch):
    r=runner(tmp_path);calls=[]
    with r.offline() as bot:
        def fail(data):
            calls.append(data['update_id'])
            raise RuntimeError('synthetic interruption')
        monkeypatch.setattr(bot,'_process_update',fail)
        client=bot.app.test_client();headers={'X-Telegram-Bot-Api-Secret-Token':'closed-local-secret'}
        assert client.post('/webhook',json=message(),headers=headers).status_code==503
        assert client.post('/webhook',json=message(),headers=headers).status_code==503
        assert calls==[1]
        with bot._db_cursor() as cur:
            assert cur.execute('select state from webhook_receipts').fetchone()[0]=='uncertain'
        assert client.post('/webhook',data='{broken',content_type='application/json',headers=headers).status_code==400
