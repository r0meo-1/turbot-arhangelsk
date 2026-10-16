# Telegram releases while VK moderation is pending

`VK_MODERATION_FREEZE=true` selects **Deploy Telegram only**. The legacy broad deploy/bootstrap jobs are skipped. The server marker `/etc/turbot/vk-moderation-freeze` independently refuses legacy deployment/config protocols before any application files, settings, units, permissions or dependencies can change.

The root-owned receiver `/opt/turbot-tg-drive/telegram_deploy.py` accepts only `bot.py` and its SHA256 from tested current main, verifies the manifest of existing Python runtime dependencies and requirements, compiles before writing, backs up the previous Telegram source/revision, replaces only `bot.py` and the Telegram revision marker, and restarts only `turbot.service`. Health failure rolls back Telegram only. Repeated delivery of the same source/revision verifies health without restarting. Existing VK process, source, assets, state, config, dependencies and units stay untouched. This narrow lane intentionally refuses dependency changes; it is not a general Telegram frontend deploy.

GitHub variable `TURBOT_DEPLOY_KNOWN_HOSTS` pins the already-verified public server host key; no trust-on-first-use scan. Repository secrets are reused; no new account or purchases.

Receiver bootstrap is an explicit root-admin step, not executable code from an incoming release payload. The installed entrypoint is backed up before adding the freeze guard. Removing the server marker **and** setting `VK_MODERATION_FREEZE=false` re-enables broad releases; do this only after explicit authorization to resume VK releases. Keep the freeze while VK is under review.
