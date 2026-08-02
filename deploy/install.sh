#!/usr/bin/env bash
#
# TurBot — automated installation on a clean Ubuntu/Debian VM in Russia.
#
# Two modes, pick one:
#
#   Domain (recommended — real certificate, no browser warnings):
#     BOT_DOMAIN=bot.example.ru BOT_REPO=https://github.com/you/turbot.git \
#       bash install.sh
#
#   Bare IP (self-signed certificate):
#     BOT_IP=185.1.2.3 BOT_REPO=https://github.com/you/turbot.git \
#       bash install.sh
#
#   Telegram accepts a self-signed certificate when you upload it with
#   setWebhook, so the bot works fine on a bare IP. Humans do not: anyone
#   opening /health or /privacy in a browser gets a full-page security
#   warning. If those links are meant to be shown to people, use a domain.
#
# Options:
#   INSTALL_VK=1     also install and enable the VK bot service
#   SKIP_BACKUP=1    do not install the nightly backup cron job
#
set -euo pipefail

REPO="${BOT_REPO:?Set BOT_REPO, e.g. BOT_REPO=https://github.com/you/turbot.git}"
DOMAIN="${BOT_DOMAIN:-}"
IP_ADDR="${BOT_IP:-}"
APP_DIR="/opt/turbot"
APP_USER="turbot"
SELF_SIGNED_DIR="/etc/ssl/turbot"

if [ -z "$DOMAIN" ] && [ -z "$IP_ADDR" ]; then
    echo "ERROR: set BOT_DOMAIN=<domain> or BOT_IP=<address>." >&2
    exit 1
fi
if [ -n "$DOMAIN" ] && [ -n "$IP_ADDR" ]; then
    echo "ERROR: set only one of BOT_DOMAIN / BOT_IP." >&2
    exit 1
fi

if [ -n "$DOMAIN" ]; then
    MODE="domain"; HOST="$DOMAIN"
    CERT_PATH="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
    SERVER_NAME="$DOMAIN"
else
    MODE="ip"; HOST="$IP_ADDR"
    CERT_PATH="$SELF_SIGNED_DIR/fullchain.pem"
    KEY_PATH="$SELF_SIGNED_DIR/privkey.pem"
    SERVER_NAME="_"
fi

TOTAL=9
echo "==> [1/$TOTAL] Installing system packages (mode: $MODE, host: $HOST)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS="python3 python3-venv python3-pip git nginx sqlite3 openssl curl"
[ "$MODE" = "domain" ] && PKGS="$PKGS certbot python3-certbot-nginx"
apt-get install -y -qq $PKGS > /dev/null

echo "==> [2/$TOTAL] Creating user '$APP_USER'"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> [3/$TOTAL] Cloning repo to $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    echo "    already present — pulling latest"
    git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
    git -C "$APP_DIR" pull --ff-only
else
    rm -rf "$APP_DIR"
    git clone --depth 1 "$REPO" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> [4/$TOTAL] Setting up Python virtualenv"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> [5/$TOTAL] Preparing .env"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    {
        printf '\n# --- written by install.sh ---\n'
        # A bot on your own VM in Russia is the real deployment, not a showcase.
        printf 'DEMO_MODE=false\n'
        # Lets the bot build the link to its own /privacy page in the consent
        # text. Render exposes RENDER_EXTERNAL_URL for this; a VM does not.
        printf 'PUBLIC_BASE_URL=https://%s\n' "$HOST"
    } >> "$APP_DIR/.env"
    echo "    created from .env.example — FILL IT IN before starting"
else
    echo "    already exists — left untouched"
fi
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
# Holds BOT_TOKEN and CRM credentials: owner-only.
chmod 600 "$APP_DIR/.env"

echo "==> [6/$TOTAL] Installing systemd services"
cp "$APP_DIR/deploy/turbot.service" /etc/systemd/system/turbot.service
if [ "${INSTALL_VK:-0}" = "1" ]; then
    cp "$APP_DIR/deploy/vk-turbot.service" /etc/systemd/system/vk-turbot.service
    echo "    VK service installed"
fi
systemctl daemon-reload
systemctl enable turbot > /dev/null
[ "${INSTALL_VK:-0}" = "1" ] && systemctl enable vk-turbot > /dev/null

