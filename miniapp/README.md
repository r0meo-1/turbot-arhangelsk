# TurBot Telegram Mini App

Статический MVP Telegram Mini App для сбора параметров поездки перед передачей их в основной Telegram-бот.

## Что уже есть

- адаптивный mobile-first интерфейс;
- Telegram theme CSS variables;
- `Telegram.WebApp.ready()` и `expand()`;
- приветствие по `initDataUnsafe.user.first_name`;
- быстрые направления;
- город вылета, дата, ночи, взрослые, дети, бюджет, прямой перелёт;
- отправка JSON через `Telegram.WebApp.sendData()`;
- безопасный browser-preview fallback без сетевой отправки.

Пример payload:

```json
{
  "type": "trip_request",
  "version": 1,
  "destination": "Таиланд",
  "departure": "Москва",
  "date": "2026-10-08",
  "nights": 10,
  "adults": 2,
  "children": 0,
  "budgetMaxRub": 270000,
  "directOnly": false,
  "source": "telegram_mini_app"
}
```

## Локальный просмотр

Из корня репозитория:

```bash
python -m http.server 8080
```

Открыть:

```text
http://localhost:8080/miniapp/
```

В обычном браузере форма работает как preview и печатает payload в DevTools. Реальная `sendData()` работает при запуске Mini App из Telegram.

## Подключение к Telegram

1. Разместить каталог `miniapp/` по публичному HTTPS URL.
2. В BotFather настроить Mini App / menu button на этот URL или добавить `web_app` кнопку в сообщении бота.
3. В обработчике Telegram updates принять `message.web_app_data.data`.
4. Распарсить JSON и прогнать данные через существующую валидацию TurBot перед созданием/обновлением черновика заявки.
5. Не доверять `initDataUnsafe` на сервере. Если Mini App начнёт обращаться к backend напрямую, валидировать подписанную строку `Telegram.WebApp.initData` на серверной стороне.

## Следующий интеграционный шаг

Необходимо связать payload `trip_request` с текущим FSM в `bot.py`. Это лучше делать отдельным изменением с тестом, чтобы Mini App не обходил существующую логику согласия, валидации и дедупликации лидов.
