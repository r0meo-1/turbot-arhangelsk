import os
import logging
from flask import Flask, request
import requests
from urllib.parse import urlencode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
PARTNER_SID = "kg87ezvoan"  # Ваш партнерский ID Travelata
UON_API_KEY = os.getenv("UON_API_KEY", "SqHP1egva6LTrL08U763")  # API-ключ U-ON
UON_URL = "https://apreltour.u-on.ru"  # URL вашей системы U-ON

app = Flask(__name__)
user_data = {}

STATES = {'destination': 1, 'dates': 2, 'people': 3, 'budget': 4}

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, json={'chat_id': chat_id, 'text': text})
    logger.info(f"Sent to {chat_id}: {response.status_code} - {response.text[:100]}")

def generate_partner_link(destination, people):
    """
    Генерирует партнерскую ссылку Travelata с параметрами клиента
    """
    # Базовая ссылка поиска туров
    base_url = "https://partners.travelata.ru/search"
    
    # Параметры поиска
    params = {
        'fromCity': 2,  # Москва (по умолчанию)
        'adults': people,
        'sid': PARTNER_SID  # Ваш партнерский ID - комиссия идет вам!
    }
    
    # Формируем URL с параметрами
    query_string = urlencode(params)
    partner_url = f"{base_url}?{query_string}"
    
    return partner_url

@app.route('/')
def index():
    return 'TurBot Архангельск is running!'

def send_to_uon_crm(chat_id, destination, dates, people, budget):
    """
    Отправляет заявку в U-ON CRM
    """
    try:
        # Формируем данные для создания лида
        lead_data = {
            "r_name": f"Telegram заявка {chat_id}",
            "r_u_name": destination,
            "r_cl_company": f"Telegram ID: {chat_id}",
            "r_note": f"Направление: {destination}\nДаты: {dates}\nЛюдей: {people}\nБюджет: {budget}",
            "tourists": people,
            "price": budget
        }
        
        # Отправляем запрос в U-ON API
        response = requests.post(
            f"{UON_URL}/api/lead/create",
            params={"token": UON_API_KEY},
            json=lead_data
        )
        
        if response.status_code == 200:
            logger.info(f"Lead created in U-ON CRM: {response.json()}")
            return True
        else:
            logger.error(f"Failed to create lead in U-ON: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending to U-ON CRM: {e}")
        return False

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if 'message' not in data:
            return 'OK'
        
        message = data['message']
        chat_id = message['chat']['id']
        text = message.get('text', '')
        
        if text == '/start':
            user_data[chat_id] = {'state': 'destination'}
            send_message(chat_id, "🌴 Привет! Я помогу подобрать идеальный тур! 🔥\n\nКуда хотите поехать?")
        
        elif chat_id in user_data:
            state = user_data[chat_id].get('state')
            
            if state == 'destination':
                user_data[chat_id]['destination'] = text
                user_data[chat_id]['state'] = 'dates'
                send_message(chat_id, "📅 Когда планируете поездку?")
            
            elif state == 'dates':
                user_data[chat_id]['dates'] = text
                user_data[chat_id]['state'] = 'people'
                send_message(chat_id, "👥 Сколько человек поедет?")
            
            elif state == 'people':
                user_data[chat_id]['people'] = text
                user_data[chat_id]['state'] = 'budget'
                send_message(chat_id, "💰 Какой бюджет на человека?")
            
            elif state == 'budget':
                data_info = user_data[chat_id]
                
                # Отправляем подтверждение заявки
                send_message(chat_id, f"✅ Заявка принята!\n\n📍 {data_info['destination']}\n📅 {data_info['dates']}\n👥 {data_info['people']}\n💰 {text}")

                                # Отправляем заявку в U-ON CRM
                send_to_uon_crm(
                    chat_id,
                    data_info['destination'],
                    data_info['dates'],
                    data_info['people'],
                    text
                )
                
                # Генерируем персонализированную партнерскую ссылку
                try:
                    people_count = int(data_info['people'])
                                    except:
except:
                    people_count = 2
                
                partner_link = generate_partner_link(
                    data_info['destination'],
                    people_count
                )
                
                # Отправляем ссылку с призывом к действию
                send_message(
                    chat_id,
                    f"🔥 Подборка туров специально для вас:\n\n"
                    f"👉 {partner_link}\n\n"
                    f"💡 Переходите по ссылке и выбирайте лучший тур!\n"
                    f"📞 Есть вопросы? Пишите /start"
                )
                
                del user_data[chat_id]
        
        return 'OK'
    except Exception as e:
        logger.error(f"Error: {e}")
        return 'OK'

if __name__ == '__main__':
    port = int(os.getenv("PORT", 10000))
    logger.info(f"🚀 Запуск Flask на порту {port}")
    app.run(host='0.0.0.0', port=port)