echo "==> [7/$TOTAL] Obtaining the TLS certificate"
mkdir -p /var/www/certbot
if [ "$MODE" = "domain" ]; then
    # nginx must answer on :80 for the webroot challenge, so stand up a
    # minimal HTTP-only site first and swap in the full config afterwards.
    cat > /etc/nginx/sites-available/turbot.conf <<NGINX_BOOTSTRAP
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'bootstrap'; add_header Content-Type text/plain; }
}
NGINX_BOOTSTRAP
    ln -sf /etc/nginx/sites-available/turbot.conf /etc/nginx/sites-enabled/turbot.conf
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl restart nginx
    certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
        --non-interactive --agree-tos --register-unsafely-without-email || {
        echo "ERROR: certificate issue failed. Does $DOMAIN resolve to this server?" >&2
        echo "       Fix DNS and re-run this script." >&2
        exit 1
    }
else
    mkdir -p "$SELF_SIGNED_DIR"
    if [ ! -f "$CERT_PATH" ]; then
        # Telegram matches the URL host against the certificate, so the IP
        # must appear as a SAN, not only as the common name.
        openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
            -keyout "$KEY_PATH" -out "$CERT_PATH" \
            -subj "/C=RU/O=TurBot/CN=$IP_ADDR" \
            -addext "subjectAltName=IP:$IP_ADDR" 2>/dev/null
        chmod 600 "$KEY_PATH"
        echo "    self-signed certificate created for IP:$IP_ADDR"
    else
        echo "    self-signed certificate already present"
    fi
fi

echo "==> [8/$TOTAL] Installing the nginx site"
cp "$APP_DIR/deploy/nginx-turbot.conf" /etc/nginx/sites-available/turbot.conf
sed -i "s|BOT_DOMAIN|$SERVER_NAME|g; s|SSL_CERT_PATH|$CERT_PATH|g; s|SSL_KEY_PATH|$KEY_PATH|g" \
    /etc/nginx/sites-available/turbot.conf
ln -sf /etc/nginx/sites-available/turbot.conf /etc/nginx/sites-enabled/turbot.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "==> [9/$TOTAL] Installing the nightly backup job"
if [ "${SKIP_BACKUP:-0}" = "1" ]; then
    echo "    skipped (SKIP_BACKUP=1)"
else
    chmod +x "$APP_DIR/scripts/backup.sh"
    # The script documented its own cron line, but nothing ever installed it,
    # so backups existed only in theory.
    cat > /etc/cron.d/turbot-backup <<CRON
# TurBot — nightly SQLite backup, keeps the last 7 copies.
0 3 * * * root $APP_DIR/scripts/backup.sh >> /var/log/turbot-backup.log 2>&1
CRON
    chmod 644 /etc/cron.d/turbot-backup
    echo "    /etc/cron.d/turbot-backup installed (03:00 daily)"
fi

WEBHOOK_URL="https://$HOST/webhook"

cat <<DONE

✅ Installation complete — mode: $MODE, host: $HOST

Next steps
──────────
1. Fill in the secrets:
     nano $APP_DIR/.env
   Required: BOT_TOKEN, ADMIN_ID. Strongly recommended: TELEGRAM_SECRET_TOKEN
   (generate one with:  openssl rand -hex 32 ).

2. Start the bot:
     systemctl start turbot
     systemctl status turbot --no-pager
DONE

if [ "${INSTALL_VK:-0}" = "1" ]; then
    echo "     systemctl start vk-turbot"
fi

cat <<DONE

3. Register the Telegram webhook. Source .env so the token never lands in
   your shell history:

     set -a; . $APP_DIR/.env; set +a
DONE

if [ "$MODE" = "domain" ]; then
    cat <<DONE
     curl -sS "https://api.telegram.org/bot\$BOT_TOKEN/setWebhook" \\
       -d "url=$WEBHOOK_URL" \\
       -d "secret_token=\$TELEGRAM_SECRET_TOKEN"
DONE
else
    cat <<DONE
     curl -sS "https://api.telegram.org/bot\$BOT_TOKEN/setWebhook" \\
       -F "url=$WEBHOOK_URL" \\
       -F "secret_token=\$TELEGRAM_SECRET_TOKEN" \\
       -F "certificate=@$CERT_PATH"

   Uploading the certificate is what makes a self-signed setup work: Telegram
   pins it for this webhook. Re-upload whenever the certificate changes.
DONE
fi

cat <<DONE

4. Verify:
     curl -sk https://$HOST/health
     curl -sS "https://api.telegram.org/bot\$BOT_TOKEN/getWebhookInfo"

   getWebhookInfo should show your URL and an empty last_error_message.
DONE
