"""Private HTTPS ingress over Unix sockets behind isolated nginx.

Telegram polling -> verified local TLS -> authenticated account gate -> existing
closed webhook runner. No setWebhook, public bind, production env or CRM route.
"""
import argparse
import hmac
import hashlib
import http.client
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import sys
import time

from flask import Flask, request, jsonify

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared.webhook_delivery import valid_telegram

HOST = 'bot.r0meo1.ru'


def ingress_app(runner, secret, database):
    if len(secret)<32:raise ValueError('private ingress secret required')
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=16384
    stats={'accepted':0,'rejected':0,'duplicates':0,'uncertain':0}

    def authenticated():
        return hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token','').encode(),secret.encode())

    @app.get('/closed-health')
    def health():
        if not authenticated():return '',403
        return jsonify(ready=True,stats=stats)

    @app.post('/closed-webhook')
    def receive():
        if not authenticated():
            stats['rejected']+=1
            return jsonify(error='authentication'),403
        if not request.is_json:return '',415
        data=request.get_json(silent=True)
        if (not valid_telegram(data) or type(data.get('update_id')) is not int
                or not ('message' in data or 'callback_query' in data)):
            stats['rejected']+=1
            return '',400
        if not runner.allowed(data):
            stats['rejected']+=1
            return jsonify(error='account_gate'),403
        # Persistent HTTP fence is separate from transport send receipts. A
        # crash/uncertain outcome never causes automatic rerun of the handler.
        with sqlite3.connect(database) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS https_ingress_receipts (
                update_id INTEGER PRIMARY KEY, status TEXT NOT NULL, created_at INTEGER NOT NULL,
                payload_hash TEXT NOT NULL)''')
            digest=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            old=conn.execute('SELECT status,payload_hash FROM https_ingress_receipts WHERE update_id=?',(data['update_id'],)).fetchone()
            if old:
                stats['duplicates']+=1
                if old[1]!=digest:return '',409
                return ('',200) if old[0]=='complete' else ('',503)
            conn.execute('INSERT INTO https_ingress_receipts VALUES (?, ?, ?, ?)',(data['update_id'],'processing',int(time.time()),digest))
        try:
            runner.handle(data)
        except Exception:
            with sqlite3.connect(database) as conn:
                conn.execute("UPDATE https_ingress_receipts SET status='uncertain' WHERE update_id=?",(data['update_id'],))
            stats['uncertain']+=1
            return '',503
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE https_ingress_receipts SET status='complete' WHERE update_id=?",(data['update_id'],))
        stats['accepted']+=1
        return '',200
    return app


class PrivateTLS(http.client.HTTPSConnection):
    """Always dial a private Unix socket; verify real CA, hostname and SNI."""
    def __init__(self,socket_path):
        super().__init__(HOST,timeout=60,context=ssl.create_default_context())
        self.socket_path=str(socket_path)

    def connect(self):
        raw=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        try:
            raw.connect(self.socket_path)
            self.sock=self._context.wrap_socket(raw,server_hostname=HOST)
        except BaseException:
            raw.close()
            raise


def post_tls(update,secret,socket_path):
    conn=PrivateTLS(socket_path)
    try:
        conn.request('POST','/closed-webhook',body=json.dumps(update).encode(),headers={
            'Host':HOST,'Content-Type':'application/json','X-Telegram-Bot-Api-Secret-Token':secret})
        response=conn.getresponse();raw=response.read()
        try:body=json.loads(raw) if raw else {}
        except ValueError:body={}
        return response.status,body
    finally:conn.close()


def serve_bounded(server,duration):
    """Bound the accept window even outside systemd; let an in-flight request finish.

    RuntimeMaxSec is still required for a hard wall-clock deadline during I/O.
    """
    deadline=time.monotonic()+min(900,max(1,duration))
    server.timeout=.25
    try:
        while time.monotonic()<deadline:
            server.handle_request()
    finally:
        server.server_close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['server','bridge'])
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--ai-env',type=Path)
    p.add_argument('--state',required=True,type=Path)
    p.add_argument('--ingress-secret',required=True,type=Path)
    p.add_argument('--duration',type=int,default=600)
    args=p.parse_args()
    if args.mode=='server' and args.ai_env is None:
        p.error('server mode requires --ai-env; implicit environment discovery is disabled')
    from dotenv import dotenv_values
    from tools.closed_telegram_ai import Settings
    from tools.closed_webhook import WebhookTelegram, ClosedWebhookTest
    logging.disable(logging.CRITICAL)
    secret=args.ingress_secret.read_text().strip()
    if len(secret)<32:raise ValueError('invalid private ingress secret')
    settings=Settings.load(dotenv_values(args.config,interpolate=False))
    telegram=WebhookTelegram(settings,os.getenv('TELEGRAM_PROXY_URL',''))
    provider=None
    import fcntl
    lock=args.state.with_name('https-'+args.mode+'.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        telegram.preflight()
        if args.mode=='server':
            from shared.ai_provider import build_selection_provider
            from werkzeug.serving import make_server
            ai=dotenv_values(args.ai_env,interpolate=False)
            provider=build_selection_provider('yandex',yandex_api_key=ai.get('YANDEX_AI_API_KEY',''),yandex_model=ai.get('YANDEX_AI_MODEL',''))
            if not provider.ready:raise ValueError('test AI unavailable')
            runner=ClosedWebhookTest(settings,telegram,args.state,provider)
            app=ingress_app(runner,secret,args.state.with_name('https-ingress.sqlite'))
            # Single request at a time: bot's scoped socket/thread patches must
            # never overlap another request in this process.
            server=make_server('unix://'+str(args.state.with_name('app.sock')),0,app,threaded=False)
            print(json.dumps({'ready':True,'bind':'private-unix-socket','mode':'server'}),flush=True)
            serve_bounded(server,args.duration)
        else:
            offset_file=args.state.with_name('bridge-offset.json')
            offset=json.loads(offset_file.read_text()) if offset_file.exists() else json.loads(args.state.read_text())['last_update']
            deadline=time.monotonic()+min(900,max(1,args.duration))
            print(json.dumps({'ready':True,'mode':'polling-to-private-HTTPS','public_webhook':False}),flush=True)
            while time.monotonic()<deadline:
                updates=telegram.call('getUpdates',offset=offset+1,timeout=20,allowed_updates=['message','callback_query'])
                for update in updates:
                    started=time.monotonic()
                    status,body=post_tls(update,secret,args.state.with_name('tls.sock'))
                    print(json.dumps({'https_status':status,'elapsed_ms':round((time.monotonic()-started)*1000)}),flush=True)
                    if status not in {200,400,403,415} or (status==403 and body.get('error')!='account_gate'):
                        raise RuntimeError('private ingress incomplete: inspect, do not automatically replay')
                    offset=update['update_id']
                    tmp=offset_file.with_suffix('.tmp')
                    with os.fdopen(os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600),'w') as out:json.dump(offset,out)
                    os.replace(tmp,offset_file)
    except Exception as exc:
        print(json.dumps({'error_class':type(exc).__name__}),flush=True)
        return 1
    finally:
        if provider and provider.client:provider.client.close()
        telegram.session.close();lock.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
