(() => {
  const input = document.getElementById('departure');
  if (!input) return;
  const box = document.createElement('div');
  box.id = 'departure-support';
  box.setAttribute('role', 'status');
  box.setAttribute('aria-live', 'polite');
  box.className = 'card';
  box.hidden = true;
  input.closest('.grid')?.insertAdjacentElement('afterend', box);
  if (!box.isConnected) input.insertAdjacentElement('afterend', box);
  let catalog;
  let renderedCity;
  const normalize = value => String(value).toLocaleLowerCase('ru').replaceAll('ё', 'е').trim().replace(/\s+/g, ' ');
  const render = () => {
    const city = input.value.trim();
    // A blur/change event must not remove a suggestion under the pointer
    // between pointerdown and click.
    if (catalog && renderedCity === city) return;
    renderedCity = catalog ? city : undefined;
    box.replaceChildren();
    box.hidden = true;
    if (!city || !catalog) return;
    if (!catalog.complete) {
      box.hidden = false;
      box.textContent = 'Поддержку города проверим при поиске. Параметры можно сохранить.';
      return;
    }
    if (catalog.departures.some(name => normalize(name) === normalize(city))) return;
    box.hidden = false;
    const text = document.createElement('p');
    text.textContent = `Автопоиск с вылетом из «${city.slice(0, 80)}» не поддерживается подключёнными источниками. Это не означает, что туров нет: наличие не проверено. Город не менялся.`;
    box.append(text);
    for (const candidate of ['Москва', 'Санкт-Петербург']) {
      if (!catalog.departures.some(name => normalize(name) === normalize(candidate))) continue;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chip';
      button.textContent = `Вылет из: ${candidate}`;
      button.addEventListener('click', () => {
        input.value = candidate;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      box.append(button);
    }
  };
  input.addEventListener('input', render);
  input.addEventListener('change', render);
  document.getElementById('trip-form')?.addEventListener('reset', () => queueMicrotask(render));
  const url = location.pathname.startsWith('/vk/miniapp/') ? './departures'
    : location.hostname === 'r0meo-1.github.io' ? 'https://bot.r0meo1.ru/miniapp/departures' : '/miniapp/departures';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  fetch(url, { signal: controller.signal }).then(async response => {
    if (!response.ok) throw new Error('Unavailable');
    const value = await response.json();
    if (typeof value.complete !== 'boolean' || !Array.isArray(value.departures)
        || !value.departures.every(name => typeof name === 'string')) throw new Error('Invalid');
    catalog = value;
    render();
  }).catch(() => { catalog = { complete: false, departures: [] }; render(); })
    .finally(() => clearTimeout(timer));
})();
