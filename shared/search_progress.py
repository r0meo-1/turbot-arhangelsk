"""Customer-facing preview of validated offers, before a search finishes."""
from shared import tourvisor


def format_search_progress(result, *, origin=''):
    text = ('🔎 Поиск продолжается.\n'
            + (f'Вылет из: {origin}.\n' if origin else '')
            + f'Успешно проверено частей диапазона: {result.completed_windows} из {result.total_windows}.')
    if result.offers:
        text += '\n\nПервые найденные варианты — итоговый список ещё может измениться:\n\n'
        text += tourvisor.format_client_message(
            tourvisor.SearchResult(offers=result.offers[:3]), max_len=1800)
        text += '\n\nВыбрать вариант можно после завершения поиска.'
    else:
        text += '\nПродолжаю проверять остальные даты.'
    return text + '\nМожно отменить подбор. Заявка менеджеру не отправлена.'
