(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const tg = window.Telegram?.WebApp;
  const localEndpoint = window.location.pathname.startsWith('/miniapp/');
  const endpoint = localEndpoint ? new URL('/miniapp/alean/search', window.location.origin).href
    : 'https://bot.r0meo1.ru/miniapp/alean/search';
  let query = null;
  let controller = null;
  let generation = 0;
  const iso = (date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  const today = new Date(); today.setHours(0,0,0,0);
  const arrival = new Date(today); arrival.setDate(arrival.getDate()+30);
  const departure = new Date(arrival); departure.setDate(departure.getDate()+7);
  $('arrival').min = $('departure').min = iso(today);
  $('arrival').value = iso(arrival); $('departure').value = iso(departure);
  const invalidate = () => { generation += 1; controller?.abort(); controller = null; };
  const syncProduct = () => { $('origin-card').hidden = document.querySelector('[name="kind"]:checked').value === 'hotel'; };
  document.querySelectorAll('[name="kind"]').forEach(el => el.addEventListener('change', syncProduct));
  const show = (stage) => {
    $('alean-form').hidden = stage !== 'form';
    $('alean-review').hidden = stage !== 'review';
    $('alean-results').hidden = stage !== 'results';
    $('back').hidden = stage === 'form';
    if (stage === 'form') tg?.BackButton?.hide?.(); else tg?.BackButton?.show?.();
  };
  const back = () => { invalidate(); show('form'); $('destination').focus(); };
  $('back').addEventListener('click', back);
  tg?.BackButton?.onClick?.(back);
  $('cancel').addEventListener('click', () => {
    invalidate(); query = null; show('form');
    $('offer-list').replaceChildren(); $('validation').textContent = '';
    $('cancel-status').textContent = 'Поиск отменён. Ничего не отправлено. Параметры можно изменить и поискать снова.';
  });
  $('mode').addEventListener('change', () => {
    back(); $('scenario-card').hidden = $('mode').value !== 'demo';
    $('demo-banner').textContent = $('mode').value === 'demo'
      ? 'ДЕМО: вымышленные отели, цены и наличие. Это не предложения Алеан.'
      : 'Живой поиск: доступ требует настройки сервера и запуска из Telegram. Цена и наличие не подтверждены.';
  });
  $('alean-form').addEventListener('submit', (event) => {
    event.preventDefault(); $('validation').textContent = ''; $('cancel-status').textContent = '';
    if (!$('alean-form').reportValidity()) { $('validation').textContent = 'Проверьте обязательные поля.'; return; }
    const ageTokens = $('ages').value.trim() ? $('ages').value.trim().split(/[,\s]+/) : [];
    const ages = ageTokens.map(Number);
    const nights = (new Date($('departure').value) - new Date($('arrival').value))/86400000;
    if (!(nights >= 1 && nights <= 28)) { $('validation').textContent = 'Выезд должен быть через 1–28 ночей после заезда.'; return; }
    if (ages.length > 6 || ageTokens.some(token => !/^\d+$/.test(token)) || ages.some(age => !Number.isInteger(age) || age < 0 || age > 17)) {
      $('validation').textContent = 'Возраст каждого ребёнка: целое число 0–17, не более шести детей.'; return;
    }
    query = {kind: document.querySelector('[name="kind"]:checked').value,
      destination: $('destination').value, arrival: $('arrival').value, departure: $('departure').value,
      adults: Number($('adults').value), children_ages: ages, budget: Number($('budget').value), origin: $('origin').value.trim()};
    $('alean-summary').replaceChildren();
    for (const [label, value] of [['Поиск', query.kind === 'hotel' ? 'Отель' : 'Тур с перелётом'],
      ['Направление',query.destination], ['Заезд',query.arrival], ['Выезд',query.departure],
      ['Взрослых',query.adults], ['Возраст детей',ages.join(', ') || 'нет'], ['Бюджет на всех',`${query.budget.toLocaleString('ru-RU')} ₽`]]) {
      const row = document.createElement('div'); row.className = 'review-row';
      const dt = document.createElement('dt'); dt.textContent = label;
      const dd = document.createElement('dd'); dd.textContent = String(value);
      row.append(dt,dd); $('alean-summary').append(row);
    }
    if (query.kind === 'tour') {
      const row = document.createElement('div'); row.className = 'review-row';
      const dt = document.createElement('dt'); dt.textContent = 'Город вылета';
      const dd = document.createElement('dd'); dd.textContent = query.origin;
      row.append(dt,dd); $('alean-summary').append(row);
    }
    show('review'); $('alean-review').querySelector('h2').focus();
  });
  const search = async () => {
    if (!query) return;
    invalidate(); const current = generation;
    controller = new AbortController(); const requestController = controller;
    show('results'); $('offer-list').replaceChildren(); $('retry').hidden = true;
    $('alean-results').setAttribute('aria-busy','true');
    $('search-status').textContent = 'Ищем варианты в Алеан…'; $('result-title').textContent = 'Поиск';
    $('result-notice').textContent = 'Бронирование, оплата и отправка заявки отключены.';
    const timer = setTimeout(() => requestController.abort(), 45000);
    try {
      if ($('mode').value === 'live' && !tg?.initData) throw new Error('Для живого поиска откройте приложение из Telegram. Пока доступно демо.');
      const response = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'},
        signal:requestController.signal, body:JSON.stringify({query, mode:$('mode').value, scenario:$('scenario').value,
          ...($('mode').value === 'live' ? {initData:tg.initData} : {})})});
      const result = await response.json();
      if (current !== generation) return;
      if (!response.ok || !result.ok) throw new Error(result.error || 'Алеан временно недоступен.');
      $('result-notice').textContent = result.notice;
      $('result-title').textContent = result.mode === 'demo' ? 'Демо-результаты' : 'Результаты Алеан';
      $('search-status').textContent = result.offers.length ? `Найдено вариантов: ${result.offers.length}. Цена и наличие не подтверждены.`
        : 'Вариантов не найдено. Измените даты или бюджет.';
      for (const offer of result.offers) {
        const card = document.createElement('article'); card.className = 'card offer-card';
        for (const [tag, text, cls] of [['span',offer.mocked ? 'Вымышленный пример' : 'Требует проверки','offer-tag'],
          ['h3',offer.name,''], ['p',offer.location,''], ['p',`${offer.arrival} → ${offer.departure}`,''],
          ['p',`${Number(offer.price).toLocaleString('ru-RU')} ${offer.currency}`,'offer-price'], ['p',offer.availability,'']]) {
          const el = document.createElement(tag); el.textContent = text; el.className = cls; card.append(el);
        }
        const button = document.createElement('button'); button.className = 'secondary'; button.disabled = true;
        button.textContent = 'Бронирование недоступно'; card.append(button); $('offer-list').append(card);
      }
      $('result-title').focus();
    } catch (error) {
      if (current !== generation) return;
      $('result-title').textContent = 'Поиск не завершён';
      $('search-status').textContent = error.name === 'AbortError' ? 'Время ожидания истекло. Повторите поиск.'
        : error instanceof SyntaxError ? 'Провайдер вернул некорректный ответ. Повторите поиск.'
        : error instanceof TypeError ? 'Не удалось связаться с провайдером. Проверьте сеть и повторите.'
        : error.message || 'Ошибка сети. Повторите поиск.';
      $('retry').hidden = false;
    } finally {
      clearTimeout(timer);
      if (current === generation) { controller = null; $('alean-results').setAttribute('aria-busy','false'); }
    }
  };
  $('search').addEventListener('click', search); $('retry').addEventListener('click', search);
  tg?.ready?.(); tg?.expand?.(); syncProduct();
})();
