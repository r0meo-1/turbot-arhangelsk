# Travelata API activation for TurBot

TurBot uses Travelata as the first live package-tour provider and Tourvisor as a fallback:

```text
TOUR_PROVIDER_ORDER=travelata,tourvisor
```

Production must never substitute demo hotels when a live provider is unavailable.

## 1. Get access

Travelata API access is granted individually to Travelpayouts partners.

Current process (2026):

1. Register a free Travelpayouts account.
2. Create a Project for the TurBot/Aprel Tour integration.
3. Confirm that Travelata is available for the Project under My Programs.
4. Contact Travelpayouts support and request Travelata API access.

Suggested request text:

```text
Здравствуйте.

Просим предоставить доступ к API Travelata для проекта TurBot / «Апрель Тур».

Цель использования: пользователь самостоятельно запускает поиск пакетных туров в VK Mini App или Telegram-боте. Backend получает актуальные предложения Travelata по параметрам заявки (город вылета, направление, даты, длительность, состав туристов и бюджет), а найденные варианты показываются пользователю в чате. Автоматического массового сбора или скрейпинга выдачи нет.

Площадки:
- https://r0meo1.ru
- VK Mini App «Апрель Тур», app_id 54475121
- Telegram bot @apreltour_bot

API-запросы выполняются только с backend-сервера. Учётные данные не передаются клиенту и не хранятся в публичном репозитории.
```

After approval Travelata provides an API username and password for HTTP Basic Auth.

## 2. Configure production

Store credentials only in `/opt/turbot/.env` or an equivalent protected secret store:

```dotenv
TRAVELATA_USERNAME=<issued username>
TRAVELATA_PASSWORD=<issued password>
VK_TRAVELATA_ENABLED=true
TOUR_PROVIDER_ORDER=travelata,tourvisor
```

Do not put real credentials in Git, screenshots, chat messages, logs, query strings, or client-side Mini App code.

The default API host is:

```text
https://api-gateway.travelata.ru
```

TurBot calls endpoints under `/partners`, using HTTP Basic Auth.

## 3. Verify

`deploy/verify-vk-miniapp.sh` prints only safe readiness metadata:

```text
Travelata config: enabled=yes credentials=set endpoint_host=api-gateway.travelata.ru
Travelata API: http=200 success=yes
```

It never prints the username, password, Authorization header, response body, or request query.

After Travelata becomes healthy, restart/redeploy `turbot-vk`. The VK review card should show `🔎 Показать отели и цены` because `TOUR_SEARCH_ENABLED` is true when at least one live provider is configured.

## 4. Failure behaviour

- Missing Travelata credentials: provider is skipped.
- Travelata 401/403: provider reports an access problem and the router tries the next configured live provider.
- Travelata 429: provider reports rate limiting and the router can fall through.
- Empty Travelata result: the next provider may be tried.
- No live provider: VK does not advertise automatic price search; the user can still send the request to a manager.
- No production path may fall back to the curated demo hotel catalogue.
