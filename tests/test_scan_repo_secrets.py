from scripts.scan_repo_secrets import scan_text


def test_secret_scanner_ignores_documentation_placeholders():
    text = """
BOT_TOKEN=
BOT_TOKEN=123456789:replace_me
GROQ_API_KEY=<secret>
password=secret
sk-not-a-real-key
"""
    assert scan_text(text) == []


def test_secret_scanner_detects_high_confidence_values_without_returning_value():
    fake_telegram = "123456789:" + ("A" * 35)
    fake_github = "ghp_" + ("B" * 36)
    fake_private_key = "-----BEGIN " + "PRIVATE KEY-----"
    text = "\n".join((fake_telegram, fake_github, fake_private_key))

    findings = scan_text(text)

    assert findings == [
        (1, "telegram_bot_token"),
        (2, "github_classic_token"),
        (3, "private_key"),
    ]
    rendered = repr(findings)
    assert fake_telegram not in rendered
    assert fake_github not in rendered
