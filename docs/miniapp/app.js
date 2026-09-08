(() => {
  const tg = window.Telegram?.WebApp;
  const API_URL = 'https://bot.r0meo1.ru/miniapp/submit';
  const form = document.getElementById('trip-form');
  const destination = document.getElementById('destination');
  const children = document.getElementById('children');
  const childrenAgesCard = document.getElementById('children-ages-card');
  const childrenAges = document.getElementById('children-ages');
  const budget = document.getElementById('budget');
  const budgetOutput = document.getElementById('budget-output');
  const errorBox = document.getElementById('error');
  const submit = document.getElementById('submit');
  const welcome = document.getElementById('welcome');

  const formatRub = (value) => `${Number(value).toLocaleString('ru-RU')} ₽`;
  const formatDateInput = (value) => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const setBudget = () => { budgetOutput.textContent = formatRub(budget.value); };

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
    if (firstName) welcome.textContent = `${firstName}, соберём параметры поездки за минуту.`;
    try {
      tg.setHeaderColor('secondary_bg_color');
      tg.setBackgroundColor('bg_color');
    } catch (_) {
      // Older clients may not expose colour setters.
    }
  };

  const markDestinationChip = (value) => {
    document.querySelectorAll('.chip').forEach((chip) => {
      chip.classList.toggle('active', chip.dataset.destination === value);
    });
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

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      destination.value = chip.dataset.destination;
      markDestinationChip(chip.dataset.destination);
      tg?.HapticFeedback?.selectionChanged();
    });
  });

  destination.addEventListener('input', () => markDestinationChip(destination.value.trim()));
  children.addEventListener('input', renderChildAges);
  budget.addEventListener('input', setBudget);

  const restoreSubmit = () => {
    submit.disabled = false;
    submit.textContent = 'Продолжить в TurBot';
  };

  const submitViaBackend = async (payload) => {
    const response = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: tg.initData, payload })
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok !== true) {
      throw new Error(result.error || 'Не удалось передать заявку в TurBot.');
    }
    tg.HapticFeedback?.notificationOccurred('success');
    tg.close();
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorBox.textContent = '';

    if (!form.reportValidity()) {
      errorBox.textContent = 'Проверьте обязательные поля и согласие.';
      tg?.HapticFeedback?.notificationOccurred('error');
      return;
    }

    const data = new FormData(form);
    const childCount = Number(data.get('children'));
    const childAgeValues = Array.from(childrenAges.querySelectorAll('input')).map((input) => Number(input.value));
    const payload = {
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
      directOnly: data.get('direct') === 'on',
      consent: data.get('consent') === 'on',
      source: 'telegram_mini_app'
    };

    if (!payload.destination || !payload.departure || !payload.date || childAgeValues.length !== childCount) {
      errorBox.textContent = 'Проверьте направление, город вылета, дату и возраст детей.';
      return;
    }

    submit.disabled = true;
    submit.textContent = 'Передаём в TurBot…';

    try {
      if (tg?.initData) {
        await submitViaBackend(payload);
        return;
      }

      // Telegram simple WebView / reply-keyboard fallback. sendData is intended
      // for Keyboard Button Mini Apps, not the persistent bot menu button.
      if (tg?.sendData) {
        tg.sendData(JSON.stringify(payload));
        return;
      }

      console.info('TurBot Mini App payload', payload);
      errorBox.textContent = 'Предпросмотр: откройте приложение из Telegram для отправки заявки.';
      restoreSubmit();
    } catch (error) {
      console.error('TurBot Mini App submit failed', error);
      errorBox.textContent = error instanceof Error ? error.message : 'Не удалось передать заявку.';
      tg?.HapticFeedback?.notificationOccurred('error');
      restoreSubmit();
    }
  });

  setBudget();
  setMinDate();
  renderChildAges();
  applyTelegramContext();
})();
