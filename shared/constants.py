"""Dialog states and shared UI labels used by both bots."""

STATE_CONSENT = "consent"
STATE_DESTINATION = "destination"
STATE_ORIGIN = "origin"  # departure city — needed for a real transport search
STATE_DATES = "dates"
STATE_PEOPLE = "people"
STATE_BUDGET = "budget"
STATE_CONTACT = "contact"  # choose how to be reached
STATE_PHONE = "phone"
STATE_VK = "vk"

PEOPLE_OPTIONS = ["1", "2", "3", "4", "5+"]

BACK_BUTTON_TEXT = "◀️ Назад"
CANCEL_BUTTON_TEXT = "❌ Отменить"
CONSENT_YES_TEXT = "✅ Согласен"
CONSENT_NO_TEXT = "❌ Отказаться"
START_BUTTON_TEXT = "🚀 Начать подбор"

CONTACT_TG_TEXT = "✈️ Telegram"
CONTACT_PHONE_TEXT = "📱 Телефон"
CONTACT_VK_TEXT = "💙 VK"

# Destination names without emoji (VK keyboard labels; TG uses emoji variants).
POPULAR_DESTINATIONS_PLAIN = [
    "Египет",
    "Турция",
    "Таиланд",
    "Мальдивы",
    "ОАЭ",
    "Другое",
]

POPULAR_DESTINATIONS_TG = [
    "🏖 Египет",
    "🏝 Турция",
    "🌴 Таиланд",
    "🌊 Мальдивы",
    "🏛 ОАЭ",
    "✏️ Другое",
]

# Departure cities. The agency is in Arkhangelsk, so it leads — but clients
# routinely fly out of Moscow or St Petersburg, and guessing wrong makes the
# quoted price meaningless.
ORIGIN_OPTIONS_PLAIN = [
    "Архангельск",
    "Москва",
    "Санкт-Петербург",
    "Другой город",
]

ORIGIN_OPTIONS_TG = [
    "🏠 Архангельск",
    "🏙 Москва",
    "🌉 Санкт-Петербург",
    "✏️ Другой город",
]
