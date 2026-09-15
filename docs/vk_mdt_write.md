# VK → MDT write path

VK and Telegram share the same MDT account/key/base URL, but VK keeps a platform-specific source label.

- `MDT_ENABLED=true` enables CRM delivery.
- `MDT_BASE_URL` / `MDT_ACCOUNT` select the CRM account.
- `MDT_API_KEY` authenticates requests.
- `MDT_MODE=lead` remains the shared/Telegram default.
- `VK_MDT_MODE=preorder` sends VK requests with `/api/create-preorder`, which supports `preorder_manager_id`.
- When `MDT_MANAGER_IDS` is empty, VK assigns the sole active MDT manager; if multiple are active it leaves the request unassigned instead of guessing.
- `VK_MDT_SOURCE` controls the VK CRM source label and defaults to `VK Bot`.

The VK completion flow confirms the request to the client and notifies the manager before running MDT and other enrichment side effects. A CRM outage therefore must not make an already accepted VK request disappear.

`tests/test_vk_mdt_write.py` covers the VK preorder API sequence, sole-manager assignment, selected-tour/origin attribution, and failure isolation without touching the live CRM.
