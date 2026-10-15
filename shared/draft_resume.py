"""One-use choices bound to the current draft, never to a user's next trip."""
import secrets

from shared.tour_favorites import scope


CONTINUE = "Продолжить подбор"
RESTART = "Начать заново"
PROMPT = ("У вас есть незавершённый подбор. Продолжить с сохранёнными параметрами "
          "и избранным или начать заново? При новом подборе они будут очищены. "
          "Заявка менеджеру не отправлена.")


def fingerprint(info):
    return (info.get('state'), scope(info), info.get('review_token'),
            info.get('phone'), info.get('telegram_handle'), info.get('vk'))


def issue(info):
    token = secrets.token_hex(8)
    info['_resume_choice'] = (token, fingerprint(info))
    return token


def consume(info, token):
    choice = info.get('_resume_choice')
    if not choice or choice != (token, fingerprint(info)):
        return False
    info.pop('_resume_choice', None)
    return True
