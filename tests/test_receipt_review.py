import json
import os
import sqlite3
import subprocess
import sys
import pytest
from tools.receipt_review import DAY,inventory,review,purge,backup,prune_backups


@pytest.fixture
def db(tmp_path):
    c=sqlite3.connect(tmp_path/'receipts.sqlite')
    c.execute('CREATE TABLE ai_handoff_receipts(event_id TEXT PRIMARY KEY,subject TEXT,created_at INTEGER,outcomes TEXT)')
    c.execute('CREATE TABLE webhook_receipts(event_key TEXT PRIMARY KEY,state TEXT)')
    c.execute("INSERT INTO webhook_receipts VALUES ('keep-fence','complete')")
    c.execute('CREATE TABLE https_ingress_receipts(update_id INTEGER PRIMARY KEY,status TEXT)')
    c.execute("INSERT INTO https_ingress_receipts VALUES (1,'complete')")
    for key,outcomes in [('ok',{'telegram:0':'accepted'}),('failed',{'telegram:0':'failed'}),
            ('unknown',{'telegram:0':'uncertain'}),('empty',{}),
            ('partial',{'telegram:0':'accepted','telegram:1':'failed'})]:
        c.execute('INSERT INTO ai_handoff_receipts VALUES (?,?,?,?)',(key,'synthetic',0,json.dumps(outcomes)))
    c.commit();yield c;c.close()


def test_unresolved_overdue_never_purged_even_if_accepted(db):
    assert all(r['overdue'] for r in inventory(db,now=90*DAY))
    assert purge(db,now=90*DAY,apply=True)['receipt_ids']==[]
    assert db.execute('select count(*) from ai_handoff_receipts').fetchone()[0]==5


@pytest.mark.parametrize('event,channel,action,reason',[('ok','telegram:0','received_confirmed','personal_ack'),
    ('unknown','telegram:0','received_confirmed','visual_review'),
    ('failed','telegram:0','closed_undelivered','test_completed'),
    ('empty','configuration','closed_undelivered','handled_separately')])
def test_review_preserves_transport_idempotent_and_immutable(db,event,channel,action,reason):
    before=db.execute('select outcomes from ai_handoff_receipts where event_id=?',(event,)).fetchone()[0]
    assert review(db,event,channel,action,reason,'synthetic-case',now=100)
    assert not review(db,event,channel,action,reason,'synthetic-case',now=200)
    with pytest.raises(ValueError):review(db,event,channel,action,reason,'different-evidence',now=200)
    assert db.execute('select count(*) from receipt_review_audit').fetchone()[0]==1
    assert db.execute('select outcomes from ai_handoff_receipts where event_id=?',(event,)).fetchone()[0]==before


def test_all_channels_must_be_reviewed_and_ttl_from_last_review(db):
    review(db,'partial','telegram:0','received_confirmed','personal_ack','ack-test',now=0)
    assert purge(db,now=90*DAY)['receipt_ids']==[]
    review(db,'partial','telegram:1','closed_undelivered','test_completed','failure-test',now=DAY)
    assert purge(db,now=7*DAY)['receipt_ids']==[]
    assert purge(db,now=8*DAY)['receipt_ids']==['partial']
    assert db.execute("select count(*) from ai_handoff_receipts where event_id='partial'").fetchone()[0]==1
    purge(db,now=8*DAY,apply=True)
    assert db.execute('select count(*) from receipt_review_audit').fetchone()[0]==2
    assert db.execute('select count(*) from webhook_receipts').fetchone()[0]==1
    assert db.execute('select count(*) from https_ingress_receipts').fetchone()[0]==1
    assert purge(db,now=30*DAY)['audit_rows']==1
    assert purge(db,now=31*DAY,apply=True)['audit_rows']==2


@pytest.mark.parametrize('event,channel,action,reason,evidence',[
    ('failed','telegram:0','received_confirmed','personal_ack','wrong'),
    ('empty','configuration','received_confirmed','visual_review','wrong'),
    ('unknown','telegram:0','received_confirmed','test_completed','wrong'),
    ('missing','telegram:0','closed_undelivered','test_completed','wrong'),
    ('failed','telegram:0','closed_undelivered','test_completed','raw text with phone'),
])
def test_invalid_or_false_confirmation_is_rejected(db,event,channel,action,reason,evidence):
    with pytest.raises(ValueError):review(db,event,channel,action,reason,evidence)


