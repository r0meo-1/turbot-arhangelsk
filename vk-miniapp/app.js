(async () => {
  const $ = (id) => document.getElementById(id);
  const form = $('trip-form');
  const destination = $('destination');
  const departure = $('departure');
  const REQUEST_TIMEOUT_MS = 15000;
  const launchParams = location.search.slice(1);
  const params = new URLSearchParams(launchParams);
  const hasSignedLaunch = (query) => ['sign', 'vk_app_id', 'vk_user_id', 'vk_ts']
    .every((key) => Boolean(query.get(key)));
  let inVK = hasSignedLaunch(params);
  const bridge = window.vkBridge;
  let effectiveLaunchParams = launchParams;
  let payload;
  let continuation;
  const operationKey = () => {
    const identity = new URLSearchParams(effectiveLaunchParams);
    return `vk-miniapp-continue:${identity.get('vk_app_id')}:${identity.get('vk_user_id')}`;
  };
  const restoreOperation = () => {
    if (continuation) return;
    try {
      const stored = JSON.parse(sessionStorage.getItem(operationKey()));
      if (stored && typeof stored.id === 'string' && typeof stored.hash === 'string') continuation = stored;
    } catch (_) {}
  };
  const forgetOperation = () => {
    try { sessionStorage.removeItem(operationKey()); } catch (_) {}
  };
  const cancelOperation = async () => {
    restoreOperation();
    if (!continuation) return;
    const response = await fetch('./continue/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ launchParams: effectiveLaunchParams, requestId: continuation.id }),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok !== true) throw new Error(result.error || 'Не удалось отменить переход. Повторите попытку.');
    continuation = undefined;
    forgetOperation();
  };
  const operationForPayload = async () => {
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(payload)));
    const hash = Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
    restoreOperation();
    if (continuation && continuation.hash !== hash) await cancelOperation();
    if (!continuation) continuation = { id: crypto.randomUUID(), hash, created: Date.now() };
    try { sessionStorage.setItem(operationKey(), JSON.stringify(continuation)); } catch (_) {}
    return continuation.id;
  };

  const applyVKConfig = (config = {}) => {
    if (!config || typeof config !== 'object') return;
    if (config.appearance === 'light' || config.appearance === 'dark') {
      document.documentElement.dataset.appearance = config.appearance;
    }
    for (const side of ['top', 'bottom', 'left', 'right']) {
      const value = config.insets?.[side];
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
        document.documentElement.style.setProperty(`--vk-inset-${side}`, `${Math.min(value, 200)}px`);
      }
    }
  };
  if (bridge?.subscribe) {
    try {
      bridge.subscribe(({ detail } = {}) => {
        if (detail?.type === 'VKWebAppUpdateConfig') applyVKConfig(detail.data);
      });
    } catch (_) {}
  }

  const setJourney = (reviewing) => {
    $('step-parameters').removeAttribute('aria-current');
    $('step-review').removeAttribute('aria-current');
    (reviewing ? $('step-review') : $('step-parameters')).setAttribute('aria-current', 'step');
  };
  const updateGlance = () => {
    const adults = Number($('adults').value) || 0;
    const children = Number($('children').value) || 0;
    const nights = Number($('nights').value) || 0;
    $('trip-glance').textContent = `${adults} взр.${children ? ` · детей: ${children}` : ''} · ночей: ${nights} · до ${money($('budget').value)} на всех`;
  };

  const DESTINATIONS = Object.freeze([
    'Таиланд', 'Пхукет, Таиланд', 'Паттайя, Таиланд',
    'Вьетнам', 'Нячанг, Вьетнам', 'Фукуок, Вьетнам',
    'Шри-Ланка', 'Бентота, Шри-Ланка', 'Хиккадува, Шри-Ланка',
    'Египет', 'Хургада, Египет', 'Шарм-эль-Шейх, Египет',
    'ОАЭ', 'Дубай, ОАЭ', 'Рас-эль-Хайма, ОАЭ',
    'Турция', 'Анталья, Турция', 'Аланья, Турция', 'Кемер, Турция',
    'Мальдивы', 'Мале, Мальдивы',
    'Индонезия', 'Бали, Индонезия',
    'Китай', 'Хайнань, Китай',
    'Куба', 'Варадеро, Куба',
    'Танзания', 'Занзибар, Танзания',
    'Индия', 'Гоа, Индия',
    'Греция', 'Крит, Греция', 'Родос, Греция',
    'Кипр', 'Пафос, Кипр', 'Ларнака, Кипр',
    'Тунис', 'Монастир, Тунис', 'Хаммамет, Тунис',
    'Доминикана', 'Пунта-Кана, Доминикана',
    'Сочи', 'Адлер', 'Кисловодск', 'Ессентуки', 'Пятигорск', 'Абхазия'
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


  const money = (n) => `${Number(n).toLocaleString('ru-RU')} ₽`;
  const localDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  const today = new Date();
  $('date').min = localDate(today);
  today.setDate(today.getDate() + 30);
  $('date').value = localDate(today);
  const initialDate = $('date').value;

  // Native date inputs follow the device locale; keep the visible value Russian.
  const dateInput = $('date');
  const dateDisplay = $('date-display');
  const syncDateDisplay = () => {
    dateDisplay.value = dateInput.value ? dateInput.value.split('-').reverse().join('.') : '';
    dateDisplay.setCustomValidity(dateInput.validity.rangeUnderflow ? 'Дата вылета не может быть в прошлом.' : '');
  };
  const syncDateValue = () => {
    const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(dateDisplay.value.trim());
    const iso = match ? `${match[3]}-${match[2]}-${match[1]}` : '';
    dateInput.value = iso;
    const valid = Boolean(iso) && dateInput.value === iso;
    dateDisplay.setCustomValidity(!valid ? 'Введите дату в формате ДД.ММ.ГГГГ.'
      : dateInput.validity.rangeUnderflow ? 'Дата вылета не может быть в прошлом.' : '');
  };
  syncDateDisplay();
  dateInput.addEventListener('input', syncDateDisplay);
  dateInput.addEventListener('change', syncDateDisplay);
  dateDisplay.addEventListener('input', syncDateValue);

  $('budget').addEventListener('input', () => {
    $('budget-output').textContent = money($('budget').value);
    $('budget-amount').value = $('budget').value;
  });
  $('budget-amount').addEventListener('input', () => {
    const input = $('budget-amount');
    if (input.validity.valid && input.value) {
      $('budget').value = input.value;
      $('budget-output').textContent = money(input.value);
    }
  });
  form.addEventListener('input', () => {
    $('local-status').textContent = '';
    updateGlance();
  });
  updateGlance();

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
      budgetMaxRub: Number($('budget-amount').value),
      budgetScope: 'total',
      consent: $('consent').checked,
      termsAccepted: $('terms-accepted').checked,
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
    setJourney(true);
    $('status').textContent = inVK ? '' : 'Предпросмотр. Для сохранения откройте приложение из ВКонтакте.';
    $('save').disabled = !inVK;
    $('review-title').focus();
  });

  $('edit').addEventListener('click', async () => {
    try { await cancelOperation(); } catch (error) { $('status').textContent = error.message; return; }
    $('review').hidden = true;
    form.hidden = false;
    setJourney(false);
    destination.focus();
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  let clearOrigin;
  const askToClear = (event) => {
    clearOrigin = event.currentTarget;
    $('clear-dialog').showModal();
  };
  $('clear-trip').addEventListener('click', askToClear);
  $('cancel-trip').addEventListener('click', askToClear);
  $('keep-trip').addEventListener('click', () => {
    $('clear-dialog').close();
    clearOrigin?.focus();
  });
  $('confirm-clear').addEventListener('click', async () => {
    $('confirm-clear').disabled = true;
    try { await cancelOperation(); } catch (error) {
      $('status').textContent = error.message;
      $('clear-dialog').close();
      $('confirm-clear').disabled = false;
      return;
    }
    $('confirm-clear').disabled = false;
    $('clear-dialog').close();
    form.reset();
    dateInput.value = initialDate;
    syncDateDisplay();
    $('children-ages').replaceChildren();
    $('children-ages-card').hidden = true;
    $('budget-output').textContent = money($('budget').value);
    $('summary').replaceChildren();
    $('status').textContent = '';
    $('error').textContent = '';
    payload = undefined;
    $('review').hidden = true;
    form.hidden = false;
    setJourney(false);
    destination.dispatchEvent(new Event('input', { bubbles: true }));
    $('local-status').textContent = 'Параметры очищены. Заявка не отправлена.';
    destination.focus();
  });

  $('save').addEventListener('click', async () => {
    if (!inVK || !payload || $('save').disabled) return;
    $('save').disabled = true;
    $('edit').disabled = true;
    $('cancel-trip').disabled = true;
    $('review').setAttribute('aria-busy', 'true');
    $('status').textContent = 'Сохраняем параметры и готовим проверку в чате…';
    let terminalConflict = false;

    try {
      if (navigator.onLine === false) {
        throw new Error('Нет подключения к интернету. Проверьте сеть и повторите.');
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      let response;
      try {
        response = await fetch('./continue', {
          method: 'POST',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ launchParams: effectiveLaunchParams, payload, requestId: await operationForPayload() })
        });
      } finally {
        clearTimeout(timer);
      }

      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.ok !== true) {
        if (response.status === 409) { terminalConflict = true; forgetOperation(); }
        throw new Error(result.authReason ? `${result.error} [${result.authReason}]` : (result.error || 'Не удалось сохранить параметры.'));
      }

      $('review').setAttribute('aria-busy', 'false');
      const groupId = Number(result.groupId);
      if (!Number.isSafeInteger(groupId) || groupId <= 0) throw new Error('Не удалось открыть сообщество.');

      if (result.reviewSent !== true) {
        $('status').textContent = result.error || (result.draftSaved
          ? 'Параметры сохранены, но доставка в чат не подтверждена. Повторите переход.'
          : 'Переход ещё выполняется. Повторите через несколько секунд.');
        $('save').textContent = 'Повторить переход';
        $('save').disabled = false;
        $('edit').disabled = false;
        $('cancel-trip').disabled = false;
        return;
      }
      forgetOperation();
      $('status').textContent = 'Параметры отправлены в чат. Заявка менеджеру не отправлена.';
      $('chat').href = `https://vk.ru/im?sel=-${groupId}`;
      $('chat').hidden = false;
      $('save').hidden = true;
      $('edit').hidden = true;
      $('cancel-trip').hidden = true;

      if (new URLSearchParams(effectiveLaunchParams).get('vk_platform') === 'desktop_app_messenger' && location.hash === '#bot') {
        $('chat').hidden = true;
        $('return-chat').hidden = false;
      }
    } catch (error) {
      $('review').setAttribute('aria-busy', 'false');
      $('status').textContent = error.name === 'AbortError'
        ? 'Ответ задержался. Повторите попытку: одинаковые параметры не создадут дубль.'
        : (error.message || 'Ошибка соединения. Повторите попытку.');
      $('save').disabled = terminalConflict;
      $('save').textContent = 'Повторить переход';
      $('edit').disabled = false;
      $('cancel-trip').disabled = false;
    }
  });

  $('return-chat').addEventListener('click', async () => {
    $('return-chat').disabled = true;
    let timer;
    try {
      if (!bridge) throw new Error('unavailable');
      await Promise.race([
        bridge.send('VKWebAppClose', { status: 'success' }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), 3000); })
      ]);
    } catch (_) {
      $('status').textContent = 'Проверка параметров уже в чате. Закройте это окно крестиком, чтобы вернуться в бот. Заявка менеджеру не отправлена.';
    } finally {
      clearTimeout(timer);
      $('return-chat').disabled = false;
    }
  });

  const probeManagerEntry = async () => {
    const managerEntry = $('manager-entry');
    const managerLink = $('manager-link');
    if (!managerEntry || !managerLink || !inVK || !effectiveLaunchParams) return;

    managerEntry.hidden = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch('/agent-extension/crm/summary', {
        method: 'GET',
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'omit',
        headers: {
          'Accept': 'application/json',
          'X-VK-Launch-Params': effectiveLaunchParams
        }
      });
      if (response.status !== 200) return;
      managerLink.href = '/manager/?' + effectiveLaunchParams;
      managerEntry.hidden = false;
    } catch (_) {
      managerEntry.hidden = true;
    } finally {
      clearTimeout(timer);
    }
  };

  installDestinationAutocomplete();
  installDepartureAutocomplete();
  form.dataset.ready = 'true';
  $('submit').disabled = false;

  // Install the form before awaiting the bridge: ordinary browsers may never reply.
  if (!inVK && bridge) {
    try {
      const bridgeParams = await bridge.send('VKWebAppGetLaunchParams');
      const qp = new URLSearchParams();
      Object.entries(bridgeParams || {}).forEach(([key, value]) => {
        if (value !== undefined && value !== null) qp.set(key, String(value));
      });
      effectiveLaunchParams = qp.toString();
      const effectiveParams = new URLSearchParams(effectiveLaunchParams);
      inVK = hasSignedLaunch(effectiveParams);
    } catch (_) {
      effectiveLaunchParams = launchParams;
    }
  }

  // A delayed bridge response may arrive after the customer opens review.
  if (inVK && payload && !$('review').hidden) {
    $('status').textContent = '';
    $('save').disabled = false;
  }

  if (inVK && bridge) {
    try {
      Promise.resolve(bridge.send('VKWebAppGetConfig')).then(applyVKConfig).catch(() => {});
    } catch (_) {}
    bridge.send('VKWebAppInit').catch(() => {
      $('welcome').textContent = 'Соберём параметры поездки. Если приложение работает некорректно, откройте его заново.';
    });
  }

  probeManagerEntry();
})();
