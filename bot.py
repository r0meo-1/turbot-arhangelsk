import os
import logging
from flask import Flask, request
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
UON_API_KEY = os.getenv("UON_API_KEY", "SqHP1egva6LTrL08U763")  # API-ключ U-ON

app = Flask(__name__)
user_data = {}

STATES = {'destination': 1, 'dates': 2, 'people': 3, 'budget': 4, 'phone': 5}

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, json={'chat_id': chat_id, 'text': text})
    logger.info(f"Sent to {chat_id}: {response.status_code} - {response.text[:100]}")

@app.route('/')
def index():
    return 'TurBot Архангельск is running!'

def send_to_uon_crm(chat_id, destination, dates, people, budget, phone):
    """
    Отправляет заявку в U-ON CRM
    """
    try:
        # Формируем данные для создания лида
        lead_data = {
            "u_name": destination,
            "u_phone": phone,
            "u_note": f"Направление: {destination}\nДаты: {dates}\nЛюдей: {people}\nБюджет: {budget}\nТелефон: {phone}"
        }
        
        # Отправляем запрос в U-ON API
        response = requests.post(
            f"https://api.u-on.ru/{UON_API_KEY}/lead/create.json",
            data=lead_data
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
            send_message(chat_id, "🌴 Здравствуйте! Я помогу подобрать тур под ваши пожелания.\n\n📍 Куда бы вы хотели отправиться?")
        
        elif chat_id in user_data:
            state = user_data[chat_id].get('state')
            
            if state == 'destination':
                user_data[chat_id]['destination'] = text
                user_data[chat_id]['state'] = 'dates'
                send_message(chat_id, "📅 На какие даты планируете поездку? (например: 15-22 июня)")
            
            elif state == 'dates':
                user_data[chat_id]['dates'] = text
                user_data[chat_id]['state'] = 'people'
                send_message(chat_id, "👥 Сколько человек будет путешествовать?")
            
            elif state == 'people':
                user_data[chat_id]['people'] = text
                user_data[chat_id]['state'] = 'budget'
                send_message(chat_id, "💰 Какой бюджет рассматриваете на человека? (в рублях)")
            
            elif state == 'budget':
                user_data[chat_id]['budget'] = text
                user_data[chat_id]['state'] = 'phone'
                send_message(chat_id, "📱 Укажите ваш номер телефона для связи:")
            
            elif state == 'phone':
                data_info = user_data[chat_id]
                phone = text
                
                # Отправляем подтверждение заявки
                send_message(chat_id, f"✅ Ваша заявка принята! Наш менеджер свяжется с вами в ближайшее время.\n\n📍 Направление: {data_info['destination']}\n📅 Даты: {data_info['dates']}\n👥 Человек: {data_info['people']}\n💰 Бюджет: {data_info['budget']}\n📱 Телефон: {phone}\n\nСпасибо за обращение в турагентство Апрель Тур! 🌺")
                
                # Отправляем заявку в U-ON CRM
                send_to_uon_crm(
                    chat_id,
                    data_info['destination'],
                    data_info['dates'],
                    data_info['people'],
                    data_info['budget'],
                    phone
                )
                
                # Очищаем данные пользователя
                del user_data[chat_id]
    
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
    
    return 'OK'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
