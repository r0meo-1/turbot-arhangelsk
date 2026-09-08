import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from edge_bot_playwright import run_search

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Edge automation bot ready. Use /search <query> to run a Bing search in Edge."
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <query>")
        return
    query = " ".join(context.args)
    await update.message.reply_text(f"Searching for: {query}")
    try:
        title = await run_search(query)
        await update.message.reply_text(f"Page title: {title}")
    except Exception as e:
        await update.message.reply_text(f"Error running Playwright: {e}")

def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        print("Set TELEGRAM_TOKEN environment variable or create edge_bot.env and load it.")
        return
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    print("Bot started. Press Ctrl-C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
