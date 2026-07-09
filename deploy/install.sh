#!/usr/bin/env bash
#
# TurBot — automated installation on a clean Ubuntu/Debian VM in Russia.
#
# Usage (as root):
#   BOT_DOMAIN=bot.example.ru  BOT_REPO=https://github.com/.../turbot.git  \
#   bash install.sh
#
# What it does:
#   1. Installs system packages (python3-venv, nginx, certbot)
#   2. Creates a dedicated `turbot` system user
#   3. Clones the repo to /opt/turbot
#   4. Sets up a Python virtualenv with requirements.txt
#   5. Installs + enables the systemd service
#   6. Installs the nginx site config (domain placeholder → BOT_DOMAIN)
#   7. Issues a Let's Encrypt certificate (or skips if no domain yet)
#
# After the script finishes you still need to:
#   - Fill /opt/turbot/.env (BOT_TOKEN, ADMIN_ID, …) — see DEPLOY.md
#   - Run: sudo systemctl start turbot && sudo systemctl reload nginx
#   - Register the Telegram webhook (see DEPLOY.md step 7)
#
set -euo pipefail

DOMAIN="${BOT_DOMAIN:?Please set BOT_DOMAIN, e.g. BOT_DOMAIN=bot.example.ru}"
REPO="${BOT_REPO:?Please set BOT_REPO, e.g. BOT_REPO=https://github.com/.../turbot.git}"
APP_DIR="/opt/turbot"
APP_USER="turbot"

echo "==> [1/7] Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git nginx certbot \
    python3-certbot-nginx > /dev/null

echo "==> [2/7] Creating user '$APP_USER'"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> [3/7] Cloning repo to $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    echo "    $APP_DIR already exists — pulling latest"
    git -C "$APP_DIR" pull --ff-only
else
    rm -rf "$APP_DIR"
    git clone --depth 1 "$REPO" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> [4/7] Setting up Python virtualenv"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "==> [5/7] Installing systemd service"
cp "$APP_DIR/deploy/turbot.service" /etc/systemd/system/turbot.service
systemctl daemon-reload
systemctl enable turbot

echo "==> [6/7] Installing nginx site"
cp "$APP_DIR/deploy/nginx-turbot.conf" /etc/nginx/sites-available/turbot.conf
sed -i "s/BOT_DOMAIN/$DOMAIN/g" /etc/nginx/sites-available/turbot.conf
mkdir -p /var/www/certbot
if [ ! -L /etc/nginx/sites-enabled/turbot.conf ]; then
    ln -s /etc/nginx/sites-available/turbot.conf /etc/nginx/sites-enabled/
fi
# Remove default nginx site if it conflicts
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "==> [7/7] Issuing Let's Encrypt certificate for $DOMAIN"
certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
    --non-interactive --agree-tos --register-unsafely-without-email -v || \
    echo "    ⚠️  Certificate issue failed — DNS may not point here yet." \
         "Run manually once ready (see DEPLOY.md)."

echo ""
echo "✅ Installation complete."
echo ""
echo "Next steps:"
echo "  1. Edit  /opt/turbot/.env  — fill BOT_TOKEN, ADMIN_ID, etc."
echo "     (copy from /opt/turbot/.env.example)"
echo "  2. Start:  sudo systemctl start turbot"
echo "  3. Reload: sudo systemctl reload nginx"
echo "  4. Check:  sudo systemctl status turbot"
echo "  5. Register webhook — see DEPLOY.md step 7"
