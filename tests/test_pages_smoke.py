"""Exercise the actual Pages workflow shell with isolated HTTP responses."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import textwrap

import pytest


BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Pages workflow executes in Bash")
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/pages.yml"
MARKER = "PAGE_BODY_MUST_NOT_APPEAR_IN_LOGS"
PAGES = {
    "/": "TurBot × АПРЕЛЬ тур",
    "/apreltour/": 'Наталья Ильина data-turbot-link rel="canonical"',
    "/apreltour/privacy.html": "Политика обработки персональных данных ИП Замятина Мария Андреевна",
    "/apreltour/consent.html": "ИП Замятина Мария Андреевна",
    "/robots.txt": "Sitemap: https://r0meo1.ru/sitemap.xml",
    "/sitemap.xml": "https://r0meo1.ru/apreltour/",
}


def _run(pages, *, fetch_fails=False):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Verify published TurBot site\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
    # No network or real sleeps: response fixtures exercise the deployed script.
    cases = "\n".join(
        f"{shlex.quote('https://pages.example/project' + path + '?rev=test-revision')}) "
        f"printf '%s' {shlex.quote(body + ' ' + MARKER)} ;;"
        for path, body in pages.items()
    )
    mock = f'''curl() {{
      [[ "$*" == *'Cache-Control: no-cache'* ]] || return 22
      {"return 22" if fetch_fails else ":"}
      case "${{@: -1}}" in
        {cases}
        *) return 22 ;;
      esac
    }}
    sleep() {{ :; }}
    '''
    result = subprocess.run(
        [BASH], input=mock + script, encoding="utf-8", capture_output=True,
        env={**os.environ, "PAGE_URL": "https://pages.example/project/", "GITHUB_SHA": "test-revision"},
        timeout=20,
    )
    assert MARKER not in result.stdout + result.stderr
    return result


def test_pages_smoke_accepts_complete_deployment_without_custom_host_requests():
    result = _run(PAGES)
    assert result.returncode == 0, result.stderr
    assert "TurBot Pages smoke: ok" in result.stdout
    assert "attempt" not in result.stdout


@pytest.mark.parametrize("path,removed,added,diagnostic", [
    ("/", "TurBot × АПРЕЛЬ тур", "", "page_home"),
    ("/apreltour/", "Наталья Ильина", "", "page_canonical_manager"),
    ("/apreltour/", "data-turbot-link", "", "page_canonical_link"),
    ("/apreltour/", 'rel="canonical"', "", "page_canonical_tag"),
    ("/apreltour/privacy.html", "Политика обработки персональных данных", "", "page_privacy_title"),
    ("/apreltour/privacy.html", "ИП Замятина Мария Андреевна", "", "page_privacy_operator"),
    ("/apreltour/consent.html", "ИП Замятина Мария Андреевна", "", "page_consent_operator"),
    ("/apreltour/privacy.html", "", "ЧЕРНОВИК / ШАБЛОН", "page_privacy_no_draft"),
    ("/apreltour/privacy.html", "", "[указать оператора]", "page_privacy_no_placeholder"),
    ("/apreltour/consent.html", "", "[указать оператора]", "page_consent_no_placeholder"),
    ("/robots.txt", "Sitemap: https://r0meo1.ru/sitemap.xml", "", "page_robots_sitemap"),
    ("/sitemap.xml", "https://r0meo1.ru/apreltour/", "", "page_sitemap_canonical"),
])
def test_pages_smoke_reports_each_failure_without_logging_bodies(path, removed, added, diagnostic):
    pages = {**PAGES, path: PAGES[path].replace(removed, "") + added}
    result = _run(pages)
    assert result.returncode == 1
    assert f"{diagnostic}=fail" in result.stderr
    assert sum(line.endswith("=fail") for line in result.stderr.splitlines()) == 1
    assert "attempt 11/12" in result.stdout


def test_pages_smoke_rejects_http_failure():
    result = _run(PAGES, fetch_fails=True)
    assert result.returncode == 1
    assert "page_privacy_operator=fail" in result.stderr
    assert "TurBot Pages smoke failed" in result.stderr
