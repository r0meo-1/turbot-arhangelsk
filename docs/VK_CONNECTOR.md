# VK Connector for ChatGPT

This is the first production-safe connector slice for TurBot. It exposes a
small read-only MCP-style JSON-RPC endpoint at:

`POST /mcp/vk`

The endpoint is disabled unless `VK_CONNECTOR_TOKEN` is configured with at
least 32 characters. VK community credentials remain server-side and are read
from the existing `VK_TOKEN` / `VK_ACCESS_TOKEN` plus
`VK_OWNER_ID` / `VK_GROUP_ID` configuration.

## Security contract

- `Authorization: Bearer <VK_CONNECTOR_TOKEN>` is required on every request.
- The connector token is separate from the VK access token.
- The VK access token is never returned in tool output.
- Request bodies are limited to 64 KiB.
- Query strings are rejected.
- Only JSON POST is accepted.
- Read results are projected to bounded, explicit fields instead of returning
  raw VK API payloads.
- Attribution actions use the existing privacy-minimized funnel table and
  return counts only, not customer names, phones, message bodies or platform
  user IDs.
- Write tools are not advertised.
- Direct calls to known write names such as `vk.create_post` or
  `vk.send_message` fail closed before any VK API call.

## Current tools

- `vk.get_group` — public metadata for the configured VK community.
- `vk.list_posts` — recent community wall posts with bounded text and public
  counters.
- `vk.get_post_stats` — aggregate public counters for one post owned by the
  configured community; post text, attachments and commenter identities are
  not returned.
- `vk.get_campaign_attribution` — privacy-safe VK funnel counts grouped by
  source tag.
- `vk.get_leads_by_source` — start/lead/manager-delivery counts for one
  validated source tag.

This slice intentionally does not expose messages, comments, customer records,
or any write action.

## Protocol

The endpoint supports both protocol eras used by the current MCP SDK:

Legacy 2025 flow:

- `initialize`
- `notifications/initialized`
- `ping`
- `tools/list`
- `tools/call`

Modern `2026-07-28` read/tool flow:

- `server/discover`
- per-request `_meta` protocol-version envelope;
- `MCP-Protocol-Version` and `Mcp-Method` header validation;
- exact `Mcp-Name` validation for `tools/call`;
- required `resultType=complete` wire discriminator;
- server identity under `_meta["io.modelcontextprotocol/serverInfo"]`.

The modern endpoint is intentionally stateless and JSON-response-only. It does
not yet implement `subscriptions/listen`, SSE notifications, resources,
prompts or multi-round input-required flows. None are needed by the current
read-only VK tools.

The connector identifies itself as `turbot-vk 0.2.0`.

## Local fixture check

Use a non-production connector token and fake/mock VK API calls in tests.
Never paste a real VK token into curl history or issue evidence.

A shape-only request looks like:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

and a tool call looks like:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "vk.list_posts",
    "arguments": {
      "limit": 10,
      "offset": 0
    }
  }
}
```

## Rollout

1. Merge and deploy with `VK_CONNECTOR_TOKEN` unset. The endpoint must return
   HTTP 503 even when presented with a Bearer value.
2. Generate a dedicated connector token outside Git and install it only in the
   protected runtime environment.
3. Verify missing/wrong connector authorization returns HTTP 401.
4. Verify `tools/list` exposes only the five read-only tools.
5. Exercise the read tools with the existing server-side VK credentials.
6. Verify both legacy `initialize` and modern `server/discover` fixture
   flows before enabling a client.
7. Do not add write tools until explicit per-action authorization and
   idempotency/audit semantics are implemented and accepted.

No production write is enabled by this slice.
