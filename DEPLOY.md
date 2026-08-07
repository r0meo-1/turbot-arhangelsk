# Deploying TurBot to a Russian VM

Step-by-step guide for deploying the bot on a VPS / cloud VM **located in Russia**
(required by 152-ФЗ — personal data of RF citizens must be stored on RF territory).

Tested on **Ubuntu 22.04 / 24.04** and **Debian 12**.

---

## Quick start: a VPS with a bare IP (no domain)

The fast path when you already have a Russian VPS — reg.ru, Timeweb, Yandex
Cloud, anything with root over SSH — and no domain pointed at it yet.

**Telegram accepts a self-signed certificate** if you upload it with
`setWebhook`, so the bot works on a bare IP. The installer generates that
certificate with the IP in `subjectAltName`, which is what Telegram checks.

```bash
ssh root@<VM_IP>

apt-get update && apt-get install -y git
git clone https://github.com/r0meo-1/turbot-arhangelsk.git /tmp/turbot
cd /tmp/turbot

BOT_IP=<VM_IP> \
BOT_REPO=https://github.com/r0meo-1/turbot-arhangelsk.git \
bash deploy/install.sh
```

Then fill in the secrets and start it:

```bash
nano /opt/turbot/.env          # BOT_TOKEN, ADMIN_ID, TELEGRAM_SECRET_TOKEN
systemctl start turbot
systemctl status turbot --no-pager
```

Register the webhook — note `-F` and the certificate upload, both required in
self-signed mode:

```bash
# Pull out just the two values you need. Sourcing the whole file drags every
# secret into your shell environment and breaks on any value with a space.
BOT_TOKEN=$(sed -n 's/^BOT_TOKEN=//p' /opt/turbot/.env | tr -d '"'"'"'"'"'"')
SECRET=$(sed -n 's/^TELEGRAM_SECRET_TOKEN=//p' /opt/turbot/.env | tr -d '"'"'"'"'"'"')

curl -sS "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
  -F "url=https://<VM_IP>/webhook" \
  -F "secret_token=$SECRET" \
  -F "certificate=@/etc/ssl/turbot/fullchain.pem"

curl -sS "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```

`getWebhookInfo` should echo your URL with an empty `last_error_message`.
Check the service itself with `curl -sk https://<VM_IP>/health` — `-k` because
the certificate is self-signed.

### The catch with a bare IP

Telegram is happy; **browsers are not**. Anyone opening `/health` or
`/privacy` gets a full-page security warning, because nothing vouches for a
self-signed certificate. That is fine for a bot nobody visits in a browser,
and bad for a link you put in a portfolio.

If you own any domain, pointing one A record at the VM and re-running the
installer in domain mode costs about five minutes and removes the warning
everywhere:

```bash
BOT_DOMAIN=bot.example.ru \
BOT_REPO=https://github.com/r0meo-1/turbot-arhangelsk.git \
bash deploy/install.sh
```

Certbot renews on its own, and the webhook then needs no certificate upload.

### If Telegram cannot reach your server

Some hosts filter Telegram's address ranges. The symptom is specific and worth
recognising, because it looks like a broken bot rather than a broken network:

```
getWebhookInfo → last_error_message: Connection timed out
                 pending_update_count: 2
nginx access.log → no requests from Telegram at all
```

The bot is fine; the delivery never arrives. No certificate, secret or nginx
change fixes this — the route belongs to the provider. Switch to polling,
where the bot opens the connection itself:

```bash
echo 'BOT_MODE=polling' >> /opt/turbot/.env
systemctl restart turbot
```

Nothing else changes: the same handlers run, `/health` and `/privacy` keep
working, and the poller removes the registered webhook on start (without
dropping updates already queued). To go back, set `BOT_MODE=webhook` and
re-register.

Outbound may be filtered too. Check which of Telegram's addresses answer —
they are not all treated the same:

```bash
for ip in 149.154.167.220 149.154.166.110; do
  timeout 6 bash -c "echo > /dev/tcp/$ip/443" 2>/dev/null \
    && echo "$ip open" || echo "$ip blocked"
done
```

If one answers and DNS hands you another, pin the working one in `/etc/hosts`.
Treat that as a stopgap: Telegram rotates addresses, and the pin will go stale.
Raise a ticket with the provider quoting the exact addresses — it is far more
actionable than "Telegram does not work".

### What the installer sets up beyond the bot

- `DEMO_MODE=false` in the generated `.env` — a VM in Russia is the real
  deployment, so the showcase banner and phone masking stay off.
- `/etc/cron.d/turbot-backup` — nightly SQLite backup at 03:00, keeping seven
  copies. The script always documented this cron line but nothing installed it.
- An nginx route for the VK bot (`/vk/` → `127.0.0.1:5100`). Add
  `INSTALL_VK=1` to also install and enable that service.
