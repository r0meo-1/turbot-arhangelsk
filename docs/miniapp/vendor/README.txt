Telegram Mini Apps JavaScript SDK

Unmodified current official asset downloaded 2026-10-15 from:
https://telegram.org/js/telegram-web-app.js
SHA256: 124bdff7ba3fe86c8ba5143467afec21e020405e74abdb92cfdda6c0ee3b5908

Served from the Mini App origin because telegram.org requests time out on
the tested connection while the Mini App origin is reachable. Keep the SDK
before app.js so native initData and Bridge callbacks exist at startup.
Do not inject fabricated authentication data or replace native initData.

Official source project and MIT license:
https://github.com/TelegramMessenger/TGMiniAppsJsSDK
LICENSE copied from upstream revision e086c60734519ff816c3af5cf95def3edd7d12fc.
The hosted current SDK is newer than that repository snapshot; its own bytes
above are pinned independently, rather than claiming a matching repo commit.

To update: fetch the official hosted asset with TLS verification, review the
diff, retain the license, update this hash and the HTML cache version, mirror
all vendor files into docs/miniapp/vendor, and run the real-SDK browser test.
