from shared.vk_miniapp import MINIAPP_BUTTON_TEXT, build_open_app_button


def test_open_app_button_uses_existing_vk_app_and_community():
    button = build_open_app_button("54475121", 240310110, enabled=True)

    assert button == {
        "action": {
            "type": "open_app",
            "app_id": 54475121,
            "owner_id": -240310110,
            "label": MINIAPP_BUTTON_TEXT,
            "hash": "bot",
        }
    }


def test_open_app_button_is_hidden_until_runtime_is_configured():
    assert build_open_app_button("", 240310110, enabled=True) is None
    assert build_open_app_button("54475121", 0, enabled=True) is None
    assert build_open_app_button("54475121", 240310110, enabled=False) is None
