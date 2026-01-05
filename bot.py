import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import logging
from flask import Flask, request
import asyncio
from threading import Thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://turbot-arhangelsk.onrender.com")
PARTNER_LINK = "https://partners.travelata.ru/?sid=kg87ezvoan"

DESTINATION, DATES, PEOPLE, BUDGET = range(4)
user_data = {}

app = Flask(__name__)
bot_app = None

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

def setup_bot_app():
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
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
    
    bot_app.add_handler(conv)
    asyncio.run(bot_app.initialize())
    asyncio.run(bot_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook"))
    logger.info(f"🤖 Webhook установлен: {WEBHOOK_URL}/webhook")

@app.route('/', methods=['GET'])
def index():
    return 'TurBot Архангельск is running!'

@app.route('/webhook', methods=['POST'])
def webhook():
    if bot_app is None:
        return 'Bot not initialized', 503
    
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    asyncio.run(bot_app.process_update(update))
    return 'OK'

if __name__ == "__main__":
    # Инициализируем бота в отдельном потоке
    setup_thread = Thread(target=setup_bot_app)
    setup_thread.start()
    setup_thread.join()
    
    port = int(os.getenv("PORT", 10000))
    logger.info(f"🚀 Запуск Flask на порту {port}")
    app.run(host='0.0.0.0', port=port)