def test_corrupt_record_is_held(db):
    db.execute("insert into ai_handoff_receipts values ('corrupt','synthetic',0,'broken')");db.commit()
    assert next(r for r in inventory(db,now=90*DAY) if r['event_id']=='corrupt')['transport']=='invalid'
    assert purge(db,now=90*DAY,apply=True)['receipt_ids']==[]


def test_backup_is_restorable_and_backup_prune_is_scoped(db,tmp_path):
    file=tmp_path/'receipts.sqlite';name=backup(file)
    with sqlite3.connect(tmp_path/name) as c:
        assert c.execute('select count(*) from ai_handoff_receipts').fetchone()[0]==5
    old=tmp_path/'receipts.sqlite.1000000000.bak';old.write_bytes(b'synthetic')
    os.utime(old,(1,1));unrelated=tmp_path/'unrelated.1.bak';unrelated.write_text('keep')
    assert prune_backups(file,now=8*DAY)['backup_files']==[old.name]
    assert old.exists()
    prune_backups(file,now=8*DAY,apply=True)
    assert not old.exists() and unrelated.exists() and (tmp_path/name).exists()


def test_cli_preview_is_read_only_and_apply_has_backup(db,tmp_path):
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'tools/receipt_review.py'
    args=[sys.executable,str(script),'--database',str(tmp_path/'receipts.sqlite'),'--closed-root',str(tmp_path)]
    assert subprocess.run(args+['list'],capture_output=True).returncode!=0
    (tmp_path/'.closed-receipt-test').touch()
    before=(tmp_path/'receipts.sqlite').read_bytes()
    command=['review','--event','failed','--channel','telegram:0','--action','closed_undelivered',
             '--reason','test_completed','--evidence','synthetic-cli-case']
    p=subprocess.run(args+command,capture_output=True,text=True,check=True)
    assert json.loads(p.stdout)['applied'] is False
    assert (tmp_path/'receipts.sqlite').read_bytes()==before
    assert not list(tmp_path.glob('*.bak'))
    p=subprocess.run(args+['--apply']+command,capture_output=True,text=True,check=True)
    assert json.loads(p.stdout)['changed'] is True
    assert len(list(tmp_path.glob('*.bak')))==1
    assert len(json.loads(subprocess.run(args+['audit'],capture_output=True,text=True,check=True).stdout)['audit'])==1


def test_cli_rejects_database_outside_marked_root(db,tmp_path):
    from pathlib import Path
    root=tmp_path/'closed';root.mkdir();(root/'.closed-receipt-test').touch()
    script=Path(__file__).resolve().parents[1]/'tools/receipt_review.py'
    before=(tmp_path/'receipts.sqlite').read_bytes()
    args=[sys.executable,str(script),'--database',str(tmp_path/'receipts.sqlite'),
          '--closed-root',str(root),'--apply','purge']
    assert subprocess.run(args,capture_output=True).returncode!=0
    assert (tmp_path/'receipts.sqlite').read_bytes()==before
    assert not list(tmp_path.glob('*.bak'))


def test_corrupt_timestamp_is_held(db):
    db.execute("insert into ai_handoff_receipts values ('bad-date','synthetic','broken','{}')");db.commit()
    assert next(r for r in inventory(db) if r['event_id']=='bad-date')['transport']=='invalid'
    assert purge(db,apply=True)['receipt_ids']==[]


def test_manager_notice_exposes_same_receipt_id_as_database(tmp_path,monkeypatch):
    from tests.test_closed_webhook import runner
    from types import SimpleNamespace
    import requests
    r=runner(tmp_path);sent=[]
    with r.offline() as bot:
        def post(url,**kw):
            sent.append(kw['json']['text']);response=requests.Response();response.status_code=200
            response._content=json.dumps({'ok':True,'result':{'chat':{'id':42},'message_id':1}}).encode()
            return response
        monkeypatch.setattr(bot,'telegram_session',SimpleNamespace(post=post))
        result=bot._notify_lead_assist_handoff(42,'Хочу менеджера','human_requested')
        assert all('Квитанция: '+result['event_id'] in text for text in sent)
        with bot._db_cursor() as cur:
            assert cur.execute('select event_id from ai_handoff_receipts').fetchone()[0]==result['event_id']
