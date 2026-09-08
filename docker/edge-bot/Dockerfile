# Edge bot image using Playwright's Python image with browsers preinstalled
FROM mcr.microsoft.com/playwright/python:1.62.0-focal

WORKDIR /app

# Install python deps
COPY edge_bot_requirements.txt ./
RUN pip install --no-cache-dir -r edge_bot_requirements.txt

# Copy app
COPY edge_bot_playwright.py edge_bot_bot.py edge_bot.env.example ./

ENV HEADLESS=1

CMD ["python", "edge_bot_bot.py"]
