"""Dialog states and shared UI labels used by both bots."""

STATE_CONSENT = "consent"
STATE_DESTINATION = "destination"
STATE_ORIGIN = "origin"  # departure city — needed for a real transport search
STATE_DATES = "dates"
STATE_PEOPLE = "people"          # adults, 12+ (airlines price them as adults)
STATE_KIDS = "kids"              # how many children under 12
STATE_KIDS_AGES = "kids_ages"    # their exact ages — the fare depends on them
# Retired in favour of the ages step, which derives infants precisely instead of
# asking a second question the first one could contradict. Kept so a session
# saved mid-dialog before that change still routes somewhere on the next reply.
STATE_INFANTS = "infants"
STATE_BUDGET = "budget"
STATE_CONTACT = "contact"  # choose how to be reached
STATE_PHONE = "phone"
STATE_VK = "vk"
STATE_MAX = "max_contact"   # номер телефона или ссылка на профиль MAX

PEOPLE_OPTIONS = ["1", "2", "3", "4", "5+"]

# Airlines price three age bands completely differently, and the funnel used to
# collapse them into one number — so a family of four with two kids was quoted
# four adult fares. The count is picked with a button; the ages are typed, and
# it is the ages that decide which band each child falls into.
KIDS_NONE_LABEL = "Без детей"
KIDS_OPTIONS = [KIDS_NONE_LABEL, "1", "2", "3+"]

BACK_BUTTON_TEXT = "◀️ Назад"
CANCEL_BUTTON_TEXT = "❌ Отменить"
CONSENT_YES_TEXT = "✅ Согласен"
CONSENT_NO_TEXT = "❌ Отказаться"
START_BUTTON_TEXT = "🚀 Начать подбор"

CONTACT_TG_TEXT = "✈️ Telegram"
CONTACT_PHONE_TEXT = "📱 Телефон"
CONTACT_VK_TEXT = "💙 VK"
# Offered instead of Telegram to clients who came in through VK. MAX has no
# @username for personal profiles: people are found by phone number or by a
# personal https://max.ru/u/<hash> link, so the step asks for those and not
# for a handle that does not exist.
CONTACT_MAX_TEXT = "🟣 MAX"
MAX_PROFILE_HINT = "аватар → иконка QR → «Поделиться»"

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
