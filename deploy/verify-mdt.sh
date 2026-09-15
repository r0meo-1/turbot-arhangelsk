#!/usr/bin/env bash
# Safe production diagnostics for MoiDokumenti-Turism integration.
# Never prints API keys, response bodies, tourist data, or other secrets.
set -Eeuo pipefail

repo=/opt/turbot
venv="$repo/venv/bin"

cd "$repo"

"$venv/python" - <<'PY'
import os
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv('/opt/turbot/.env')

def truthy(value: str) -> bool:
    return (value or '').strip().lower() in {'1', 'true', 'yes', 'on'}

enabled = truthy(os.getenv('MDT_ENABLED', 'false'))
mode = (os.getenv('MDT_MODE', 'lead') or 'lead').strip().lower()
account = (os.getenv('MDT_ACCOUNT', '') or '').strip()
base = (os.getenv('MDT_BASE_URL', '') or '').strip().rstrip('/')
api_key = (os.getenv('MDT_API_KEY', '') or '').strip()

endpoint = base or (f'https://{account}.moidokumenti.ru' if account else '')
endpoint_host = urlparse(endpoint).hostname or 'missing'

print(
    'MDT config: '
    f'enabled={"yes" if enabled else "no"} '
    f'mode={mode} '
    f'endpoint_host={endpoint_host} '
    f'api_key={"set" if api_key else "missing"}'
)

if not enabled:
    print('MDT check: integration disabled in production environment')
    raise SystemExit(0)

if mode not in {'lead', 'preorder', 'both'}:
    print(f'MDT check: WARNING invalid MDT_MODE={mode!r}')
    raise SystemExit(0)

if not endpoint or not api_key:
    print('MDT check: WARNING integration enabled but required configuration is incomplete')
    raise SystemExit(0)

try:
    response = requests.post(
        f'{endpoint}/api/get-country-list',
        data={'params': '{}', 'key': api_key},
        timeout=15,
    )
    content_type = (response.headers.get('content-type') or '').split(';', 1)[0].strip() or 'missing'
    final_host = urlparse(response.url).hostname or 'missing'
    print(
        'MDT HTTP: '
        f'status={response.status_code} '
        f'content_type={content_type} '
        f'redirects={len(response.history)} '
        f'final_host={final_host}'
    )
    response.raise_for_status()
    response.json()
except requests.HTTPError as exc:
    status = exc.response.status_code if exc.response is not None else 'unknown'
    print(f'MDT check: WARNING read-only API returned HTTP {status}')
except Exception as exc:
    print(f'MDT check: WARNING read-only API failed ({type(exc).__name__})')
else:
    print('MDT check: read-only API reachable')
PY
