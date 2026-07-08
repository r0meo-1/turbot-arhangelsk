# Deploying TurBot to a Russian VM

Step-by-step guide for deploying the bot on a VPS / cloud VM **located in Russia**
(required by 152-ФЗ — personal data of RF citizens must be stored on RF territory).

Tested on **Ubuntu 22.04 / 24.04** and **Debian 12**.

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

---

## 1. Create a VM

1. Sign up at your chosen provider (e.g. [timeweb.cloud](https://timeweb.cloud),
   [console.yandex.cloud](https://console.yandex.cloud)).
2. Create a cloud server:
   - **OS**: Ubuntu 22.04 LTS or 24.04 LTS
   - **Specs**: 1 vCPU, 1 GB RAM, 10 GB SSD — more than enough
   - **Region**: any Russian data center (Москва / Санкт-Петербург)
3. Note the **public IP** of the VM.
4. Point your domain's **A record** to that IP. You'll need a domain for the
   HTTPS webhook — a `.ru` domain costs ~200 ₽/year.

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
