(() => {
  const tg = window.Telegram?.WebApp;
  const form = document.getElementById('trip-form');
  const destination = document.getElementById('destination');
  const budget = document.getElementById('budget');
  const budgetOutput = document.getElementById('budget-output');
  const errorBox = document.getElementById('error');
  const submit = document.getElementById('submit');
  const welcome = document.getElementById('welcome');

  const formatRub = (value) => `${Number(value).toLocaleString('ru-RU')} ₽`;

  const setBudget = () => {
    budgetOutput.textContent = formatRub(budget.value);
  };

  const setMinDate = () => {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const iso = now.toISOString().slice(0, 10);
    const dateInput = document.getElementById('date');
    dateInput.min = iso;
    if (!dateInput.value) {
      const defaultDate = new Date(now);
      defaultDate.setDate(defaultDate.getDate() + 30);
      dateInput.value = defaultDate.toISOString().slice(0, 10);
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
      // Older Telegram clients may not expose these methods.
    }
  };

  const markDestinationChip = (value) => {
    document.querySelectorAll('.chip').forEach((chip) => {
      chip.classList.toggle('active', chip.dataset.destination === value);
    });
  };

  document.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      destination.value = chip.dataset.destination;
      markDestinationChip(chip.dataset.destination);
      tg?.HapticFeedback?.selectionChanged();
    });
  });

  destination.addEventListener('input', () => markDestinationChip(destination.value.trim()));
  budget.addEventListener('input', setBudget);

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    errorBox.textContent = '';

    if (!form.reportValidity()) {
      errorBox.textContent = 'Проверьте обязательные поля.';
      tg?.HapticFeedback?.notificationOccurred('error');
      return;
    }

    const data = new FormData(form);
    const payload = {
      type: 'trip_request',
      version: 1,
      destination: String(data.get('destination') || '').trim(),
      departure: String(data.get('departure') || '').trim(),
      date: String(data.get('date') || ''),
      nights: Number(data.get('nights')),
      adults: Number(data.get('adults')),
      children: Number(data.get('children')),
      budgetMaxRub: Number(data.get('budget')),
      directOnly: data.get('direct') === 'on',
      source: 'telegram_mini_app'
    };

    if (!payload.destination || !payload.departure || !payload.date) {
      errorBox.textContent = 'Заполните направление, город вылета и дату.';
      return;
    }

    submit.disabled = true;
    submit.textContent = 'Отправляем…';

    if (tg?.sendData) {
      tg.HapticFeedback?.notificationOccurred('success');
      tg.sendData(JSON.stringify(payload));
      return;
    }

    // Browser fallback for local/static preview. No network request is made.
    console.info('TurBot Mini App payload', payload);
    errorBox.textContent = 'Предпросмотр: откройте Mini App из Telegram для отправки заявки.';
    submit.disabled = false;
    submit.textContent = 'Найти туры';
  });

  setBudget();
  setMinDate();
  applyTelegramContext();
})();
