"""Two instrumented save probes against an unchanged pinned application snapshot.

This is diagnostic coverage, not a replacement for the browser release suite.
Only allowlisted metadata is recorded: never query strings, bodies or headers.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest
from flask import Flask
from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server
from shared.vk_miniapp import create_blueprint

SNAPSHOT = Path(os.environ['PR238_SNAPSHOT']).resolve()
REPORTS = Path(os.environ['PR238_REPORTS']).resolve()
PINNED = '5a9abe482f9e86fdd11b9ee13714a93f839dd606'
SECRET = 'vk-miniapp-e2e-secret'
API_URL = 'https://bot.r0meo1.ru/miniapp/submit'
OFFLINE_TEXT = 'Нет подключения к интернету. Проверьте сеть и повторите.'
STATUS_TEXTS = {
    '', OFFLINE_TEXT, 'Сохраняем параметры…', 'Передаём параметры в TurBot…',
    'Параметры сохранены. Возвращаемся в TurBot…',
    'Failed to fetch', 'Load failed',
    'TurBot отвечает слишком долго. Проверьте связь и повторите.',
    'Ответ задержался. Повторите попытку: одинаковые параметры не создадут дубль.',
    'Параметры сохранены. Бот уже подготовил проверку. Откройте чат: вводить команду не нужно. Заявка менеджеру ещё не отправлена.',
    'Параметры сохранены. Откройте чат и напишите «Проверить заявку»: бот покажет ваш подбор. Заявка менеджеру ещё не отправлена.',
}
PATHS = {
    '/', '/index.html', '/app.js', '/styles.css', '/favicon.ico',
    '/vk/miniapp/', '/vk/miniapp/app.js', '/vk/miniapp/styles.css',
    '/vk/miniapp/vk-bridge.js', '/vk/miniapp/legal.js',
    '/vk/miniapp/draft', '/miniapp/submit', '/js/telegram-web-app.js',
}
METHODS = {'GET', 'POST', 'OPTIONS', 'HEAD', 'PUT', 'PATCH', 'DELETE'}


def request_meta(url: str, method: str) -> dict:
    parts = urlsplit(url)
    return {
        'method': method if method in METHODS else 'OTHER',
        'path': parts.path if parts.path in PATHS else 'REDACTED_OTHER_PATH',
        'origin_class': 'loopback' if parts.hostname in {'127.0.0.1', 'localhost', '::1'} else 'external',
    }


class Probe:
    def __init__(self, channel):
        self.started = time.monotonic()
        self.data = {
            'channel': channel, 'snapshot': PINNED, 'navigator_overridden': False,
            'application_files_modified': False, 'events': [], 'states': [],
            'fixture_deliveries': 0, 'fetch_calls': [], 'cleanup': {},
            'flow_result': 'INCOMPLETE',
        }

    def event(self, kind, **metadata):
        self.data['events'].append({
            'kind': kind, 'elapsed_ms': round((time.monotonic() - self.started) * 1000),
            **metadata,
        })

    def attach(self, page):
        page.on('request', lambda r: self.event('request', **request_meta(r.url, r.method)))
        page.on('response', lambda r: self.event('response', status=r.status, **request_meta(r.url, r.request.method)))

        def failed(r):
            match = re.search(r'ERR_[A-Z0-9_]+', str(r.failure or ''))
            self.event('requestfailed', error_code=match.group(0) if match else 'UNKNOWN', **request_meta(r.url, r.method))

        page.on('requestfailed', failed)
        page.on('pageerror', lambda e: self.event('pageerror', error_class=e.name if e.name in {'Error', 'TypeError', 'ReferenceError', 'SyntaxError', 'RangeError'} else 'OTHER'))
        page.on('console', lambda message: self.event('console', level=message.type if message.type in {'log', 'info', 'warning', 'error', 'debug'} else 'OTHER'))

    def state(self, page, phase):
        state = page.evaluate('''() => ({
          online: navigator.onLine,
          status_text: document.querySelector('#status')?.textContent ?? '',
          save_disabled: document.querySelector('#save')?.disabled ?? null,
          save_hidden: document.querySelector('#save')?.hidden ?? null,
          chat_hidden: document.querySelector('#chat')?.hidden ?? null,
          tg_closed: typeof window.__tgClosed === 'boolean' ? window.__tgClosed : null,
          review_busy: document.querySelector('#review')?.getAttribute('aria-busy') ?? null
        })''')
        if state['status_text'] not in STATUS_TEXTS:
            state['status_text'] = 'REDACTED_UNRECOGNIZED_STATUS'
        state['phase'] = phase
        self.data['states'].append(state)
        self.data['fetch_calls'] = page.evaluate('window.__qaFetchMeta')
        return state

    def write(self):
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / (self.data['channel'] + '.json')).write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


# Passive wrapper: records method/path only and calls the original fetch with
# its original receiver/arguments. It does not replace navigator.onLine.
FETCH_INSTRUMENTATION = '''(() => {
  const original = window.fetch;
  const paths = new Set(['/vk/miniapp/draft', '/miniapp/submit']);
  window.__qaFetchMeta = [];
  window.fetch = function(input, init) {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = String(init?.method ?? input?.method ?? 'GET').toUpperCase();
    window.__qaFetchMeta.push({
      method: ['GET','POST','OPTIONS','HEAD','PUT','PATCH','DELETE'].includes(method) ? method : 'OTHER',
      path: paths.has(url.pathname) ? url.pathname : 'REDACTED_OTHER_PATH'
    });
    return Reflect.apply(original, this, arguments);
  };
})();'''

TG_STUB = '''(() => {
  window.__tgClosed = false;
  window.Telegram = {WebApp: {
    initData: 'query_id=e2e&auth_date=2099999999&hash=e2e-placeholder',
    initDataUnsafe: {start_param: 'thailand'},
    ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {},
    close() {window.__tgClosed = true;}, sendData() {},
    BackButton: {show() {}, hide() {}, onClick() {}},
    HapticFeedback: {selectionChanged() {}, notificationOccurred() {}}
  }};
})();'''


class QuietWSGI(WSGIRequestHandler):
    def log(self, kind, message, *args):
        # Browser events provide method/path/status without signed query strings.
        pass


class QuietHTTP(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@contextmanager
def local_server(channel, probe):
    if channel == 'vk':
        app = Flask('pr238-diagnostic')
        def save_draft(*_):
            probe.data['fixture_deliveries'] += 1
        app.register_blueprint(create_blueprint(save_draft, lambda: (SECRET, '54475121', 240310110)))
        server = make_server('127.0.0.1', 0, app, request_handler=QuietWSGI)
    else:
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHTTP, directory=str(SNAPSHOT / 'miniapp')))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    try:
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            try:
                server.shutdown()
            finally:
                thread.join(timeout=5)
    finally:
        server.server_close()
        probe.data['cleanup'].update(server_socket_closed=server.socket.fileno() == -1, server_thread_stopped=not thread.is_alive())
    assert not thread.is_alive(), 'diagnostic test server did not stop'


@contextmanager
def browser_page(probe):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        try:
            context = browser.new_context(viewport={'width': 390, 'height': 760})
            try:
                context.add_init_script(FETCH_INSTRUMENTATION)
                if probe.data['channel'] == 'telegram':
                    context.add_init_script(TG_STUB)
                page = context.new_page()
                probe.attach(page)
                yield page
            finally:
                context.close()
                probe.data['cleanup']['browser_context_closed'] = True
        finally:
            browser.close()
            probe.data['cleanup']['browser_closed'] = True


def signed_query():
    params = dict(vk_app_id='54475121', vk_group_id='240310110', vk_platform='desktop_web',
                  vk_ts=str(int(time.time())), vk_user_id='424242')
    data = urlencode(sorted(params.items()))
    params['sign'] = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), data.encode(), hashlib.sha256).digest()).decode().rstrip('=')
    return urlencode(params)


def run_probe(channel):
    probe = Probe(channel)
    success = False
    try:
        with local_server(channel, probe) as origin:
            with browser_page(probe) as page:
                page.set_default_timeout(5000)
                if channel == 'vk':
                    page.goto(origin + '/vk/miniapp/?' + signed_query(), wait_until='domcontentloaded')
                    page.evaluate("window.vkBridge.send = () => Promise.resolve({result: true})")
                else:
                    page.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.abort())
                    def accept_submit(route, request):
                        probe.event('fixture_intercept', **request_meta(request.url, request.method))
                        if request.method == 'POST':
                            probe.data['fixture_deliveries'] += 1
                        route.fulfill(status=200, content_type='application/json', body='{"ok":true}', headers={'Access-Control-Allow-Origin': '*'})
                    page.route(API_URL, accept_submit)
                    page.goto(origin + '/index.html', wait_until='domcontentloaded')
                page.locator('#destination').fill('Пхукет, Таиланд')
                page.locator('#departure').fill('Архангельск')
                page.locator('#consent').check()
                if channel == 'vk':
                    page.locator('#terms-accepted').check()
                page.locator('#submit').click()
                page.locator('#review').wait_for(state='visible')
                before = probe.state(page, 'before_save')
                assert before['save_disabled'] is False, 'save was not enabled'
                page.locator('#save').click()
                try:
                    page.wait_for_function('''() => {
                      const status = document.querySelector('#status')?.textContent ?? '';
                      return status !== '' && !['Сохраняем параметры…', 'Передаём параметры в TurBot…'].includes(status);
                    }''', timeout=17000)
                except PlaywrightTimeout:
                    probe.data['settlement_timeout'] = True
                after = probe.state(page, 'after_save')
                success = (after['chat_hidden'] is False if channel == 'vk' else after['tg_closed'] is True)
                probe.data['flow_result'] = 'PASS' if success else 'FAIL'
                probe.data['offline_guard_evidence'] = (
                    before['online'] is False and after['status_text'] == OFFLINE_TEXT
                    and len(probe.data['fetch_calls']) == 0 and probe.data['fixture_deliveries'] == 0)
    except Exception as exc:
        # Full pytest assertion traceback remains useful without raw signed URLs.
        probe.data['diagnostic_exception_type'] = type(exc).__name__
        raise AssertionError('diagnostic setup or interaction failed; inspect sanitized JSON') from None
    finally:
        probe.write()
    return success


@pytest.mark.parametrize('channel', ['vk', 'telegram'])
def test_instrumented_save(channel):
    assert run_probe(channel), 'save flow failed; inspect sanitized state and request evidence'
