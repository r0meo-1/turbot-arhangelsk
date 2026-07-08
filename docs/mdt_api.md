# МоиДокументы-Туризм — API v1.7

> Структурированная версия документации для нейросетей и разработчиков.  
> Источник: [`https://www.moidokumenti.ru/downloads/API.pdf`](https://www.moidokumenti.ru/downloads/API.pdf)

## Общие сведения

| Параметр | Значение |
|----------|----------|
| Базовый URL (веб-тариф) | `https://[YOUR_ACCOUNT].moidokumenti.ru/api/[METHOD]` |
| Базовый URL (локальный тариф) | `http://[SERVER_ADDRESS]/api/[METHOD]` |
| HTTP-метод | `POST` |
| Формат запроса | `multipart/form-data` или `application/x-www-form-urlencoded` с двумя полями: `params` (JSON-строка) и `key` (API-ключ) |
| Формат ответа | JSON |

### Параметры каждого запроса

| Поле | Тип | Описание |
|------|-----|----------|
| `params` | string (JSON) | JSON-кодированный массив параметров конкретного метода |
| `key` | string | Ключ доступа к API |

### Пример запроса

```php
$api_key = 'GBM05uZpdLVs95KlPFVS1NksvP4qz794f04xpRpG8I5VZ8Qzo94V7PhJXB9o6tUH';
$url = 'https://***.moidokumenti.ru/api/send-push';

$params = array(
    'manager_ids' => array(1),
    'title' => 'Срочно выполнить задание!',
    'text' => 'Позвонить Петрову Ивану Ивановичу',
    'url' => 'http://www.moidokumenti.ru'
);

$request = array(
    'params' => json_encode($params),
    'key' => $api_key
);

$ch = curl_init($url);
curl_setopt($ch, CURLOPT_URL, $url);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, 1);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
curl_setopt($ch, CURLOPT_TIMEOUT, 600);
curl_setopt($ch, CURLOPT_POST, 1);
curl_setopt($ch, CURLOPT_POSTFIELDS, $request);

$result = curl_exec($ch);
curl_close($ch);
var_dump($result);
```

---

## 1. Лиды

### 1.1. Добавление лида

- **URL:** `/api/add-lead`
- **Описание:** Создаёт новый лид (заявку) в CRM.

#### Параметры

| Название | Тип | Описание | Обязательный |
|----------|-----|----------|--------------|
| `name` | string | ФИО клиента | да |
| `phone` | string | Телефон | да |
| `email` | string | Email | нет |
| `source` | string | Название источника лида | нет |
| `fields` | array of arrays | Дополнительная информация о заказе | нет |

Формат `fields`:

```php
'fields' => array(
    array(
        'name' => 'Желаемая страна отдыха',
        'values' => array('Турция', 'Тунис')
    ),
    array(
        'name' => 'Бюджет',
        'values' => array(150000)
    )
)
```

#### Пример запроса

```php
$params = array(
    'name' => 'Иванов Сергей',
    'phone' => '+79012223333',
    'email' => 'ivanov.sr@mail.ru',
    'source' => 'Facebook Leads',
    'fields' => array(
        array(
            'name' => 'Желаемая страна отдыха',
            'values' => array('Турция', 'Тунис')
        ),
        array(
            'name' => 'Бюджет',
            'values' => array(150000)
        )
    )
);
```

---

## 2. Туристы

### 2.1. Добавление туриста

- **URL:** `/api/add-tourist`

#### Параметры

| Название | Тип | Описание |
|----------|-----|----------|
| `name` | string | ФИО туриста |
| `name_lat` | string | ФИО туриста на латинице |
| `gender` | string | Пол туриста: `f` — женский, `m` — мужской |
| `address` | string | Адрес |
| `tel` | string | Телефон, можно несколько |
| `email` | string | Email |
| `passport_series` | string | Серия загранпаспорта |
| `passport_number` | string | Номер загранпаспорта |
| `passport_who` | string | Кем выдан загранпаспорт |
| `passport_when` | date | Когда выдан загранпаспорт (`YYYY-MM-DD`) |
| `passport_till` | date | Срок действия загранпаспорта (`YYYY-MM-DD`) |
| `passport_series_rus` | string | Серия внутреннего паспорта |
| `passport_number_rus` | string | Номер внутреннего паспорта |
| `passport_who_rus` | string | Кем выдан внутренний паспорт |
| `passport_when_rus` | date | Когда выдан внутренний паспорт (`YYYY-MM-DD`) |
| `dr` | date | Дата рождения туриста (`YYYY-MM-DD`) |
| `receive_sms` | boolean | Получает ли турист SMS |
| `receive_email` | boolean | Получает ли турист Email |
| `manager_id` | integer | ID менеджера |
| `office_id` | integer | ID офиса |
| `groups` | array | Список ID групп, в которые входит турист |
| `contacts` | array | Контактная информация (см. примечание) |
| `vk` | string | JSON-строка связанных аккаунтов ВКонтакте |

#### Примечание по `contacts`

Массив вида:

```php
'contacts' => array(
    ID_ТИПА_КОНТАКТА => array('ЗНАЧЕНИЕ_1', 'ЗНАЧЕНИЕ_2')
)
```

Типы контактов:

| ID | Тип |
|----|-----|
| 1 | VK |
| 2 | Facebook |
| 3 | Одноклассники |
| 4 | ICQ |
| 5 | Skype |

Если присутствует контакт типа VK, обязателен дополнительный параметр `vk`.

#### Пример запроса

```php
$params = array(
    'name' => 'Сидоренко Иван Николаевич',
    'name_lat' => 'SIDORENKO IVAN',
    'gender' => 'm',
    'address' => 'Екатеринбург, ул. Малышева 51',
    'tel' => '+79024567891',
    'email' => 'sidorenko.in@mail.ru',
    'passport_series' => '65',
    'passport_number' => '123456',
    'passport_who' => 'FMS 67',
    'passport_when' => '2011-05-11',
    'passport_till' => '2021-05-11',
    'passport_series_rus' => '64 02',
    'passport_number_rus' => '022233',
    'passport_who_rus' => 'ОВД 66',
    'passport_when_rus' => '2006-10-15',
    'dr' => '1988-12-10',
    'receive_sms' => 1,
    'receive_email' => 1,
    'manager_id' => 1,
    'office_id' => 1,
    'groups' => array(),
    'contacts' => array(
        1 => array('335697421'),
        2 => array('help.moidokumenti.ru')
    ),
    'vk' => json_encode(array(
        array(
            'id' => 335697421,
            'name' => 'Антонина Тимофеева',
            'photo' => 'https://pp.vk.me/c630917/v630917421/2285/tqUSSBWpWqM.jpg'
        )
    ))
);
```

### 2.2. Редактирование туриста

- **URL:** `/api/edit-tourist`
- **Дополнительный параметр:** `id` (integer, ID туриста)
- Остальные параметры совпадают с `/api/add-tourist`.

### 2.3. Список туристов

- **URL:** `/api/get-tourist-list`

#### Параметры

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество возвращаемых результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля |
| `search` | string | Поиск по имени/номеру телефона |
| `id` | integer | Поиск по ID туриста |
| `manager_id` | integer | Фильтр по ID менеджера |
| `office_id` | integer | Фильтр по ID офиса |

Возможные значения `fields`:

```
id, name, name_lat, address, tel, dr, passport_series, passport_number,
passport_who, passport_when, passport_till, gender, email,
passport_series_rus, passport_number_rus, passport_who_rus, passport_when_rus,
receive_sms, receive_email, manager_id, office_id, manager_name, office_name, comments
```

#### Пример запроса

```php
$params = array(
    'offset' => 0,
    'count' => 10,
    'fields' => array(
        'id', 'name', 'name_lat', 'address', 'tel', 'dr',
        'passport_series', 'passport_number', 'passport_who', 'passport_when',
        'passport_till', 'gender', 'email', 'passport_series_rus',
        'passport_number_rus', 'passport_who_rus', 'passport_when_rus',
        'receive_sms', 'receive_email', 'manager_id', 'office_id',
        'manager_name', 'office_name', 'comments'
    )
);
```

### 2.4. Список туристов по имени

- **URL:** `/api/get-tourist-list-by-name`
- **Назначение:** Для списков и выпадающих меню.

#### Параметры

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `search` | string | Поиск по имени (обязательный) |
| `manager_id` | integer | Фильтр по менеджеру |
| `office_id` | integer | Фильтр по офису |

### 2.5. Удаление туриста

- **URL:** `/api/delete-tourist`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID туриста |

---

## 3. Потенциальные туристы

### 3.1. Добавление потенциального туриста

- **URL:** `/api/add-tourist-temp`

| Название | Тип | Описание |
|----------|-----|----------|
| `name` | string | ФИО туриста |
| `tags` | string | Теги |
| `tel` | string | Телефон |
| `email` | string | Email |
| `manager_id` | integer | ID менеджера |
| `office_id` | integer | ID офиса |
| `groups` | array | Список ID групп |
| `contacts` | array | Контактная информация |
| `vk` | string | JSON-строка аккаунтов ВК |

### 3.2. Редактирование потенциального туриста

- **URL:** `/api/edit-tourist-temp`
- **Дополнительный параметр:** `id` (integer)

### 3.3. Список потенциальных туристов

- **URL:** `/api/get-tourist-temp-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля: `id`, `name`, `tel`, `email`, `tags`, `manager_id`, `office_id`, `manager_name`, `office_name` |
| `search` | string | Поиск по имени/телефону |
| `id` | integer | Поиск по ID |
| `manager_id` | integer | Фильтр по менеджеру |
| `office_id` | integer | Фильтр по офису |

### 3.4. Удаление потенциального туриста

- **URL:** `/api/delete-tourist-temp`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID потенциального туриста |

---

## 4. Организации (юридические лица)

### 4.1. Добавление организации

- **URL:** `/api/add-tourist-org`

| Название | Тип | Описание |
|----------|-----|----------|
| `name` | string | Название организации |
| `director` | string | ФИО руководителя |
| `director_position` | string | Должность руководителя |
| `contract_header` | string | Шапка договора "В лице...". Пример: "Сидоренко Ивана Николаевича, действующего на основании св-ва гос. регистрации" |
| `address` | string | Адрес |
| `address_ur` | string | Юридический адрес |
| `tel` | string | Телефон |
| `email` | string | Email |
| `contact_person` | string | ФИО контактного лица |
| `contact_person_position` | string | Должность контактного лица |
| `contact_person_tel` | string | Телефон контактного лица |
| `inn` | string | ИНН |
| `kpp` | string | КПП |
| `okpo` | string | ОКПО |
| `ogrn` | string | ОГРН |
| `bank_rs` | string | Расчётный счёт |
| `bank_ks` | string | Корреспондентский счёт |
| `bank_name` | string | Банк |
| `bank_bik` | string | БИК |
| `manager_id` | integer | ID менеджера |
| `office_id` | integer | ID офиса |
| `groups` | array | Список ID групп |

### 4.2. Редактирование организации

- **URL:** `/api/edit-tourist-org`
- **Дополнительный параметр:** `id` (integer)

### 4.3. Список организаций

- **URL:** `/api/get-tourist-org-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля |
| `search` | string | Поиск |
| `id` | integer | Поиск по ID |
| `manager_id` | integer | Фильтр по менеджеру |
| `office_id` | integer | Фильтр по офису |

Возможные `fields`:

```
id, name, tel, email, director, director_position, address, address_ur,
bank_rs, bank_name, bank_ks, bank_bik, inn, kpp, okpo, ogrn,
contact_person, contact_person_position, contact_person_tel,
comments, manager_name, office_name, manager_id, office_id
```

### 4.4. Удаление организации

- **URL:** `/api/delete-tourist-org`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID организации |

---

## 5. Группы туристов

### 5.1. Получение списка групп

- **URL:** `/api/get-group-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `tourist_type` | string | Тип туриста: `tourist`, `tourist_org`, `tourist_temp` |

### 5.2. Получение участников группы

- **URL:** `/api/get-group-tourist-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID группы |

### 5.3. Добавление группы

- **URL:** `/api/add-group`

| Название | Тип | Описание |
|----------|-----|----------|
| `name` | string | Название группы |
| `tourist_type` | string | Тип туриста: `tourist`, `tourist_org`, `tourist_temp` |

### 5.4. Редактирование группы

- **URL:** `/api/edit-group`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID группы |
| `name` | string | Название группы |

### 5.5. Удаление группы

- **URL:** `/api/delete-group`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID группы |

### 5.6. Добавление туриста в группу

- **URL:** `/api/add-tourist-to-group`

| Название | Тип | Описание |
|----------|-----|----------|
| `group_id` | integer | ID группы |
| `tourist_id` | integer | ID туриста |
| `tourist_type` | string | Тип туриста: `tourist`, `tourist_org`, `tourist_temp` |

### 5.7. Удаление туриста из группы

- **URL:** `/api/delete-tourist-from-group`

| Название | Тип | Описание |
|----------|-----|----------|
| `group_id` | integer | ID группы |
| `tourist_id` | integer | ID туриста |
| `tourist_type` | string | Тип туриста |

### 5.8. Объединение групп в новую группу

- **URL:** `/api/merge-groups`

| Название | Тип | Описание |
|----------|-----|----------|
| `group_id` | integer | ID группы 1 |
| `group_id_2` | integer | ID группы 2 (в оригинале параметр также называется `group_id`; уточните в документации) |
| `group_name` | string | Название новой группы |

---

## 6. Обращения (preorders)

### 6.1. Добавление обращения

- **URL:** `/api/create-preorder`

| Название | Тип | Описание |
|----------|-----|----------|
| `tourist_type` | string | Тип туриста: `tourist` или `tourist_temp` |
| `tourist_id` | integer | ID туриста |
| `country_id1` | integer | ID страны 1 |
| `country_id2` | integer | ID страны 2 |
| `country_id3` | integer | ID страны 3 |
| `flightdate_from` | date | Дата вылета с |
| `flightdate_to` | date | Дата вылета по |
| `persons` | integer | Количество взрослых |
| `children` | integer | Количество детей |
| `children_ages` | array | Возраста детей |
| `nights_from` | integer | Количество ночей от |
| `nights_to` | integer | Количество ночей до |
| `hotel_categories` | array | Категории отеля (1–5) |
| `meal_types` | array | Типы питания: `AO`, `BB`, `HB`, `FB`, `AI` |
| `price_from` | float | Стоимость тура от |
| `price_to` | float | Стоимость тура до |
| `comment` | string | Комментарий |
| `delayed_till` | date | Заявка отложена до |
| `stage_id` | integer | ID этапа заявки |
| `tour_id` | integer | ID связанного тура |
| `preorder_manager_id` | integer | ID менеджера |
| `advert_id` | integer | ID рекламного источника |
| `link` | string | Ссылка на подборку туров |
| `wait_for_hot` | boolean | Ждёт горящий тур |
| `departure_id1` | string | ID города вылета 1 |
| `departure_id2` | string | ID города вылета 2 |
| `departure_id3` | string | ID города вылета 3 |
| `reminder_date` | date | Дата напоминания |
| `reminder_time` | time | Время напоминания (`HH:MM:SS`) |
| `reminder_comment` | string | Текст напоминания |

#### Пример запроса

```php
$params = array(
    'tourist_type' => 'tourist_temp',
    'tourist_id' => 1323,
    'country_id1' => 96,
    'country_id2' => 67,
    'country_id3' => 0,
    'flightdate_from' => '2016-08-15',
    'flightdate_to' => '2016-08-25',
    'delayed_till' => '2016-08-02',
    'persons' => 2,
    'children' => 1,
    'children_ages' => array(4),
    'nights_from' => 7,
    'nights_to' => 10,
    'hotel_categories' => array(3, 4, 5),
    'meal_types' => array('AO', 'BB', 'HB', 'FB', 'AI'),
    'price_from' => 0,
    'price_to' => 120000,
    'comment' => 'Первая линия',
    'stage_id' => 2,
    'tour_id' => 0,
    'preorder_manager_id' => 0,
    'advert_id' => 4,
    'link' => 'https://vk.com/',
    'wait_for_hot' => 1,
    'departure_id1' => 'SVX',
    'reminder_date' => '2016-08-03',
    'reminder_time' => '10:10:00',
    'reminder_comment' => 'Позвонить по заявке'
);
```

### 6.2. Редактирование обращения

- **URL:** `/api/edit-preorder`
- **Дополнительный параметр:** `id` (integer, ID обращения)
- Остальные параметры совпадают с `/api/create-preorder`.

### 6.3. Список обращений

- **URL:** `/api/get-preorder-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля |
| `preorder_id` | integer | ID заявки |
| `manager_id` | integer | ID менеджера |
| `office_id` | integer | ID офиса |
| `tourist_id` | integer | ID туриста |
| `tourist_type` | string | Тип туриста |
| `stages` | array | Массив ID статусов заявки |
| `status` | string | Статус: `open`, `reject`, `archive` |
| `countries` | array | Массив ID стран |
| `flightdate_from` | date | Дата вылета от |
| `flightdate_to` | date | Дата вылета до |
| `persons_from` | integer | Количество взрослых от |
| `persons_to` | integer | Количество взрослых до |
| `children_from` | integer | Количество детей от |
| `children_to` | integer | Количество детей до |
| `nights_from` | integer | Количество ночей от |
| `nights_to` | integer | Количество ночей до |
| `hotel_categories` | array | Категории отелей (1–5) |
| `meal_types` | array | Типы питания (`AO`, `BB`, `HB`, `FB`, `AI`) |
| `price_from` | integer | Стоимость тура от |
| `price_to` | integer | Стоимость тура до |
| `preorder_create_date_from` | date | Дата создания заявки от |
| `preorder_create_date_to` | date | Дата создания заявки до |
| `advert_id` | integer | ID рекламного источника. `-1` — не задан, `2` — Прочее |
| `order_desc` | boolean | Сортировать по убыванию |

Возможные `fields`:

```
preorder_id, preorder_create_date, tourist_id, tourist_type, tourist_name,
persons, children, children_ages, flight_date_from, flight_date_to,
nights_from, nights_to, price_from, price_to, comment, manager_id, office_id,
manager_name, office_name, stage_id, is_auto, is_external, tour_id, link,
advert_id, advert_other_comment, countries, hotel_categories, meal_types,
reject_reason_id
```

---

## 7. Туры

### 7.1. Внесение оплаты по туру

- **URL:** `/api/add-payment`

| Название | Тип | Описание |
|----------|-----|----------|
| `order_id` | integer | ID оплаты, связанной с туром (либо `order_id`, либо `tour_id`) |
| `tour_id` | integer | ID тура (либо `order_id`, либо `tour_id`) |
| `amount` | float | Сумма платежа |
| `type` | string | Тип платежа: `in` (от туриста), `out` (туроператору) |
| `pay_method` | string | Способ оплаты: `cash`, `card`, `bank` |
| `date` | date | Дата платежа |
| `comission` | float | Комиссия за платеж |
| `exchange_rate` | float | Курс, по которому принята оплата |
| `comment` | text | Комментарий |

#### Пример запроса

```php
$params = array(
    'tour_id' => 180,
    'amount' => 10000,
    'type' => 'in',
    'pay_method' => 'bank',
    'date' => date('Y-m-d'),
    'comission' => 200,
    'exchange_rate' => 67.54,
    'comment' => 'П/п 1167'
);
```

### 7.2. Оформление пакета документов

Состоит из двух этапов:

1. Получение параметров, необходимых к заполнению.
2. Формирование пакета документов на основе заполненных данных.

#### Этап 1. Получение параметров

- **URL:** `/api/create-tour-prepare`

| Название | Тип | Описание |
|----------|-----|----------|
| `documents` | array | Список ID документов, которые необходимо создать |
| `buyer_id` | integer | ID туриста, на которого оформляется договор |
| `buyer_type` | string | Тип покупателя: `tourist`, `tourist_org` |
| `buyer_is_tourist` | boolean | Является ли покупатель участником тура (только для `buyer_type=tourist`) |
| `tourist_list` | array | Список ID туристов-участников тура |
| `passport_type` | string | Тип паспортных данных: `international` (загран), `local` (внутренний) |
| `touroperator_inn` | string | ИНН туроператора |
| `manager_id` | integer | ID менеджера |
| `office_id` | integer | ID офиса |

#### Этап 2. Формирование пакета документов

- **URL:** `/api/create-tour`

Параметры те же, что и на этапе 1, плюс:

| Название | Тип | Описание |
|----------|-----|----------|
| `preorder_id` | integer | ID связанного обращения |
| `contract_number` | string | Номер договора (если пусто — автоматический номер) |
| `country_id` | integer | ID страны отдыха |
| `currency_id` | integer | ID валюты тура |
| `tour_data` | array | Массив заполненных данных по туру (см. примечание) |

` tour_data` формируется на основе данных, полученных на этапе 1.

#### Пример массива `tour_data`

```php
$tour_data = array();
$tour_data['МДТур'] = 'Таиланд, о. Пхукет';
$tour_data['МДДата1'] = '2016-05-20';
$tour_data['МДДата2'] = '2016-05-27';
$tour_data['МДСтоимость'] = 125300;
$tour_data['СтоимостьПриходник'] = 12500;
```

#### Пример ответа

```php
array(
    "result" => "success",
    "count" => 6,
    "data" => array(
        "docs" => array(1361, 1362, 1363, 1364, 1365, 1366),
        "tour_id" => "241"
    )
)
```

В ответе возвращаются ID оформленного тура и ID созданных документов.

### 7.3. Список документов по туру

- **URL:** `/api/get-tour-document-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID тура |

#### Пример ответа

```php
array(
    "result" => "success",
    "count" => 5,
    "data" => array(
        "docs" => array(
            array(
                "id" => 1355,
                "name" => "Договор",
                "file" => "https://.../Договор 12-31-11.zip"
            ),
            array(
                "id" => 1356,
                "name" => "Заявка",
                "file" => "https://.../Заявка 12-31-15.zip"
            ),
            array(
                "id" => 1357,
                "name" => "Приходник",
                "file" => "https://.../Приходник 12-31-22.zip"
            ),
            array(
                "id" => 1358,
                "name" => "Путевка",
                "file" => "https://.../Путевка 12-31-28.zip"
            ),
            array(
                "id" => 1359,
                "name" => "Фингарантии",
                "file" => "https://.../Фингарантии 12-31-36.zip"
            )
        )
    )
)
```

### 7.4. Список туров

- **URL:** `/api/get-tour-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля |
| `tour_id` | integer | ID тура |
| `order_id` | integer | ID связанной оплаты |
| `manager_id` | integer | ID менеджера |
| `office_id` | integer | ID офиса |
| `tourist_id` | integer | ID туриста |
| `tourist_org_id` | integer | ID организации |
| `contract_number` | string | Номер договора |
| `contract_date_from` | date | Дата договора с |
| `contract_date_to` | date | Дата договора по |
| `tour_start_date_from` | date | Дата начала тура с |
| `tour_start_date_to` | date | Дата начала тура по |
| `tour_end_date_from` | date | Дата окончания тура с |
| `tour_end_date_to` | date | Дата окончания тура по |
| `order_status` | string | Статус оплаты: `in`, `out`, `all`, `none` |
| `order_desc` | boolean | Сортировать по убыванию |

Возможные `fields`:

```
tour_id, date, contract_number, buyer_id, buyer_name, buyer_type,
buyer_not_tourist, tourist_list, order_id, touroperator_inn,
tour_currency, docs, manager_id, office_id, manager_name, office_name,
cost_for_tourist, cost_for_tourist_ue, payed_tourist,
cost_for_touroperator, cost_for_touroperator_ue, payed_touroperator,
comission_tourist, comission_touroperator, discount, pay_status,
touroperator_invoice_number, touroperator_paydate, tourist_paydate,
visa_status, visa_docdate, visa_readydate, tour_start_date, tour_end_date,
tour_name, tour_country, tour_hotel, is_canceled, advert_id,
advert_other_comment, preorder_id
```

### 7.5. Список платежей по турам

- **URL:** `/api/get-payment-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `tour_id` | integer | ID тура |
| `order_id` | integer | ID связанной оплаты |
| `manager_id` | integer | ID менеджера |
| `tourist_id` | integer | ID туриста |
| `tourist_type` | string | Тип туриста: `tourist`, `tourist_org` |
| `payment_type` | string | Тип платежа: `bank`, `card`, `cash` |
| `payment_direction` | string | Направление платежа: `in`, `out` |
| `payment_create_date_from` | date | Реальная дата добавления платежа с |
| `payment_create_date_to` | date | Реальная дата добавления платежа по |
| `payment_date_from` | date | Заданная дата платежа с |
| `payment_date_to` | date | Заданная дата платежа по |
| `order_desc` | boolean | Сортировать по убыванию |

### 7.6. Ссылка на оплату через Аппекс

- **URL:** `/api/get-payment-link`

| Название | Тип | Описание |
|----------|-----|----------|
| `order_id` | integer | ID оплаты (либо `order_id`, либо `tour_id`) |
| `tour_id` | integer | ID тура (либо `order_id`, либо `tour_id`) |

---

## 8. Дисконтные карты

### 8.1. Добавление дисконтной карты

- **URL:** `/api/add-card`

| Название | Тип | Описание |
|----------|-----|----------|
| `card_number` | string | Номер карты |
| `card_discount` | integer | Размер скидки по карте |
| `tourist_id` | integer | ID туриста |
| `card_comment` | string | Комментарий |
| `card_expires` | date | Срок действия (необязательно) |

### 8.2. Редактирование дисконтной карты

- **URL:** `/api/edit-card`
- **Дополнительный параметр:** `id` (integer, ID карты)

### 8.3. Удаление дисконтной карты

- **URL:** `/api/delete-card`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID карты |

### 8.4. Начисление бонусов

- **URL:** `/api/add-card-bonus`

| Название | Тип | Описание |
|----------|-----|----------|
| `card_id` | integer | ID дисконтной карты |
| `amount` | integer | Сумма накопления |
| `comment` | string | Комментарий |
| `expires` | date | Дата сгорания бонуса |

### 8.5. Списание бонусов

- **URL:** `/api/debit-card-bonus`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID бонуса |
| `amount` | integer | Сумма списания |
| `comment` | string | Комментарий |

### 8.6. Удаление бонусов

- **URL:** `/api/delete-card-bonus`

| Название | Тип | Описание |
|----------|-----|----------|
| `id` | integer | ID бонуса |

### 8.7. Получение списка бонусов

- **URL:** `/api/get-card-bonuses`

| Название | Тип | Описание |
|----------|-----|----------|
| `card_id` | integer | ID дисконтной карты |

### 8.8. Получение информации по карте

- **URL:** `/api/get-card-info`

| Название | Тип | Описание |
|----------|-----|----------|
| `card_id` | integer | ID дисконтной карты |
| `card_number` | string | Номер дисконтной карты |

---

## 9. Прочее

### 9.1. Добавление общения с туристом

- **URL:** `/api/add-tourist-action`

| Название | Тип | Описание |
|----------|-----|----------|
| `tourist_type` | string | Тип туриста: `tourist`, `tourist_temp` |
| `tourist_id` | integer | ID туриста |
| `action_type` | string | Тип события: `callin`, `callout`, `mailin`, `mailout`, `comment`, `meeting`, `vk`, `fb`, `ok`, `icq`, `skype` |
| `comment` | string | Комментарий |

---

## 10. Справочники

### 10.1. Города вылета

- **URL:** `/api/get-departure-list`

### 10.2. Загруженные документы

- **URL:** `/api/get-document-list`

### 10.3. Менеджеры

- **URL:** `/api/get-manager-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля |
| `id` | integer | Фильтр по ID менеджера |
| `office_id` | integer | Фильтр по ID офиса |

Возможные `fields`:

```
id, name, dogovor_header, code, tel, email, boss_id, office_id,
office_name, dr, iptel_extnumber, last_access, dismissed
```

### 10.4. Офисы

- **URL:** `/api/get-office-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `fields` | array | Возвращаемые поля |
| `id` | integer | Фильтр по ID офиса |

Возможные `fields`:

```
id, name, city, address, tel, code, iptel_extnumbers
```

### 10.5. Рекламные источники

- **URL:** `/api/get-advert-list`

### 10.6. Страны

- **URL:** `/api/get-country-list`

### 10.7. Туроператоры

- **URL:** `/api/get-touroperator-list`

| Название | Тип | Описание |
|----------|-----|----------|
| `count` | integer | Количество результатов (максимум 100) |
| `offset` | integer | Смещение |
| `ext_info` | boolean | Вывод расширенной информации |
| `search` | string | Поиск по названию/реестровому номеру |
| `inn` | string | Поиск по ИНН |

### 10.8. Этапы обращений

- **URL:** `/api/get-preorder-stage-list`

### 10.9. Причины отказов обращений

- **URL:** `/api/get-preorder-reject-reasons-list`

---

## 11. Уведомления

### 11.1. Email-сообщение

- **URL:** `/api/send-email`

| Название | Тип | Описание |
|----------|-----|----------|
| `for_name` | string | Имя получателя |
| `for_email` | string | Email получателя |
| `from_name` | string | Имя отправителя |
| `from_email` | string | Email отправителя |
| `title` | string | Заголовок сообщения |
| `text` | string | Текст сообщения |

### 11.2. PUSH-уведомления менеджерам

- **URL:** `/api/send-push`

| Название | Тип | Описание |
|----------|-----|----------|
| `manager_ids` | array | Список ID менеджеров |
| `title` | string | Заголовок уведомления |
| `text` | string | Текст уведомления |
| `url` | string | Ссылка для перехода |

### 11.3. SMS по ID туриста

- **URL:** `/api/send-sms-by-tourist-id`

| Название | Тип | Описание |
|----------|-----|----------|
| `tourist_id` | integer | ID туриста |
| `sms_from` | string | Имя отправителя, зарегистрированное в МДТ |
| `text` | string | Текст сообщения |

### 11.4. SMS по номеру телефона

- **URL:** `/api/send-sms`

| Название | Тип | Описание |
|----------|-----|----------|
| `tel` | string | Телефон получателя |
| `sms_from` | string | Имя отправителя, зарегистрированное в МДТ |
| `text` | string | Текст сообщения |

### 11.5. Добавление задачи / напоминания

- **URL:** `/api/add-reminder`

| Название | Тип | Описание |
|----------|-----|----------|
| `date` | date | Дата |
| `time` | time | Время (`HH:MM:SS`) |
| `text` | string | Текст напоминания |
| `for_all_managers` | boolean | Напоминание для всех менеджеров |
| `for_all_sub_managers` | boolean | Напоминание для всех подчинённых менеджеров |
| `only_one_manager` | boolean | Достаточно ли выполнить задачу только одному менеджеру |
| `tourist_type` | string | Тип туриста: `tourist`, `tourist_temp`, `tourist_org` |
| `tourist_id` | integer | ID туриста |
| `manager_owner_id` | integer | ID менеджера, выставившего задачу |
| `manager_id` | integer | ID ответственного менеджера |
| `office_id` | integer | ID ответственного офиса |
| `preorder_id` | integer | ID связанного обращения |
| `tour_id` | integer | ID связанного тура |

> Должен быть задан только один из параметров: `for_all_managers`, `for_all_sub_managers`, `manager_id`, `office_id`.

---

## 12. IP-телефония

> Адреса для IP-телефонии отличаются от основного API. В отличие от основного API, параметры передаются самостоятельными GET/POST параметрами (не внутри `params`).

### Возможности

- Инициация исходящего звонка из CRM в стороннюю систему IP-телефонии.
- Получение уведомлений о входящих звонках и отображение карточки звонящего.
- Сохранение ссылки на запись разговора.

### Настройка

1. В карточках менеджеров указать внутренние номера IP-телефонии.
2. Задать ключ-секрет (рекомендуемая длина 32–64 символа).
3. Задать адрес страницы сторонней системы IP-телефонии для уведомлений из CRM.

### 12.1. Начало звонка

- **URL:** `/ipcall/api-call-start`

| Название | Тип | Описание |
|----------|-----|----------|
| `direction` | string | Направление звонка: `in` (входящий), `out` (исходящий) |
| `uniqueId` | string | Уникальный номер звонка. Если не передан, CRM сформирует сама и вернёт в ответе. |
| `callerId` | string | Номер звонящего |
| `calleeId` | string | Номер вызываемого |
| `hash` | string | Проверочная строка |

#### Генерация `hash`

```
hash = md5([uniqueId]:[direction]:[callerId]:[calleeId]:[Ключ-секрет])
```

#### Пример

```
uniqueId = 3245342322.34544
direction = in
callerId = +79001122333
calleeId = 102
Ключ-секрет = GthDSG763hs

hash = md5("3245342322.34544:in:+79001122333:102:GthDSG763hs")
      = 5c50ebblfc337f5037d5155986de8df3
```

### 12.2. Окончание звонка

- **URL:** `/ipcall/api-call-end`

| Название | Тип | Описание |
|----------|-----|----------|
| `uniqueId` | string | Уникальный номер звонка |
| `recordUrl` | string | Ссылка на запись разговора |
| `hash` | string | Проверочная строка |

#### Генерация `hash`

```
hash = md5([uniqueId]:[recordUrl]:[Ключ-секрет])
```

#### Пример

```
uniqueId = 3245342322.34544
recordUrl = https://example.com/records/3245342322.34544.mp3
Ключ-секрет = GthDSG763hs

hash = md5("3245342322.34544:https://example.com/records/3245342322.34544.mp3:GthDSG763hs")
      = 995e1635ca7960d69058bc7c7a086015
```

### 12.3. Уведомление внешней системы о звонке из CRM

При начале звонка из CRM на адрес сторонней системы отправляется POST-запрос.

| Название | Тип | Описание |
|----------|-----|----------|
| `callerId` | string | Номер звонящего (внутренний номер сотрудника) |
| `calleeId` | string | Номер вызываемого (например, `+79001122333`) |
| `hash` | string | Проверочная строка |

#### Генерация `hash`

```
hash = md5([callerId]:[calleeId]:[Ключ-секрет])
```

Сторонняя система должна произвести вызов звонящего, затем, после поднятия трубки, вызвать вызываемого абонента и связать их.
