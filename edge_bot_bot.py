import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from edge_bot_playwright import run_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
MAX_QUERY_LENGTH = 200

# load environment file if present
load_dotenv('edge_bot.env')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Edge automation bot ready.\n"
        "Use /search <query> to run a Bing search in Edge."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/start - show a welcome message\n"
        "/search <query> - search Bing in Edge\n"
        "/help - show this help"
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <query>")
        return
    query = " ".join(context.args)
    if len(query) > MAX_QUERY_LENGTH:
        await update.message.reply_text(
            f"Please keep the query under {MAX_QUERY_LENGTH} characters."
        )
        return
    await update.message.reply_text(f"Searching for: {query}")
    try:
        # Add a timeout to prevent long-running Playwright calls
        title = await asyncio.wait_for(run_search(query), timeout=30)
        await update.message.reply_text(f"Page title: {title}")
    except asyncio.TimeoutError:
        logger.exception("Playwright search timed out")
        await update.message.reply_text("Search timed out. Try again later.")
    except Exception:
        logger.exception("Error running Playwright")
        await update.message.reply_text("Error running Playwright. See bot logs for details.")

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        print("Set TELEGRAM_TOKEN environment variable or create edge_bot.env and load it.")
        return
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    print("Bot started. Press Ctrl-C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
