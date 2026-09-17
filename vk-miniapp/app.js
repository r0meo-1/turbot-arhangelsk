(async () => {
  const $ = (id) => document.getElementById(id);
  const form = $('trip-form');
  const destination = $('destination');
  const departure = $('departure');
  const REVIEW_COMMAND = 'Проверить заявку';
  const REVIEW_PAYLOAD = { command: 'miniapp_review', version: 1 };
  const launchParams = location.search.slice(1);
  const params = new URLSearchParams(launchParams);
  let inVK = params.has('sign') && params.has('vk_app_id');
  const bridge = window.vkBridge;
  let effectiveLaunchParams = launchParams;
  let payload;

  const DESTINATIONS = Object.freeze([
    'Таиланд', 'Вьетнам', 'Шри-Ланка', 'Египет', 'ОАЭ', 'Турция',
    'Мальдивы', 'Индонезия', 'Китай', 'Куба', 'Танзания', 'Индия',
    'Греция', 'Кипр', 'Тунис', 'Доминикана'
  ]);

  const installDestinationAutocomplete = () => {
    if (!destination || document.getElementById('destination-options')) return;
    const suggestions = document.createElement('datalist');
    suggestions.id = 'destination-options';
    DESTINATIONS.forEach((place) => {
      const option = document.createElement('option');
      option.value = place;
      suggestions.append(option);
    });
    destination.setAttribute('list', suggestions.id);
    destination.setAttribute('autocomplete', 'off');
    destination.insertAdjacentElement('afterend', suggestions);
  };

  const DEPARTURE_CITIES = Object.freeze([
    'Архангельск', 'Москва', 'Санкт-Петербург', 'Мурманск', 'Казань',
    'Екатеринбург', 'Новосибирск', 'Самара', 'Уфа', 'Челябинск',
    'Нижний Новгород', 'Пермь', 'Омск', 'Тюмень', 'Сургут', 'Сочи',
    'Минеральные Воды', 'Калининград', 'Красноярск', 'Иркутск',
    'Владивосток', 'Хабаровск', 'Ростов-на-Дону'
  ]);

  const installDepartureAutocomplete = () => {
    if (!departure || document.getElementById('departure-cities')) return;
    const suggestions = document.createElement('datalist');
    suggestions.id = 'departure-cities';
    DEPARTURE_CITIES.forEach((city) => {
      const option = document.createElement('option');
      option.value = city;
      suggestions.append(option);
    });
    departure.setAttribute('list', suggestions.id);
    departure.setAttribute('autocomplete', 'off');
    departure.insertAdjacentElement('afterend', suggestions);
  };

  if (!inVK && bridge) {
    try {
      const bridgeParams = await bridge.send('VKWebAppGetLaunchParams');
      const qp = new URLSearchParams();
      Object.entries(bridgeParams || {}).forEach(([key, value]) => {
        if (value !== undefined && value !== null) qp.set(key, String(value));
      });
      effectiveLaunchParams = qp.toString();
      const effectiveParams = new URLSearchParams(effectiveLaunchParams);
      inVK = effectiveParams.has('sign') && effectiveParams.has('vk_app_id');
    } catch (_) {
      effectiveLaunchParams = launchParams;
    }
  }

  const money = (n) => `${Number(n).toLocaleString('ru-RU')} ₽`;
  const localDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const today = new Date();
  $('date').min = localDate(today);
  today.setDate(today.getDate() + 30);
  $('date').value = localDate(today);

  $('budget').addEventListener('input', () => {
    $('budget-output').textContent = money($('budget').value);
  });

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      destination.value = chip.dataset.destination;
      destination.dispatchEvent(new Event('input'));
    });
  });

  destination.addEventListener('input', () => {
    document.querySelectorAll('.chip').forEach((chip) => {
      const active = chip.dataset.destination === destination.value.trim();
      chip.classList.toggle('active', active);
      chip.setAttribute('aria-pressed', String(active));
    });
  });

  $('children').addEventListener('input', () => {
    const old = Array.from($('children-ages').querySelectorAll('input'), (i) => i.value);
    const count = Math.min(6, Math.max(0, Math.trunc(Number($('children').value)) || 0));
    $('children-ages').replaceChildren();
    $('children-ages-card').hidden = count === 0;
    for (let i = 0; i < count; i += 1) {
      const label = document.createElement('label');
      label.className = 'age-field';
      label.textContent = `Ребёнок ${i + 1}, лет`;
      const input = document.createElement('input');
      Object.assign(input, {
        type: 'number', min: '0', max: '17', step: '1', required: true, value: old[i] || ''
      });
      label.append(input);
      $('children-ages').append(label);
    }
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;

    payload = {
      type: 'trip_request',
      version: 2,
      destination: destination.value.trim(),
      departure: departure.value.trim(),
      date: $('date').value,
      nights: Number($('nights').value),
      adults: Number($('adults').value),
      children: Number($('children').value),
      childrenAges: Array.from($('children-ages').querySelectorAll('input'), (i) => Number(i.value)),
      budgetMaxRub: Number($('budget').value),
      budgetScope: 'total',
      consent: $('consent').checked,
      directOnly: $('direct').checked,
      source: 'vk_mini_app'
    };

    if (!payload.destination || !payload.departure) {
      $('error').textContent = 'Укажите направление и город вылета.';
      return;
    }

    $('error').textContent = '';
    const entries = [
      ['Направление', payload.destination],
      ['Вылет', payload.departure],
      ['Дата', new Date(`${payload.date}T12:00:00`).toLocaleDateString('ru-RU')],
      ['Ночей', payload.nights],
      ['Взрослых', payload.adults],
      ['Дети', payload.children ? payload.childrenAges.map((age) => `${age} лет`).join(', ') : 'Без детей'],
      ['Бюджет на всех', `до ${money(payload.budgetMaxRub)}`],
      ['Перелёт', payload.directOnly ? 'только прямой' : 'любой подходящий']
    ];

    $('summary').replaceChildren();
    entries.forEach(([label, value]) => {
      const dt = document.createElement('dt');
      const dd = document.createElement('dd');
      dt.textContent = label;
      dd.textContent = value;
      $('summary').append(dt, dd);
    });

    form.hidden = true;
    $('review').hidden = false;
    $('status').textContent = inVK ? '' : 'Предпросмотр. Для сохранения откройте приложение из ВКонтакте.';
    $('save').disabled = !inVK;
    $('review-title').focus();
  });

  $('edit').addEventListener('click', () => {
    $('review').hidden = true;
    form.hidden = false;
    destination.focus();
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  $('save').addEventListener('click', async () => {
    if (!inVK || !payload || $('save').disabled) return;
    $('save').disabled = true;
    $('edit').disabled = true;
    $('status').textContent = 'Сохраняем параметры…';

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let response;
      try {
        response = await fetch('./draft', {
          method: 'POST',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ launchParams: effectiveLaunchParams, payload })
        });
      } finally {
        clearTimeout(timer);
      }

      const result = await response.json();
      if (!response.ok || result.ok !== true) {
        throw new Error(result.authReason ? `${result.error} [${result.authReason}]` : (result.error || 'Не удалось сохранить параметры.'));
      }

      const groupId = Number(result.groupId);
      if (!Number.isSafeInteger(groupId) || groupId <= 0) throw new Error('Не удалось открыть сообщество.');

      $('status').textContent = `Параметры сохранены. Откройте чат и напишите «${REVIEW_COMMAND}»: бот покажет ваш подбор. Заявка менеджеру ещё не отправлена.`;
      $('chat').href = `https://vk.ru/im?sel=-${groupId}`;
      $('chat').hidden = false;
      $('save').hidden = true;
      $('edit').hidden = true;

      const copyReviewCommand = () => {
        if (!bridge) return;
        try {
          Promise.resolve(bridge.send('VKWebAppCopyText', { text: REVIEW_COMMAND }))
            .then(() => {
              if (!$('chat').hidden) {
                $('status').textContent = `Параметры сохранены. Команда «${REVIEW_COMMAND}» скопирована. Откройте чат и вставьте её: бот покажет ваш подбор. Заявка менеджеру ещё не отправлена.`;
              }
            })
            .catch(() => {});
        } catch (_) {}
      };

      if (bridge) {
        try {
          Promise.resolve(bridge.send('VKWebAppSendPayload', {
            group_id: groupId,
            payload: REVIEW_PAYLOAD
          }))
            .then(() => {
              if (!$('chat').hidden) {
                $('status').textContent = 'Параметры сохранены. Бот уже подготовил проверку. Откройте чат: вводить команду не нужно. Заявка менеджеру ещё не отправлена.';
              }
            })
            .catch(copyReviewCommand);
        } catch (_) {
          copyReviewCommand();
        }
      } else {
        copyReviewCommand();
      }
    } catch (error) {
      $('status').textContent = error.name === 'AbortError'
        ? 'Ответ задержался. Повторите попытку: одинаковые параметры не создадут дубль.'
        : (error.message || 'Ошибка соединения. Повторите попытку.');
      $('save').disabled = false;
      $('edit').disabled = false;
    }
  });

  installDestinationAutocomplete();
  installDepartureAutocomplete();

  if (inVK && bridge) {
    bridge.send('VKWebAppInit').catch(() => {
      $('welcome').textContent = 'Соберём параметры поездки. Если приложение работает некорректно, откройте его заново.';
    });
  }
})();
