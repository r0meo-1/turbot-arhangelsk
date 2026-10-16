"""Safe client explanations for unsupported search parameters."""


def common_error_code(results):
    results = list(results)
    errors = [result for result in results if result.error]
    if errors and all(result.error_code == 'search_window' for result in errors):
        return 'search_window'
    for code in ('departure_unsupported', 'destination_unsupported'):
        if results and all(result.error and result.error_code == code for result in results):
            return code
    return ''


def unsupported_departure_text(origin):
    city = ' '.join(str(origin or 'выбранного города').split())[:80]
    return (f'🛫 Автоматический поиск с вылетом из «{city}» не поддерживается подключёнными источниками.\n\n'
            'Это не означает, что туров из этого города нет: наличие не проверено. '
            'Выберите другой город вылета, например Москву, или продолжите обсуждение с менеджером отдельным действием.\n'
            'Город вылета сам не менялся. Заявка менеджеру не отправлена.')
