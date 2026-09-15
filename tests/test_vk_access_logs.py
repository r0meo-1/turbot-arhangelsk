"""Guard deployment access-log formats against signed URL disclosure."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_vk_systemd_access_log_omits_query_and_referrer():
    unit = (ROOT / 'deploy/vk-turbot.service').read_text()
    raw = re.search(r"--access-logformat '([^']+)'", unit).group(1)
    # systemd consumes doubled percent signs before passing argv to Gunicorn.
    assert not re.search(r'(?<!%)%(?!%)', raw)
    fmt = raw.replace('%%', '%')
    signed_url = '/vk/miniapp/?vk_user_id=42&sign=SECRET_SIGNATURE'
    atoms = dict(h='127.0.0.1', t='[test]', m='GET', U='/vk/miniapp/',
                 H='HTTP/1.1', s='200', b='123', r=f'GET {signed_url} HTTP/1.1',
                 q='vk_user_id=42&sign=SECRET_SIGNATURE', f=signed_url)
    line = fmt % atoms
    assert 'GET /vk/miniapp/ HTTP/1.1' in line
    assert '200 123' in line
    assert 'SECRET_SIGNATURE' not in line and 'vk_user_id' not in line


@pytest.mark.parametrize('name,servers', [('nginx-turbot.conf', 2), ('nginx-compose.conf', 1)])
def test_nginx_access_logs_use_only_safe_fields(name, servers):
    config = (ROOT / 'deploy' / name).read_text()
    fmt = re.search(r"log_format turbot_safe '([^']+)';", config).group(1)
    fields = set(re.findall(r'\$([a-zA-Z_]+)', fmt))
    assert fields <= {'remote_addr', 'time_local', 'request_method', 'uri',
                      'server_protocol', 'status', 'body_bytes_sent'}
    assert {'request_method', 'uri', 'status'} <= fields
    assert config.count('access_log /var/log/nginx/access.log turbot_safe;') == servers
