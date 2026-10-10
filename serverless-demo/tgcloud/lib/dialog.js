import { integer, budget, maskedPhone, kids, place, dates, moscowToday, addDays, iso } from './validation.js';

export const STEPS = ['destination','origin','dates','nights','people','kids','budget','contact'];
export const CHOICES = {
  destination: ['Египет','Турция','Таиланд','Мальдивы','ОАЭ'],
  origin: ['Архангельск','Москва','Санкт-Петербург'],
  dates: ['Ближайшие выходные','Через 1–2 недели','Через месяц','Лето','Зима','Даты гибкие'],
  nights: ['7','10','14'], people: ['1','2','3','4'], kids: ['Детей нет','До года'],
  budget: ['40 000','60 000','80 000','100 000','150 000','200 000'],
  contact: ['Telegram (демо)','Телефон (демо)','VK (демо)'],
};
const TITLES = {destination:'Направление',origin:'Город вылета',dates:'Даты',nights:'Число ночей',people:'Взрослые',kids:'Возраст детей',budget:'Бюджет',contact:'Способ связи'};
const QUESTIONS = {
  destination:'📍 Куда хотите поехать? Выберите страну или напишите направление, например Сочи.',
  origin:'🛫 Откуда вылетаете? Выберите или напишите город.',
  dates:'📅 Когда планируете поездку? Например: 15–22 июня, 10.11.2026 или 2026-11-10. Для сезонных кнопок покажем конкретную дату-пример.',
  nights:'🌙 На сколько ночей? Целое число от 1 до 28.',
  people:'👥 Сколько взрослых? Целое число от 1 до 50. Возраст детей спросим отдельно.',
  kids:'🎂 Возраст каждого ребёнка через запятую, например 5, 9. Для младенца — «до года». Максимум 10 детей до 18 лет.',
  budget:'💰 Максимальный бюджет на человека в рублях, например 100000 или 100 тыс. Укажите одну сумму, без диапазона.',
  contact:'📞 Выберите способ связи для демонстрации. Менеджер не получит анкету и не свяжется с вами.',
  phone:'📱 Введите тестовый номер +79001234567. Не используйте реальный номер. В базе демо остаётся только маска.',
  vk:'💙 Введите тестовую ссылку https://vk.com/demo_profile. Сам адрес в базе демо не сохраняется.',
};
export const INTRO = '🌴 АПРЕЛЬ тур · Подбор поездки\n\n8 коротких шагов — и ваша анкета готова.\n\nДемо: используйте вымышленные данные. Анкета не отправляется менеджеру или в CRM; бронирования и оплаты нет.\n\n/resume — продолжить · /start — новая анкета\n/privacy — хранение данных · /delete — очистить';
export const PRIVACY = 'Демо сохраняет ID чата и черновик анкеты для продолжения диалога. Имя, username, исходный телефон и ссылка VK не сохраняются: телефон маскируется, VK заменяется отметкой.\n\nЧерновик истекает через 24 часа бездействия; просроченные записи удаляются при следующем обращении к боту. /delete очищает анкету сразу; техническая запись ID/версии остаётся до истечения срока для защиты от повторов. Сообщения в истории Telegram команда не удаляет.\n\nТолько по кнопке «Проверить рейсы Tutu» отправляются города, даты и число пассажиров по возрастным группам в Tutu.ru. Контакты и ID чата туда не передаются. В CRM и менеджеру данные не отправляются.';
export function empty() { return {step:'idle', data:{}, seen:[]}; }
const clone = s => JSON.parse(JSON.stringify(s || empty()));
export function reduce(current, event, today = moscowToday()) {
  const state = clone(current);
  const text = String(event.text || '').trim();
  const cmd = text.split(/\s/)[0].split('@')[0].toLowerCase();
  const result = (notice = '', effect = null) => ({state, notice, effect});
  if (cmd === '/privacy') return result(PRIVACY);
  if (cmd === '/help') return result('Команды: /start — новое демо, /resume — продолжить, /cancel — отмена, /privacy — данные, /delete — очистить.');
  if (cmd === '/resume' || (state.restartPending && event.action === 'resume')) {
    delete state.restartPending;
    return result(state.step === 'idle' ? 'Нет сохранённой анкеты. /start — начать новое демо.' : 'Продолжаем сохранённую демо-анкету. Ответы сохранены.');
  }
  if (cmd === '/start' || (state.restartPending && event.action === 'restart')) {
    if (cmd === '/start' && Object.keys(state.data).length) { state.restartPending = true; return result(); }
    state.step = 'destination'; state.data = {}; delete state.editing; delete state.restartPending;
    return result(INTRO);
  }
  if (cmd === '/delete' || cmd === '/cancel' || event.action === 'cancel') {
    state.step = 'idle'; state.data = {}; delete state.editing; delete state.restartPending;
    return result(cmd === '/delete' ? 'Анкета очищена. /start — новое демо.' : '❌ Демо отменено. /start — начать заново.');
  }
  if (state.restartPending) return result('Выберите: продолжить анкету или начать заново.');
  if (event.action === 'back') {
    if (state.editing) { state.step = 'review'; delete state.editing; }
    else if (['phone','vk'].includes(state.step)) state.step = 'contact';
    else state.step = STEPS[Math.max(0, STEPS.indexOf(state.step) - 1)] || 'destination';
    return result();
  }
  if (state.step === 'review') {
    if (event.action === 'finish') { state.step = 'done'; return result('✅ Демо завершено. Анкета никуда не отправлена.'); }
    if (event.action === 'edit') { state.step = 'edit'; return result(); }
    return result('Подтвердите демо кнопкой или измените анкету.');
  }
  if (state.step === 'edit') {
    if (event.action === 'review') { state.step = 'review'; return result(); }
    const field = event.action?.startsWith('edit-') ? event.action.slice(5) : null;
    if (STEPS.includes(field)) { state.step = field; state.editing = true; }
    return result();
  }
  if (state.step === 'done') {
    if (event.action === 'edit') { state.step = 'edit'; return result(); }
    if (event.action === 'flights' && state.data.dates?.start) return result('', 'flights');
    return result('Демо уже завершено. /start — новый подбор.');
  }
  if (state.step === 'idle') return result('Для демонстрации нажмите /start.');
  let value = text;
  if (event.action?.startsWith('pick-')) {
    const index = integer(event.action.slice(5), 0, 20);
    value = CHOICES[state.step]?.[index];
    if (value === undefined) return result('Выберите вариант текущего шага.');
  }
  if (typeof value !== 'string' || value.length > 500) return result('Слишком длинный ввод. Используйте короткий ответ.');
  let parsed, target;
  switch (state.step) {
    case 'destination': case 'origin':
      parsed = place(value);
      if (!parsed) return result('Укажите название длиной 2–80 символов, без ссылок и цифр.');
      state.data[state.step] = parsed; break;
    case 'dates':
      parsed = dates(value, today);
      if (!parsed) return result('Не удалось разобрать будущую дату. Пример: 10.11.2026 или 15–22 июня. Диапазон — 1–28 ночей.');
      state.data.dates = parsed;
      if (parsed.nights) state.data.nights = parsed.nights;
      break;
    case 'nights':
      parsed = integer(value, 1, 28);
      if (parsed === null) return result('Укажите целое число от 1 до 28 ночей.');
      state.data.nights = parsed;
      // Nights are the source of truth after this explicit answer.
      if (state.data.dates) state.data.dates.end = state.data.dates.start ? iso(addDays(new Date(state.data.dates.start + 'T00:00:00Z'), parsed)) : null;
      break;
    case 'people':
      parsed = integer(value, 1, 50);
      if (parsed === null) return result('Укажите число взрослых от 1 до 50.');
      state.data.people = parsed; break;
    case 'kids':
      parsed = kids(value);
      if (parsed === null) return result('Укажите возраст каждого ребёнка от 0 до 17 лет: например 5, 9 или «до года».');
      state.data.kids = parsed; break;
    case 'budget':
      parsed = budget(value);
      if (parsed === null) return result('Укажите одну максимальную сумму до 1 млрд ₽: например 100 тыс. Диапазон не подходит.');
      state.data.budget = parsed; break;
    case 'contact':
      if (value === CHOICES.contact[0]) state.data.contact = 'Telegram — демо, без связи с менеджером';
      else if (value === CHOICES.contact[1]) target = 'phone';
      else if (value === CHOICES.contact[2]) target = 'vk';
      else return result('Выберите способ связи кнопкой.');
      break;
    case 'phone':
      parsed = maskedPhone(value);
      if (!parsed) return result('Номер некорректен. Для демо используйте +79001234567.');
      state.data.contact = parsed; break;
    case 'vk':
      if (!/^https:\/\/(?:vk\.com|vk\.ru)\/[A-Za-z0-9_.-]{2,100}\/?$/.test(value)) return result('Пример тестовой ссылки: https://vk.com/demo_profile');
      state.data.contact = 'VK — тестовая ссылка проверена, адрес не сохранён'; break;
    default: return result();
  }
  if (target) state.step = target;
  else if (state.editing) { state.step = 'review'; delete state.editing; }
  else if (['phone','vk','contact'].includes(state.step)) state.step = 'review';
  else state.step = STEPS[STEPS.indexOf(state.step) + 1];
  return result();
}

