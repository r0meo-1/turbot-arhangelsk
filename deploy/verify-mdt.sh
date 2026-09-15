#!/usr/bin/env bash
# Safe production diagnostics for MoiDokumenti-Turism integration.
# Never prints API keys, response bodies, tourist data, or other secrets.
set -Eeuo pipefail

repo=/opt/turbot
venv="$repo/venv/bin"

cd "$repo"

"$venv/python" - <<'PY'
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv('/opt/turbot/.env')

def truthy(value: str) -> bool:
    return (value or '').strip().lower() in {'1', 'true', 'yes', 'on'}

def print_outbox_summary() -> None:
    raw = (os.getenv('VK_DATABASE_PATH') or os.getenv('DATABASE_PATH') or 'vk_bot_state.sqlite').strip()
    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = Path('/opt/turbot') / db_path
    if not db_path.exists():
        print('MDT outbox: database=missing')
        return
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='leads'"
        ).fetchone()
        if not table:
            print('MDT outbox: leads_table=missing')
            return
        counts = {
            str(row['status'] or 'unset'): int(row['count'])
            for row in conn.execute(
                "SELECT mdt_status AS status, COUNT(*) AS count FROM leads GROUP BY mdt_status"
            ).fetchall()
        }
        latest = conn.execute(
            "SELECT id, mdt_status, mdt_attempts, mdt_next_retry_at, created_at "
            "FROM leads ORDER BY id DESC LIMIT 1"
        ).fetchone()
        counts_text = ','.join(f'{key}:{counts[key]}' for key in sorted(counts)) or 'none'
        if latest is None:
            print(f'MDT outbox: counts={counts_text} latest=none')
            return
        now = int(time.time())
        retry_at = latest['mdt_next_retry_at']
        retry_due = 'n/a' if retry_at is None else ('yes' if int(retry_at) <= now else 'no')
        age = max(0, now - int(latest['created_at'] or now))
        print(
            'MDT outbox: '
            f'counts={counts_text} '
            f'latest_id={int(latest["id"])} '
            f'latest_status={latest["mdt_status"] or "unset"} '
            f'latest_attempts={int(latest["mdt_attempts"] or 0)} '
            f'retry_due={retry_due} '
            f'age_seconds={age}'
        )
    except Exception as exc:
        print(f'MDT outbox: WARNING diagnostics failed ({type(exc).__name__})')
    finally:
        try:
            conn.close()
        except Exception:
            pass

enabled = truthy(os.getenv('MDT_ENABLED', 'false'))
mode = (os.getenv('MDT_MODE', 'lead') or 'lead').strip().lower()
vk_mode = (os.getenv('VK_MDT_MODE', mode) or mode).strip().lower()
account = (os.getenv('MDT_ACCOUNT', '') or '').strip()
base = (os.getenv('MDT_BASE_URL', '') or '').strip().rstrip('/')
api_key = (os.getenv('MDT_API_KEY', '') or '').strip()

endpoint = base or (f'https://{account}.moidokumenti.ru' if account else '')
endpoint_host = urlparse(endpoint).hostname or 'missing'

print(
    'MDT config: '
    f'enabled={"yes" if enabled else "no"} '
    f'mode={mode} '
    f'vk_mode={vk_mode} '
    f'endpoint_host={endpoint_host} '
    f'api_key={"set" if api_key else "missing"}'
)
print_outbox_summary()

if not enabled:
    print('MDT check: integration disabled in production environment')
    raise SystemExit(0)

if mode not in {'lead', 'preorder', 'both'}:
    print(f'MDT check: WARNING invalid MDT_MODE={mode!r}')
    raise SystemExit(0)
if vk_mode not in {'lead', 'preorder', 'both'}:
    print(f'MDT check: WARNING invalid VK_MDT_MODE={vk_mode!r}')
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
