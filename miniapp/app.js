(() => {
  const tg = window.Telegram?.WebApp;
  const API_URL = 'https://bot.r0meo1.ru/miniapp/submit';
  const form = document.getElementById('trip-form');
  const destination = document.getElementById('destination');
  const departure = document.getElementById('departure');
  const children = document.getElementById('children');
  const childrenAgesCard = document.getElementById('children-ages-card');
  const childrenAges = document.getElementById('children-ages');
  const budget = document.getElementById('budget');
  const budgetOutput = document.getElementById('budget-output');
  const errorBox = document.getElementById('error');
  const submit = document.getElementById('submit');
  const welcome = document.getElementById('welcome');
  const review = document.getElementById('review');
  const reviewTitle = document.getElementById('review-title');
  const summary = document.getElementById('summary');
  const edit = document.getElementById('edit');
  const save = document.getElementById('save');
  const status = document.getElementById('status');
  let pendingPayload = null;
  let isSaving = false;
  const REQUEST_TIMEOUT_MS = 15000;

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
    'Доминикана', 'Пунта-Кана, Доминикана'
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

  const startParam =
    tg?.initDataUnsafe?.start_param ||
    new URLSearchParams(window.location.search).get('tgWebAppStartParam') ||
    'landing';

  const formatRub = (value) => `${Number(value).toLocaleString('ru-RU')} ₽`;
  const formatDateInput = (value) => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const fetchJsonWithTimeout = async (url, options, timeoutMessage) => {
    if (navigator.onLine === false) {
      throw new Error('Нет подключения к интернету. Проверьте сеть и повторите.');
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const result = await response.json().catch(() => ({}));
      return { response, result };
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error(timeoutMessage);
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  };

  const setBudget = () => {
    budgetOutput.textContent = formatRub(budget.value);
  };

  const setMinDate = () => {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const dateInput = document.getElementById('date');
    dateInput.min = formatDateInput(now);
    if (!dateInput.value) {
      const defaultDate = new Date(now);
      defaultDate.setDate(defaultDate.getDate() + 30);
      dateInput.value = formatDateInput(defaultDate);
    }
  };

  const applyTelegramContext = () => {
    if (!tg) return;
    tg.ready();
    tg.expand();
    const firstName = tg.initDataUnsafe?.user?.first_name;
    if (firstName) {
      welcome.textContent = `${firstName}, соберём параметры поездки за минуту.`;
    }
    try {
      tg.setHeaderColor('secondary_bg_color');
      tg.setBackgroundColor('bg_color');
    } catch (_) {
      // Older clients may not expose colour setters.
    }
  };

  const markDestinationChip = (value) => {
    document.querySelectorAll('.chip').forEach((chip) => {
      const active = chip.dataset.destination === value;
      chip.classList.toggle('active', active);
      chip.setAttribute('aria-pressed', String(active));
    });
  };

  const applyDeepLink = () => {
    const mapped = {
      thailand: 'Таиланд',
      vietnam: 'Вьетнам',
      sri_lanka: 'Шри-Ланка'
    }[startParam];
    if (!mapped) return;
    destination.value = mapped;
    markDestinationChip(mapped);
  };

  const renderChildAges = () => {
    const count = Math.max(0, Math.min(6, Number(children.value) || 0));
    const previous = Array.from(childrenAges.querySelectorAll('input')).map((input) => input.value);
    childrenAges.replaceChildren();
    childrenAgesCard.hidden = count === 0;

    for (let index = 0; index < count; index += 1) {
      const label = document.createElement('label');
      label.className = 'age-field';
      const title = document.createElement('span');
      title.textContent = `Ребёнок ${index + 1}`;
      const input = document.createElement('input');
      input.type = 'number';
      input.name = `childAge${index}`;
      input.min = '0';
      input.max = '17';
      input.inputMode = 'numeric';
      input.required = true;
      input.placeholder = 'лет';
      input.value = previous[index] ?? '';
      label.append(title, input);
      childrenAges.append(label);
    }
  };

  const buildPayload = () => {
    const data = new FormData(form);
    const childCount = Number(data.get('children'));
    const childAgeValues = Array.from(childrenAges.querySelectorAll('input')).map((input) => Number(input.value));
    return {
      type: 'trip_request',
      version: 2,
      destination: String(data.get('destination') || '').trim(),
      departure: String(data.get('departure') || '').trim(),
      date: String(data.get('date') || ''),
      nights: Number(data.get('nights')),
      adults: Number(data.get('adults')),
      children: childCount,
      childrenAges: childAgeValues,
      budgetMaxRub: Number(data.get('budget')),
      budgetScope: 'total',
      directOnly: data.get('direct') === 'on',
      consent: data.get('consent') === 'on',
      source: 'telegram_mini_app'
    };
  };

  const summaryRow = (label, value) => {
    const row = document.createElement('div');
    row.className = 'review-row';
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    detail.textContent = value;
    row.append(term, detail);
    return row;
  };

  const renderSummary = (payload) => {
    const childrenText = payload.children
      ? `${payload.children} (${payload.childrenAges.join(', ')} лет)`
      : 'нет';
    summary.replaceChildren(
      summaryRow('Направление', payload.destination),
      summaryRow('Вылет', payload.departure),
      summaryRow('Дата', payload.date),
      summaryRow('Ночей', String(payload.nights)),
      summaryRow('Взрослых', String(payload.adults)),
      summaryRow('Детей', childrenText),
      summaryRow('Бюджет на всех', formatRub(payload.budgetMaxRub)),
      summaryRow('Перелёт', payload.directOnly ? 'только прямой' : 'любой подходящий')
    );
  };

  const showReview = (payload) => {
    pendingPayload = payload;
    isSaving = false;
    renderSummary(payload);
    form.hidden = true;
    review.hidden = false;
    review.setAttribute('aria-busy', 'false');
    status.textContent = '';
    save.disabled = false;
    edit.disabled = false;
    save.textContent = 'Сохранить и продолжить';
    tg?.BackButton?.show?.();
    reviewTitle.focus({ preventScroll: true });
    review.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const showForm = () => {
    if (isSaving) return;
    review.hidden = true;
    form.hidden = false;
    status.textContent = '';
    tg?.BackButton?.hide?.();
    destination.focus({ preventScroll: true });
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.setAttribute('aria-pressed', 'false');
    chip.addEventListener('click', () => {
      destination.value = chip.dataset.destination;
      markDestinationChip(chip.dataset.destination);
      tg?.HapticFeedback?.selectionChanged();
    });
  });

  destination.addEventListener('input', () => markDestinationChip(destination.value.trim()));
  children.addEventListener('input', renderChildAges);
  budget.addEventListener('input', setBudget);
  edit.addEventListener('click', showForm);
  tg?.BackButton?.onClick?.(showForm);

  const submitViaBackend = async (payload) => {
    const { response, result } = await fetchJsonWithTimeout(
      API_URL,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ initData: tg.initData, payload })
      },
      'TurBot отвечает слишком долго. Проверьте связь и повторите.'
    );
    if (!response.ok || result.ok !== true) {
      throw new Error(result.error || 'Не удалось передать заявку в TurBot.');
    }
  };

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    errorBox.textContent = '';

    if (!form.reportValidity()) {
      errorBox.textContent = 'Проверьте обязательные поля и согласие.';
      tg?.HapticFeedback?.notificationOccurred('error');
      return;
    }

    const payload = buildPayload();
    if (!payload.destination || !payload.departure || !payload.date || payload.childrenAges.length !== payload.children) {
      errorBox.textContent = 'Проверьте направление, город вылета, дату и возраст детей.';
      return;
    }

    showReview(payload);
    tg?.HapticFeedback?.selectionChanged();
  });

  save.addEventListener('click', async () => {
    if (!pendingPayload || save.disabled) return;
    isSaving = true;
    save.disabled = true;
    edit.disabled = true;
    review.setAttribute('aria-busy', 'true');
    tg?.BackButton?.hide?.();
    save.textContent = 'Сохраняем…';
    status.textContent = 'Передаём параметры в TurBot…';

    try {
      if (tg?.initData) {
        await submitViaBackend(pendingPayload);
        status.textContent = 'Параметры сохранены. Возвращаемся в TurBot…';
        tg.HapticFeedback?.notificationOccurred('success');
        tg.BackButton?.hide?.();
        tg.close();
        return;
      }

      // Fallback for reply-keyboard Mini Apps. The persistent menu button uses
      // the signed backend path above.
      if (tg?.sendData) {
        tg.sendData(JSON.stringify(pendingPayload));
        return;
      }

      console.info('TurBot Mini App payload', pendingPayload);
      status.textContent = 'Предпросмотр: откройте приложение из Telegram для сохранения заявки.';
      isSaving = false;
      review.setAttribute('aria-busy', 'false');
      save.disabled = false;
      edit.disabled = false;
      save.textContent = 'Сохранить и продолжить';
    } catch (error) {
      console.error('TurBot Mini App submit failed', error);
      status.textContent = error instanceof Error ? error.message : 'Не удалось передать заявку.';
      tg?.HapticFeedback?.notificationOccurred('error');
      isSaving = false;
      review.setAttribute('aria-busy', 'false');
      save.disabled = false;
      edit.disabled = false;
      tg?.BackButton?.show?.();
      save.textContent = 'Повторить сохранение';
    }
  });

  installDestinationAutocomplete();
  installDepartureAutocomplete();
  setBudget();
  setMinDate();
  renderChildAges();
  applyTelegramContext();
  applyDeepLink();
})();
