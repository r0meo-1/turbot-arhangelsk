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

# Default: run Telegram bot. Override with CMD for VK bot.
EXPOSE 5000 5100

CMD ["gunicorn", "bot:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "60"]
