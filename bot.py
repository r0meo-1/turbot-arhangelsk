import os
import logging
from flask import Flask, request
import requests
from groq import Groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
UON_API_KEY = os.getenv("UON_API_KEY", "SqHP1egva6LTrL08U763")  # API-ключ U-ON
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # ID админа (мамы) для пересылки подборок
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # Groq API для AI-подборок
TRAVELATA_PARTNER_ID = os.getenv("TRAVELATA_PARTNER_ID", "hoevf8zmd4")  # Партнерский ID Travelata

app = Flask(__name__)
user_data = {}
all_users = set()  # Хранение всех chat_id пользователей

STATES = {'destination': 1, 'dates': 2, 'people': 3, 'budget': 4, 'phone': 5}

def send_message(chat_id, text, parse_mode=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    response = requests.post(url, json=payload)
    logger.info(f"Sent to {chat_id}: {response.status_code} - {response.text[:100]}")
    return response

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
                        "source": "Telegram бот",
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
        
        
        def generate_ai_selection(destination, dates, people, budget):
    """
    Генерирует AI-подборку с партнерской ссылкой Travelata
    """
    try:
        if not GROQ_API_KEY:
            # Если нет GROQ API, просто возвращаем базовое сообщение
            travelata_url = f"https://partners.travelata.ru/search?fromCity=2&toCountry={destination}&sid={TRAVELATA_PARTNER_ID}"
            return f"🔍 🌴 Подбираю для вас лучшие туры!\n\n👉 Посмотреть актуальные предложения: {travelata_url}\n\nℹ️ Наш менеджер свяжется с вами и подготовит персональную подборку!"
        
        # Инициализируем Groq клиент
        groq_client = Groq(api_key=GROQ_API_KEY)
        
        # Генерируем промпт для AI
        prompt = f"""Ты - эксперт по туризму турагентства "Апрель Тур" из Архангельска.

Клиент хочет:
- Направление: {destination}
- Даты: {dates}
- Количество человек: {people}
- Бюджет: {budget} рублей

Напиши короткое (3-4 предложения), дружелюбное сообщение с:
- Что ожидает в этом направлении
- Почему это отличный выбор
- Что взять с собой

Используй эмодзи. Не упоминай цены и конкретные отели."""
        
        response = groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.7
        )
        
        ai_text = response.choices[0].message.content
        
        # Формируем партнерскую ссылку Travelata
        travelata_url = f"https://partners.travelata.ru/search?fromCity=2&toCountry={destination}&sid={TRAVELATA_PARTNER_ID}"
        
        result = f"🌴 Ваша подборка туров\n\n{ai_text}\n\n🔍 Посмотреть актуальные предложения: {travelata_url}\n\nℹ️ Наш менеджер скоро свяжется с вами для уточнения деталей!"
        
        logger.info(f"AI selection generated for {destination}")
        return result
        
    except Exception as e:
        logger.error(f"Error generating AI selection: {e}")
        travelata_url = f"https://partners.travelata.ru/search?fromCity=2&toCountry={destination}&sid={TRAVELATA_PARTNER_ID}"
        return f"🔍 🌴 Подбираю для вас лучшие туры!\n\n👉 Посмотреть: {travelata_url}"False

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if 'message' not in data:
            return 'OK'
        
        message = data['message']
        chat_id = message['chat']['id']
        text = message.get('text', '')
        
        # Сохраняем chat_id всех пользователей
        all_users.add(chat_id)
        
        # Команды для админа (мамы)
        if chat_id == ADMIN_ID:
            # Команда /send {chat_id} {message} - отправить сообщение конкретному пользователю
            if text.startswith('/send '):
                parts = text.split(' ', 2)
                if len(parts) >= 3:
                    target_chat_id = parts[1]
                    msg_text = parts[2]
                    try:
                        send_message(int(target_chat_id), msg_text, parse_mode='HTML')
                        send_message(chat_id, f"✅ Сообщение отправлено пользователю {target_chat_id}")
                    except Exception as e:
                        send_message(chat_id, f"❌ Ошибка: {e}")
                else:
                    send_message(chat_id, "Использование: /send {chat_id} {сообщение}")
                return 'OK'
            
            # Команда /broadcast {message} - отправить всем пользователям
            elif text.startswith('/broadcast '):
                msg_text = text.replace('/broadcast ', '', 1)
                count = 0
                for user_id in all_users:
                    if user_id != ADMIN_ID:  # Не отправляем админу
                        try:
                            send_message(user_id, msg_text, parse_mode='HTML')
                            count += 1
                        except:
                            pass
                send_message(chat_id, f"✅ Подборка отправлена {count} пользователям")
                return 'OK'
            
            # Команда /users - показать всех пользователей
            elif text == '/users':
                users_list = "📋 Список пользователей бота:\n\n"
                for user_id in all_users:
                    if user_id != ADMIN_ID:
                        users_list += f"• ID: {user_id}\n"
                users_list += f"\nВсего: {len(all_users) - 1} пользователей"
                send_message(chat_id, users_list)
                return 'OK'
            
            # Команда /help - помощь для админа
            elif text == '/help':
                help_text = """🔧 Команды админа:

/send {chat_id} {текст} - отправить сообщение пользователю
/broadcast {текст} - разослать всем пользователям
/users - список всех пользователей
/help - эта справка

Пример отправки подборки:
/send 123456789 🌴 <b>Ваша подборка туров!</b>\n\nЕгипет, Хургада\n7 ночей - 60000₽\n\n👉 https://qui-quo.ru/abc123

HTML-теги работают: <b>жирный</b>, <i>курсив</i>"""
                send_message(chat_id, help_text, parse_mode='HTML')
                return 'OK'
        
        # Обычный диалог для клиентов
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
                
                # Уведомляем админа о новой заявке
                if ADMIN_ID:
                    admin_msg = f"🔔 Новая заявка!\n\nОт пользователя: {chat_id}\n📍 {data_info['destination']}\n📅 {data_info['dates']}\n👥 {data_info['people']} чел\n💰 {data_info['budget']}₽\n📱 {phone}\n\nОтветить: /send {chat_id} ваше сообщение"
                    send_message(ADMIN_ID, admin_msg)

                                # Генерируем и отправляем AI-подборку
                ai_selection = generate_ai_selection(
                    data_info['destination'],
                    data_info['dates'],
                    data_info['people'],
                    data_info['budget']
                )
                send_message(chat_id, ai_selection)
                
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
