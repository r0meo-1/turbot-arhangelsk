from pathlib import Path

from shared.legal_identity import LEGAL_OPERATOR_DISPLAY


ROOT = Path(__file__).resolve().parents[1]

PUBLIC_LEGAL_FILES = (
    ROOT / "docs" / "privacy_policy.md",
    ROOT / "docs" / "apreltour" / "privacy.html",
    ROOT / "docs" / "apreltour" / "consent.html",
    ROOT / "docs" / "turbot" / "privacy.html",
    ROOT / "docs" / "turbot" / "consent.html",
)


def test_public_legal_documents_share_verified_operator_without_placeholders():
    for path in PUBLIC_LEGAL_FILES:
        text = path.read_text(encoding="utf-8")
        assert LEGAL_OPERATOR_DISPLAY in text, path
        assert "ЧЕРНОВИК / ШАБЛОН" not in text, path
        assert "[указать" not in text, path
        assert "<strong>Оператор:</strong> ТА «АПРЕЛЬ тур»." not in text, path


def test_nginx_hsts_is_host_scoped_without_include_subdomains():
    text = (ROOT / "deploy" / "nginx-turbot.conf").read_text(encoding="utf-8")
    assert 'Strict-Transport-Security "max-age=31536000"' in text
    assert "includeSubDomains" not in text
