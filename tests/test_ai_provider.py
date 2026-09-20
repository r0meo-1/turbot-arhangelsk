import shared.ai_provider as providers


class _FakeGroq:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_template_provider_never_builds_external_client(monkeypatch):
    monkeypatch.setattr(providers, "Groq", _FakeGroq)
    monkeypatch.setattr(providers, "OpenAI", _FakeOpenAI)

    selected = providers.build_selection_provider(
        "template",
        groq_api_key="secret",
        regcloud_api_key="secret",
        regcloud_base_url="https://example.invalid/v1",
        regcloud_model="model",
    )

    assert selected.mode == "template"
    assert selected.ready is False


def test_regcloud_requires_https_endpoint(monkeypatch):
    monkeypatch.setattr(providers, "OpenAI", _FakeOpenAI)

    selected = providers.build_selection_provider(
        "regcloud",
        regcloud_api_key="secret",
        regcloud_base_url="http://example.invalid/v1",
        regcloud_model="gemma-test",
    )

    assert selected.mode == "regcloud"
    assert selected.ready is False


def test_regcloud_requires_explicit_key_endpoint_and_model(monkeypatch):
    monkeypatch.setattr(providers, "OpenAI", _FakeOpenAI)

    for kwargs in (
        {"regcloud_api_key": "", "regcloud_base_url": "https://ai.example/v1", "regcloud_model": "m"},
        {"regcloud_api_key": "k", "regcloud_base_url": "", "regcloud_model": "m"},
        {"regcloud_api_key": "k", "regcloud_base_url": "https://ai.example/v1", "regcloud_model": ""},
    ):
        selected = providers.build_selection_provider("regcloud", **kwargs)
        assert selected.ready is False


def test_regcloud_uses_openai_compatible_client(monkeypatch):
    monkeypatch.setattr(providers, "OpenAI", _FakeOpenAI)

    selected = providers.build_selection_provider(
        "regcloud",
        regcloud_api_key="server-only-key",
        regcloud_base_url="https://ai.example/v1/",
        regcloud_model="gemma-test",
    )

    assert selected.ready is True
    assert selected.mode == "regcloud"
    assert selected.model == "gemma-test"
    assert selected.client.kwargs == {
        "api_key": "server-only-key",
        "base_url": "https://ai.example/v1",
    }


def test_groq_provider_still_works(monkeypatch):
    monkeypatch.setattr(providers, "Groq", _FakeGroq)

    selected = providers.build_selection_provider(
        "groq",
        groq_api_key="groq-secret",
        groq_model="openai/gpt-oss-120b",
    )

    assert selected.ready is True
    assert selected.model == "openai/gpt-oss-120b"
    assert selected.client.kwargs == {"api_key": "groq-secret"}