- `turbot-watchdog@turbot.timer` — checks `/health` every two minutes and
  restarts the service when the bot reports itself deaf. See
  [section 11](#11-watchdog-when-the-bot-is-up-but-not-answering).
  `SKIP_WATCHDOG=1` opts out.

---

## 0. Why not Render.com?

This project ran on Render's free tier and moved off it. Two reasons, both
measured rather than assumed: the instance slept, and a cold start regularly
exceeded 45 seconds — for a webhook bot that is lost updates and, for a
portfolio link, a closed tab. The filesystem is also ephemeral, so every
redeploy wiped the SQLite database.

The deciding reason is legal, though: personal data of RF citizens must be
stored on RF territory (152-ФЗ ст. 18 ч. 5), and Render is US-hosted.

Russian providers worth a look: Timeweb Cloud (cheapest, simplest), Yandex
Cloud (longest free trial), Selectel (most reliable). Any of them runs this
bot comfortably on 1 vCPU / 1 GB — the two bots together use ~310 MB.

---

## 1. Create a VM

1. Sign up at your chosen provider (e.g. [timeweb.cloud](https://timeweb.cloud),
   [console.yandex.cloud](https://console.yandex.cloud)).
2. Create a cloud server:
   - **OS**: Ubuntu 22.04 LTS or 24.04 LTS
   - **Specs**: 1 vCPU, 1 GB RAM, 10 GB SSD — more than enough
   - **Region**: any Russian data center (Москва / Санкт-Петербург)
3. Note the **public IP** of the VM.
4. Optionally point a domain's **A record** at that IP. A domain is *not*
   required — the installer falls back to a self-signed certificate, which
   Telegram accepts (see the quick-start section above). It is still worth
   having: a real certificate removes the browser warning on `/health` and
   `/privacy`, and a `.ru` domain costs ~200 ₽/year.

---

## 2. SSH into the VM and run the installer

```bash
ssh root@<VM_IP>

# Clone or upload the repo, then run the installer:
BOT_DOMAIN=bot.example.ru \
BOT_REPO=https://github.com/your-username/turbot-arhangelsk.git \
bash deploy/install.sh
```

The installer does everything: installs packages, creates a `turbot` user,
sets up the venv, installs systemd + nginx, and issues an SSL certificate.

If DNS isn't pointing to the VM yet, the certificate step will fail — that's
OK, just re-run it later (step 5).

---

## 3. Configure environment variables

```bash
cp /opt/turbot/.env.example /opt/turbot/.env
nano /opt/turbot/.env
```

Fill in the essentials:

```ini
BOT_TOKEN=123456:ABC...          # from @BotFather
ADMIN_ID=123456789               # your chat ID (get from @userinfobot)
AI_MODE=template                 # or groq (needs GROQ_API_KEY)
TELEGRAM_SECRET_TOKEN=long-random-string   # optional but recommended
DATABASE_PATH=/opt/turbot/bot_state.sqlite

# 152-ФЗ compliance
PRIVACY_POLICY_URL=https://your-domain/privacy
DATA_RETENTION_DAYS=180
```

Then fix ownership:

```bash
chown turbot:turbot /opt/turbot/.env
chmod 600 /opt/turbot/.env        # restrict access
```

---

## 4. Start the bot

```bash
sudo systemctl start turbot
sudo systemctl status turbot     # should be "active (running)"
sudo journalctl -u turbot -f     # tail logs in real time
```

---

## 5. SSL certificate (if not done by the installer)

If DNS is now pointing to your VM but the installer skipped the certificate:

```bash
certbot certonly --webroot -w /var/www/certbot -d bot.example.ru \
    --non-interactive --agree-tos -m your@email.com
```

Then reload nginx:

```bash
sudo systemctl reload nginx
```

Set up auto-renewal (certbot does this automatically, but verify):

```bash
certbot renew --dry-run
```

---

## 6. Verify the bot is reachable

```bash
curl https://bot.example.ru/health
# Expected: {"status":"ok","revision":"...",...}
```

---

## 7. Register the Telegram webhook

Tell Telegram where to send updates:

```bash
TOKEN="123456:ABC..."            # your bot token
SECRET="long-random-string"      # matches TELEGRAM_SECRET_TOKEN in .env
DOMAIN="bot.example.ru"

curl "https://api.telegram.org/bot${TOKEN}/setWebhook" \
    -d "url=https://${DOMAIN}/webhook" \
    -d "secret_token=${SECRET}"
```

Verify:

```bash
curl "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
# "url" should show your webhook, "last_error_message" should be null
```

Send `/start` to the bot in Telegram — you should see a consent prompt.

---

## 8. Daily operations

| Action              | Command                                |
| ------------------- | -------------------------------------- |
| View logs           | `journalctl -u turbot -f`              |
| Restart bot         | `sudo systemctl restart turbot`        |
| Stop bot            | `sudo systemctl stop turbot`           |
| Update from repo    | `cd /opt/turbot && bash scripts/deploy.sh` |
| Dry-run an update   | `cd /opt/turbot && bash scripts/deploy.sh --check` |
| Backup database     | `cp /opt/turbot/bot_state.sqlite /backup/` |

---

## 9. Security checklist

- [x] VM is in a Russian data center (152-ФЗ)
- [x] `.env` has `chmod 600` and is owned by `turbot` user
- [x] `TELEGRAM_SECRET_TOKEN` is set — webhook rejects calls without it
- [x] HTTPS via Let's Encrypt — auto-renews
- [x] Bot runs as unprivileged `turbot` user (systemd hardening)
- [x] SQLite database is on the VM disk (not a foreign cloud)
- [ ] Firewall: open only ports 22 (SSH), 80, 443 — `ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable`
- [ ] Notify Roskomnadzor about personal-data processing
- [ ] Publish privacy policy and set `PRIVACY_POLICY_URL`
- [ ] Regular database backups (cron + encrypted copy)

---

## 10. VK.com group bot (optional)

The repo includes a separate `vk_bot.py` that runs the same tour-selection
dialog, AI suggestions, 152-ФЗ consent, and MDT CRM integration — but for a
VKontakte group instead of Telegram.

### Setup a VK group

1. Create a group on [vk.com](https://vk.com) (or use an existing one).
2. Go to **Manage → Settings → API usage → Callback API**.
3. Enable Callback API.
4. Set the callback URL to `https://bot.example.ru/vk/webhook` (your domain).
5. Copy the **confirmation string** — VK sends it to verify server ownership.
6. Go to **API usage → Access tokens** and create a token with
   `messages` permission. Copy the token.
7. Copy the **group ID** from the group settings page.

### Configure

Add these to `/opt/turbot/.env`:

```ini
VK_ACCESS_TOKEN=vk1.a.BcDeFg...      # from step 6
VK_GROUP_ID=123456                   # from step 7
VK_CONFIRMATION=abc123def456         # from step 5
VK_SECRET_KEY=<long-random-string>   # same value in Callback API settings
VK_PORT=5100
VK_DATABASE_PATH=/opt/turbot/vk_bot_state.sqlite

# Optional: Tourvisor package-tour search in the VK review step.
# Keep the real JWT only in /opt/turbot/.env, never in Git.
TOURVISOR_TOKEN=
# VK_TOURVISOR_ENABLED=true
```

### Install the systemd service

```bash
sudo cp /opt/turbot/deploy/vk-turbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vk-turbot
sudo systemctl start vk-turbot
sudo systemctl status vk-turbot
```

### Add nginx route for VK webhook

Add this `location` block inside the existing HTTPS `server` block in
`/etc/nginx/sites-available/turbot.conf`:

```nginx
location /vk/webhook {
    proxy_pass http://127.0.0.1:5100;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Verify

```bash
curl https://bot.example.ru/vk/webhook -X POST \
    -H "Content-Type: application/json" \
    -d '{"type":"confirmation","group_id":123456}'
# Should return your confirmation string
```

Then go back to VK group settings and click "Confirm" — VK will send the
verification request and your server should respond with the confirmation string.

---

## 11. Watchdog: when the bot is up but not answering

`Restart=always` only covers a process that dies. Twice this bot stayed up and
stopped answering, which systemd is perfectly happy to call success:

```
● turbot.service - TurBot
     Active: active (running)          <- true, and useless
```

`/health` returned `200` both times, the log was silent, and the outage was
found by a client who got no reply. So the health check was made capable of
failing.

### What /health reports now

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool | head -6
```

```json
{
    "status": "ok",
    "seconds_since_poll_ok": 3.1,
    "poll_stale_after": 180,
    "seconds_since_update": 412.7
}
```

`seconds_since_poll_ok` is the age of the last **completed** `getUpdates` call,
empty results included — proof the link to Telegram is alive without waiting
for a client to write in. Past `POLL_STALE_AFTER` (default 180s) the endpoint
answers **HTTP 503** with `"status": "degraded"`.

Under `BOT_MODE=webhook` nothing is expected to poll, so the field is `null`
and never degrades.

### What the watchdog does

`turbot-watchdog@turbot.timer` runs every two minutes:

1. `GET /health`. Exit quietly on `200`.
2. Otherwise restart the unit and send the admin a Telegram message.
3. Re-check after 15s and report whether the restart helped.

A 600-second cooldown (`WATCHDOG_COOLDOWN`) keeps a genuine Telegram outage
from becoming a restart loop — if restarting did not help, doing it again every
two minutes will not either, and the alert is the useful part.

```bash
systemctl list-timers 'turbot-watchdog*' --no-pager   # is it armed
journalctl -u 'turbot-watchdog@turbot' --since today  # what it saw
/opt/turbot/deploy/watchdog.sh turbot                 # run it by hand
```

The script reads `BOT_TOKEN` and `ADMIN_ID` from `.env` with `sed`, never
`source` — an unquoted value in that same file once turned a config read into
command execution. The alert is sent via `curl -K -`, so the token never
appears in `ps`.

Add `INSTALL_VK=1` at install time and the VK bot gets its own timer. There the
check is weaker by nature — VK is inbound, so there is no heartbeat to age —
but it still catches a wedged gunicorn that stopped answering HTTP.
