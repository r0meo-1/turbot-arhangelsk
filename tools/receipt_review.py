"""Offline closed-test receipt review. No sending, retry, bot or provider imports.

Default CLI operations preview only; --apply backs up SQLite before mutation.
Original transport outcomes and webhook replay fences are never rewritten.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import time

DAY = 86400
ACTIONS = {"received_confirmed", "closed_undelivered"}
REASONS = {"personal_ack", "visual_review", "test_completed", "handled_separately"}


def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS receipt_reviews (
        event_id TEXT NOT NULL, channel TEXT NOT NULL, action TEXT NOT NULL,
        reason TEXT NOT NULL, evidence TEXT NOT NULL, reviewed_at INTEGER NOT NULL,
        PRIMARY KEY(event_id,channel))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS receipt_review_audit (
        id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, channel TEXT NOT NULL,
        action TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL,
        occurred_at INTEGER NOT NULL)""")


def reviews(conn):
    exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_reviews'").fetchone()
    if not exists:
        return {}
    return {(r[0],r[1]):r[2:] for r in conn.execute(
        'SELECT event_id,channel,action,reason,evidence,reviewed_at FROM receipt_reviews')}


def inventory(conn, now=None):
    now=int(time.time()) if now is None else now
    decisions=reviews(conn);result=[]
    for event,created,raw in conn.execute('SELECT event_id,created_at,outcomes FROM ai_handoff_receipts ORDER BY created_at,event_id'):
        try:
            if type(created) is not int or created<0:
                raise ValueError()
            outcomes=json.loads(raw)
            if not isinstance(outcomes,dict) or any(v not in {'accepted','failed','uncertain'} for v in outcomes.values()):
                raise ValueError()
        except (ValueError,TypeError):
            # Corrupt records must be visible and kept, never silently purged.
            result.append({'event_id':event,'channel':'corrupt','transport':'invalid',
                           'review':None,'overdue':True,'purge_after':None})
            continue
        for channel,status in (outcomes or {'configuration':'not_configured'}).items():
            review=decisions.get((event,channel))
            result.append({'event_id':event,'channel':channel,'transport':status,
                'review':review[0] if review else None,
                'reason':review[1] if review else None,
                'evidence':review[2] if review else None,
                'reviewed_at':review[3] if review else None,
                'overdue':not review and now-created>=7*DAY,
                'purge_after':review[3]+7*DAY if review else None})
    return result


def review(conn,event,channel,action,reason,evidence,now=None):
    if action not in ACTIONS or reason not in REASONS:
        raise ValueError('unsupported review decision')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',evidence):
        raise ValueError('use a short synthetic evidence reference, not message text')
    if (action=='received_confirmed') != (reason in {'personal_ack','visual_review'}):
        raise ValueError('delivery confirmation requires human observation; closure is not delivery')
    row=next((r for r in inventory(conn,now) if r['event_id']==event and r['channel']==channel),None)
    if row is None or row['transport']=='invalid':
        raise ValueError('unknown or corrupt receipt: inspect separately')
    if action=='received_confirmed' and row['transport'] not in {'accepted','uncertain'}:
        raise ValueError('explicit refusal/missing destination cannot be confirmed as this delivery')
    if row['review'] is not None:
        old=conn.execute('SELECT action,reason,evidence FROM receipt_reviews WHERE event_id=? AND channel=?',(event,channel)).fetchone()
        if old==(action,reason,evidence):
            return False
        raise ValueError('review is immutable; conflicting decision requires separate investigation')
    now=int(time.time()) if now is None else now
    with conn:
        schema(conn)
        conn.execute('INSERT INTO receipt_reviews VALUES (?,?,?,?,?,?)',(event,channel,action,reason,evidence,now))
        conn.execute('INSERT INTO receipt_review_audit(event_id,channel,action,reason,evidence,occurred_at) VALUES (?,?,?,?,?,?)',
                     (event,channel,action,reason,evidence,now))
    return True


