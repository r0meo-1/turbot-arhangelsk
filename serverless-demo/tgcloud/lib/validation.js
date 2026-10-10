export function integer(raw, min, max) {
  if (!/^\d+$/.test(String(raw).trim())) return null;
  const n = Number(raw);
  return Number.isSafeInteger(n) && n >= min && n <= max ? n : null;
}
export function budget(raw) {
  if (typeof raw !== 'string' || raw.length > 100) return null;
  const m = raw.match(/^\s*(?:до\s+)?(\d+|\d{1,3}(?:[ \u00a0\u202f]\d{3})+)\s*(тыс\.?|тысяч(?:а|и)?|[кk])?\s*(?:₽|руб\.?|рублей|рубля|рубль|р\.?)?\s*$/i);
  if (!m) return null;
  const value = Number(m[1].replace(/\s/g, '')) * (m[2] ? 1000 : 1);
  return Number.isSafeInteger(value) && value > 0 && value <= 1000000000 ? value : null;
}
export function maskedPhone(raw) {
  if (typeof raw !== 'string' || raw.length > 100 || !/^\+?\d[\d\s()\u2010-\u2014-]*$/.test(raw.trim())) return null;
  let digits = raw.replace(/\D/g, '');
  if (raw.trim().startsWith('+') && digits.length !== 11) return null;
  if (digits.length === 10) digits = '7' + digits;
  if (digits.length === 11 && digits.startsWith('8')) digits = '7' + digits.slice(1);
  if (!/^7\d{10}$/.test(digits)) return null;
  return '+' + digits.slice(0, 4) + '***' + digits.slice(-4);
}
export function kids(raw) {
  if (typeof raw !== 'string' || raw.length > 500) return null;
  if (/^(?:0+|нет(?: детей)?|без детей|детей нет)$/i.test(raw.trim())) return [];
  const ages = [];
  for (let chunk of raw.trim().split(/[,;\/\n]|\s+и\s+/i)) {
    chunk = chunk.trim();
    if (!chunk) return null;
    if (/^(до года|полгода|младенец|грудничок)$/i.test(chunk)) { ages.push(0); continue; }
    const months = chunk.match(/^(\d{1,3})\s*(?:месяц(?:а|ев)?|мес\.?)$/i);
    if (months) { ages.push(Math.floor(Number(months[1]) / 12)); continue; }
    if (!/^\d{1,2}(?:\s+(?:\d{1,2}|год|года|лет))*$/i.test(chunk)) return null;
    ages.push(...chunk.match(/\d+/g).map(Number));
  }
  return ages.length > 0 && ages.length <= 10 && ages.every(n => n <= 17) ? ages : null;
}
export function place(raw) {
  if (typeof raw !== 'string') return null;
  const value = raw.trim().replace(/\s+/g, ' ');
  return value.length >= 2 && value.length <= 80 && /^[\p{L}\p{M} ,.'’()-]+$/u.test(value) ? value : null;
}
export const iso = d => d.toISOString().slice(0, 10);
export const addDays = (d, n) => new Date(d.getTime() + n * 86400000);
export function moscowToday(now = new Date()) {
  return new Date(iso(new Date(now.getTime() + 3 * 3600000)) + 'T00:00:00Z');
}
const MONTHS = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
function calendar(y, m, d) {
  const result = new Date(Date.UTC(y, m - 1, d));
  return result.getUTCFullYear() === y && result.getUTCMonth() === m - 1 && result.getUTCDate() === d ? result : null;
}
export function dates(raw, today = moscowToday()) {
  const text = String(raw).trim().toLowerCase().replace(/\s+/g, ' ');
  let start, end;
  if (text === 'через месяц') {
    const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 1));
    const lastDay = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
    start = calendar(first.getUTCFullYear(), first.getUTCMonth() + 1, Math.min(today.getUTCDate(), lastDay));
  } else if (text === 'через 1–2 недели' || text === 'через 1-2 недели') start = addDays(today, 10);
  else if (text === 'ближайшие выходные') start = addDays(today, (6 - today.getUTCDay() + 7) % 7 || 7);
  else if (text === 'даты гибкие') return {label:'Даты гибкие', start:null, end:null};
  else if (text === 'лето' || text === 'зима') {
    const month = text === 'лето' ? 7 : 1;
    start = calendar(today.getUTCFullYear(), month, 15);
    if (start < today) start = calendar(today.getUTCFullYear() + 1, month, 15);
  } else {
    const exact = text.match(/^(20\d{2})-(\d{2})-(\d{2})$/);
    const numeric = text.match(/^(\d{1,2})\.(\d{1,2})(?:\.(20\d{2}))?$/);
    const russian = text.match(/^(?:с )?(\d{1,2})(?:\s*[-–—]\s*(\d{1,2})| по (\d{1,2}))? ([а-я]+)(?: (20\d{2}))?$/);
    let year, month, day, endDay, explicit;
    if (exact) { year = +exact[1]; month = +exact[2]; day = +exact[3]; explicit = true; }
    else if (numeric) { year = +(numeric[3] || today.getUTCFullYear()); month = +numeric[2]; day = +numeric[1]; explicit = !!numeric[3]; }
    else if (russian && MONTHS.includes(russian[4])) {
      year = +(russian[5] || today.getUTCFullYear()); month = MONTHS.indexOf(russian[4]) + 1;
      day = +russian[1]; endDay = +(russian[2] || russian[3]) || null; explicit = !!russian[5];
    } else return null;
    start = calendar(year, month, day);
    if (!start) return null;
    if (!explicit && start < today) { year++; start = calendar(year, month, day); }
    if (endDay) end = calendar(year, month, endDay);
    if (!start || start < today || (endDay && (!end || end <= start))) return null;
  }
  if (!start || start < today) return null;
  const duration = end ? Math.round((end - start) / 86400000) : null;
  if (duration !== null && (duration < 1 || duration > 28)) return null;
  return {label:raw, start:iso(start), end:end ? iso(end) : null, nights:duration};
}
export function flightParty(data) {
  return {
    adults: data.people + data.kids.filter(a => a >= 12).length,
    children: data.kids.filter(a => a >= 2 && a < 12).length,
    infants: data.kids.filter(a => a < 2).length,
  };
}
