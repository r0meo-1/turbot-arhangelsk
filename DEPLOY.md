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

### What the installer sets up beyond the bot

- `DEMO_MODE=false` in the generated `.env` — a VM in Russia is the real
  deployment, so the showcase banner and phone masking stay off.
- `/etc/cron.d/turbot-backup` — nightly SQLite backup at 03:00, keeping seven
  copies. The script always documented this cron line but nothing installed it.
- An nginx route for the VK bot (`/vk/` → `127.0.0.1:5100`). Add
  `INSTALL_VK=1` to also install and enable that service.

---

## 0. Why not Render.com?

The included `render.yaml` deploys to **Render.com (USA)**. That's fine for a
demo, but **personal data (names, phone numbers) must not be stored on foreign
servers** (152-ФЗ ст. 18 ч. 5). Use a Russian provider instead:

| Provider        | Free start        | Cheapest VPS  | Notes                          |
| --------------- | ----------------- | ------------- | ------------------------------ |
| Yandex Cloud    | ~4000 ₽ grant     | ~500 ₽/мес    | Best free trial, solid infra    |
| VK Cloud        | ~3000 ₽ bonus     | ~400 ₽/мес    | Good trial credits              |
| Timeweb Cloud   | trial period      | ~150–200 ₽/мес | Simplest, cheapest long-term   |
| Beget           | trial             | ~200 ₽/мес    | Friendly panel                  |
| Selectel        | —                 | from ~600 ₽/мес | Most reliable, enterprise-grade |

For a lightweight Telegram bot, **Timeweb Cloud** is the sweet spot (cheap +
simple), and **Yandex Cloud** gives you the longest free runway.

### Demo / portfolio on free Render (optional keep-alive)

Free Render **sleeps ~15 minutes** without HTTP traffic. Cold start = slow first
message. For a “try the bot” demo (not real client phones):

1. Deploy with `render.yaml` (or Blueprint).
2. Open `https://YOUR-SERVICE.onrender.com/health` — should return JSON.
3. **GitHub keep-alive** (already in the repo):
   - Repo → **Settings → Secrets and variables → Actions**
   - Secret name: `RENDER_HEALTH_URL`
   - Value: `https://YOUR-SERVICE.onrender.com/health`
   - Workflow: [`.github/workflows/keep-alive.yml`](.github/workflows/keep-alive.yml)  
     runs every 10 minutes + manual **Run workflow**.
4. Or use [UptimeRobot](https://uptimerobot.com) / cron-job.org → same URL every 5 min  
   (often more reliable than GitHub Actions cron, which can lag).

Do **not** use this for production personal data. SQLite on free Render is also
ephemeral unless you add a persistent disk.

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
# Expected: {"status":"ok","bot_token_configured":true,...}
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
| Update from repo    | `cd /opt/turbot && git pull && sudo systemctl restart turbot` |
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
VK_PORT=5100
VK_DATABASE_PATH=/opt/turbot/vk_bot_state.sqlite
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