def purge(conn,now=None,apply=False):
    now=int(time.time()) if now is None else now
    items=inventory(conn,now);groups={}
    for item in items:groups.setdefault(item['event_id'],[]).append(item)
    eligible=[event for event,rows in groups.items() if all(
        r['purge_after'] is not None and r['purge_after']<=now for r in rows)]
    audit_exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_review_audit'").fetchone()
    old_audit=[]
    if audit_exists:
        retained=set(groups)-set(eligible)
        old_audit=[r[0] for r in conn.execute('SELECT id,event_id,occurred_at FROM receipt_review_audit')
                   if r[2]<=now-30*DAY and r[1] not in retained]
    if apply:
        with conn:
            schema(conn)
            for event in eligible:
                conn.execute('DELETE FROM ai_handoff_receipts WHERE event_id=?',(event,))
                conn.execute('DELETE FROM receipt_reviews WHERE event_id=?',(event,))
            for audit in old_audit:conn.execute('DELETE FROM receipt_review_audit WHERE id=?',(audit,))
    return {'receipt_ids':eligible,'audit_rows':len(old_audit),'applied':apply}


def backup(database):
    target=database.with_name(database.name+'.'+str(time.time_ns())+'.bak')
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    with sqlite3.connect(f'{database.as_uri()}?mode=ro',uri=True) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
    return target.name


def prune_backups(database,now=None,apply=False):
    now=time.time() if now is None else now
    names=[]
    for file in database.parent.glob(database.name+'.*.bak'):
        match=re.fullmatch(re.escape(database.name)+r'\.(\d+)\.bak',file.name)
        if (not match or file.is_symlink() or not file.is_file()
                or int(match[1])/1e9>now-7*DAY or file.stat().st_mtime>now-7*DAY):
            continue
        names.append(file.name)
        if apply:file.unlink()
    return {'backup_files':sorted(names),'applied':apply}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True,type=Path)
    p.add_argument('--closed-root',required=True,type=Path)
    p.add_argument('--apply',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('list');sub.add_parser('audit');sub.add_parser('purge');sub.add_parser('prune-backups')
    r=sub.add_parser('review')
    for name in ['event','channel','action','reason','evidence']:r.add_argument('--'+name,required=True)
    args=p.parse_args();database=args.database.resolve();root=args.closed_root.resolve()
    if not database.is_relative_to(root) or not (root/'.closed-receipt-test').is_file():
        raise ValueError('requires a marked closed-test directory; production paths are forbidden')
    if not database.is_file():raise ValueError('existing receipt database required')
    if args.command in {'list','audit'} and args.apply:raise ValueError('inspection is read-only')
    if args.command=='prune-backups':
        print(json.dumps(prune_backups(database,apply=args.apply)))
        return
    result={}
    if args.apply:result['backup']=backup(database)
    with sqlite3.connect(f'{database.as_uri()}?mode={"rw" if args.apply else "ro"}',uri=True) as conn:
        if args.command=='list':result['receipts']=inventory(conn)
        elif args.command=='audit':
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='receipt_review_audit'").fetchone()
            columns=('event_id','channel','action','reason','evidence','occurred_at')
            result['audit']=[dict(zip(columns,row)) for row in conn.execute(
                'SELECT event_id,channel,action,reason,evidence,occurred_at FROM receipt_review_audit ORDER BY id')] if exists else []
        elif args.command=='purge':result.update(purge(conn,apply=args.apply))
        elif args.apply:result['changed']=review(conn,args.event,args.channel,args.action,args.reason,args.evidence)
        else:
            # Validate against an in-memory backup; preview never changes disk.
            with sqlite3.connect(':memory:') as preview:
                conn.backup(preview)
                result['would_change']=review(preview,args.event,args.channel,args.action,args.reason,args.evidence)
            result['applied']=False
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
