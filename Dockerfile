FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (shared/ is required — both bots import it)
COPY bot.py vk_bot.py ./
COPY shared/ ./shared/
COPY deploy/ ./deploy/
COPY docs/ ./docs/

# Create data directory for SQLite
RUN mkdir -p /app/data

# Run unprivileged. The systemd units already drop privileges deliberately
# (NoNewPrivileges, ProtectSystem=strict); the container path had none of that
# discipline and ran gunicorn as root.
RUN useradd --system --create-home --shell /usr/sbin/nologin turbot \
    && chown -R turbot:turbot /app
USER turbot

# Declare the data directory so a bare `docker run` (no compose) does not
# silently keep SQLite inside the throwaway container layer.
VOLUME ["/app/data"]

# Default: run Telegram bot. Override with CMD for VK bot.
# --workers MUST stay 1: dialog sessions and update dedup live in process
# memory, so a second worker would split them and corrupt in-flight dialogs.
EXPOSE 5000 5100

CMD ["gunicorn", "bot:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "60"]
