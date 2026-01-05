import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import logging
from flask import Flask, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-app.onrender.com
PARTNER_LINK = "https://partners.travelata.ru/?sid=kg87ezvoan"

DESTINATION, DATES, PEOPLE, BUDGET = range(4)
user_data = {}

app = Flask(__name__)
tg_app = None

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

def setup_application():
    application = Application.builder().token(BOT_TOKEN).build()
    
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
    
    application.add_handler(conv)
    return application

@app.route('/', methods=['GET'])
def index():
    return 'TurBot Архангельск is running!'

@app.route('/webhook', methods=['POST'])
async def webhook():
    global tg_app
    if tg_app is None:
        tg_app = setup_application()
        await tg_app.initialize()
        await tg_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        logger.info(f"🤖 Webhook установлен: {WEBHOOK_URL}/webhook")
    
    update = Update.de_json(request.get_json(force=True), tg_app.bot)
    await tg_app.process_update(update)
    return 'OK'

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    logger.info(f"🚀 Запуск Flask на порту {port}")
    app.run(host='0.0.0.0', port=port)
