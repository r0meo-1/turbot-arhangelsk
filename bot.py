import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
PARTNER_LINK = "https://partners.travelata.ru/?sid=kg87ezvoan"

DESTINATION, DATES, PEOPLE, BUDGET = range(4)
user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🌴 Привет, {user.first_name}!\n\n"
        f"Я помогу подобрать идеальный тур! 🏖️\n\n"
        f"Куда хотите поехать?"
    )
    return DESTINATION

async def get_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['destination'] = update.message.text
    await update.message.reply_text(f"Отлично! 🌍\n\nКогда планируете вылет?")
    return DATES

async def get_dates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['dates'] = update.message.text
    await update.message.reply_text(f"📅 Сколько человек?")
    return PEOPLE

async def get_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['people'] = update.message.text
    await update.message.reply_text(f"👥 Бюджет?")
    return BUDGET

async def get_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['budget'] = update.message.text
    data = user_data[user_id]
    await update.message.reply_text(
        f"✅ Заявка принята!\n\n"
        f"📍 {data['destination']}\n📅 {data['dates']}\n👥 {data['people']}\n💰 {data['budget']}"
    )
    await update.message.reply_text(
        f"🔥 Лучшие туры:\n\n👉 {PARTNER_LINK}"
    )
    user_data.pop(user_id, None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено. /start")
    return ConversationHandler.END

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_destination)],
            DATES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dates)],
            PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people)],
            BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_budget)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    logger.info("🤖 Бот запущен!")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