export function summary(data) {
  const d = data.dates;
  const departure = d.start || 'гибкие';
  const returnDate = d.start && data.nights ? iso(addDays(new Date(d.start + 'T00:00:00Z'), data.nights)) : null;
  return `📍 Направление: ${data.destination}\n🛫 Откуда: ${data.origin}\n📅 Выезд: ${departure}${returnDate ? '\n📅 Возвращение: ' + returnDate : ''}\n🌙 Ночей: ${data.nights}\n👥 Состав: ${data.people} взр.${data.kids.length ? ' + дети: ' + data.kids.map(a => a === 0 ? 'до года' : a).join(', ') : ''}\n💰 До ${String(data.budget).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')} ₽ на человека\n📞 Связь: ${data.contact}`;
}

export function render(state, version, notice = '') {
  const b = (text, action) => ({text, callback_data:`d:${version}:${action}`});
  if (state.restartPending) {
    return {text:[notice,'У вас уже есть демо-анкета.\n\nПродолжить с сохранённого шага или начать заново? При новом старте ответы будут удалены.'].filter(Boolean).join('\n\n'),
      reply_markup:{inline_keyboard:[[b('▶️ Продолжить','resume')],[b('🆕 Начать заново','restart')]]}};
  }
  let text = QUESTIONS[state.step] || '', rows = [];
  const stepIndex = STEPS.indexOf(['phone','vk'].includes(state.step) ? 'contact' : state.step);
  if (stepIndex >= 0) {
    const progress = state.editing ? '✏️ Изменение анкеты' : `Шаг ${stepIndex + 1} из ${STEPS.length} · ${TITLES[STEPS[stepIndex]]}`;
    text = progress + '\n\n' + text;
    if (state.data.destination && state.step !== 'destination') {
      text += '\n\n' + [state.data.origin, state.data.destination].filter(Boolean).join(' → ');
    }
  }
  if (CHOICES[state.step]) {
    const buttons = CHOICES[state.step].map((label, i) => b(label, `pick-${i}`));
    const columns = ['origin','contact'].includes(state.step) ? 1 : ['nights','people','budget'].includes(state.step) ? 3 : 2;
    for (let i = 0; i < buttons.length; i += columns) rows.push(buttons.slice(i, i + columns));
  }
  if (state.step === 'nights' && state.data.dates?.nights) text += `\nВведённый диапазон: ${state.data.dates.nights} ночей. Можно подтвердить или изменить.`;
  if (state.step === 'review') {
    text = '📝 Проверьте демо-анкету\n\n' + summary(state.data) + '\n\nОна не будет отправлена менеджеру или в CRM.';
    rows = [[b('✅ Завершить демо','finish')],[b('✏️ Изменить','edit'),b('✕ Отменить','cancel')]];
  } else if (state.step === 'edit') {
    text = 'Что изменить? Остальные ответы сохранятся.';
    for (let i = 0; i < STEPS.length; i += 2) rows.push(STEPS.slice(i, i + 2).map(field => b(TITLES[field], 'edit-' + field)));
    rows.push([b('◀️ К анкете','review')]);
  } else if (state.step === 'done') {
    text = 'Демо-анкета\n\n' + summary(state.data);
    if (state.data.dates.start) {
      text += '\n\nПо кнопке ниже передадим в Tutu.ru города, даты и число пассажиров по возрастным группам для поиска реальных рейсов. Контакты не передаются. Цена перелёта — не цена тура.';
      rows = [[b('✈️ Проверить рейсы Tutu','flights')]];
    } else text += '\n\nДля поиска рейсов нужны точные даты. Нажмите «Изменить» → «Даты» — остальные ответы сохранятся.';
    rows.push([b('✏️ Изменить','edit')]);
    text += '\n/delete — очистить анкету, /start — новое демо.';
  } else if (!['idle','review'].includes(state.step)) {
    const navigation = [];
    if (state.step !== 'destination' || state.editing) navigation.push(b(state.editing ? '◀️ К анкете' : '◀️ Назад','back'));
    navigation.push(b('✕ Отменить','cancel'));
    rows.push(navigation);
  }
  return {text:[notice,text].filter(Boolean).join('\n\n'), reply_markup:{inline_keyboard:rows}};
}
