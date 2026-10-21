# Private HTTPS test ingress

`tools/closed_https.py` adds a real TLS/reverse-proxy boundary to the closed
webhook runner. It is not public Telegram push delivery or a production server.
Telegram getUpdates feeds authenticated HTTPS requests into an isolated nginx,
then the account gate and the existing local webhook/dispatcher/sender runner.
No setWebhook/deleteWebhook call, public bind, DNS change or production reload.

## Isolation

Use the same dedicated test bot and verified exact private user/chat ID. No
other users/groups or unowned callbacks reach bot execution. Ingress secret is
random, ephemeral, mode 600, outside Git and never in a URL or access log.
A valid secret and account are required before receipt DB writes or bot/model
execution. The upstream runner retains its VK/CRM/supplier/funnel/booking/payment
blocks, dummy bot credentials and scoped Telegram sender. No new keys or paid
instances are created.

The final nginx template is `deploy/closed-https-nginx.conf.example`. Replace
`__CLOSED_HTTPS_ROOT__` only in a separate mode-700 test directory. Run a separate
nginx master with its own prefix/config/PID and a short systemd RuntimeMaxSec.
Do not write sites-enabled or reload the working nginx. Existing certificate/key
are read in place; never copy or print the private key. The certificate must be
current and have bot.r0meo1.ru in SAN. There is no verify=False or insecure curl.

Both nginx TLS and backend use private Unix sockets (tls.sock/app.sock), mode
600, owned by turbot after startup. No server TCP listener is created. The single
Werkzeug worker is intentional: scoped socket/thread patches must not overlap.
PrivateTLS verifies the real CA/hostname/SNI while connecting to tls.sock.

A local SSH forward may expose that Unix socket on a free **127.0.0.1-only**
client port. Verify using curl --connect-to while retaining the real HTTPS
hostname. Wrong hostname must fail certificate validation; absent secret ->403.
A TCP connect probe alone is not proof of origin reachability: in this session
it reported success even after the test server had stopped. The final topology
therefore has no server TCP ingress at all.

## HTTP contract and replay

Only /closed-webhook (POST) and /closed-health (authenticated GET) are proxied.
Wrong Host ->421; unknown route ->404; unsupported method is denied. Body size
is 16 KiB; malformed JSON ->400, wrong media type ->415, too large ->413,
missing/wrong secret ->403. Foreign user/chat/callback ->403 before model/state.
The bridge advances only legitimate account-gate rejections; authentication
failure stops it for inspection instead of silently dropping user updates.

`https_ingress_receipts` records update ID, canonical payload hash, status and
time, with no raw body/headers. A completed exact replay ->200 without rerun;
conflicting payload for that ID ->409; interrupted/uncertain processing ->503
and manual review. These receipts are distinct from API delivery acceptance and
personal /ack. Never purge HTTPS/webhook replay fences to resend an uncertain
notification. The bridge has a separate persistent offset and process lock.

## Closed run

Create a private ingress.secret with an existing trusted server tool, copy the
stopped test runner state and preserve offsets/quotas. Confirm no previous poller
is active. Start the Unix backend and isolated nginx, verify socket ownership,
then run the bridge with the same state and secret. Separate transient units
must have RuntimeMaxSec, UMask=0077, NoNewPrivileges and restricted writable paths.
Only the app/bridge receive the existing private Telegram proxy EnvironmentFile;
production /opt/turbot and /etc/turbot remain inaccessible to them.

The entrypoints are `closed_https.py server` and `closed_https.py bridge`, with
--config (dedicated bot), --state, --ingress-secret and --ai-env for server only.
The bridge's duration is bounded; no loopback TCP or public-host option exists.
Private per-request logs contain only HTTP status and elapsed milliseconds.

Acceptance: negative HTTP tests, trusted certificate + wrong-host rejection,
real account button reply, marked manager notice and human /ack. Reports must
separate local TLS coverage from public Telegram webhook ingress, which remains
unverified. Probe passes do not represent extra live calls or production metrics.

Stop bridge first, then backend and isolated nginx, then close the exact SSH
forward. Check zero test processes and listeners, matching production env/nginx
config hashes and unchanged production service start times. Do not revoke keys,
delete receipts or alter production to roll back this test.
