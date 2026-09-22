'use strict';

// Synthetic, deterministic, and read-only. Never put tokens or customer data here.
window.MANAGER_DEMO_FIXTURE = Object.freeze({
  task: Object.freeze({
    type: 'next_contact', destination: 'ДЕМО: Кипр', origin: 'Москва',
    dates: '10 ночей', note: '2 взрослых + дети 3, 9', budget: '150 000 ₽',
  }),
  summary: 'ДЕМО: Кипр · 2 взрослых · дети: 3, 9 · 10 ночей · бюджет 150 000 RUB',
  quote: 'ДЕМО отель · 150000 RUB · AI · пример предложения',
  activity: 'Пример заметки: уточнить прямой перелёт.',
});
