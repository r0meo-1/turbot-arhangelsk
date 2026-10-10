import { addDays, iso, flightParty } from './validation.js';
const RESORTS = {'египет':'Хургада','турция':'Анталья','таиланд':'Пхукет','мальдивы':'Мале','оаэ':'Дубай'};
export function searchArgs(data) {
  if (!data.dates?.start) return null;
  const party = flightParty(data);
  if (party.adults + party.children + party.infants > 9 || party.infants > party.adults) return null;
  return {
    origin:data.origin, destination:RESORTS[data.destination.toLowerCase()] || data.destination,
    departure_date:data.dates.start,
    return_date:iso(addDays(new Date(data.dates.start + 'T00:00:00Z'), data.nights)),
    ...party, page_size:12, sort:'price_asc',
    price_max:data.budget * (data.people + data.kids.length),
  };
}
export function formatOffers(payload, args) {
  const seen = new Set(), lines = [];
  for (const raw of Array.isArray(payload?.offers) ? payload.offers : []) {
    const amount = raw.price?.amount;
    if (typeof amount !== 'number' || !Number.isFinite(amount) || amount <= 0) continue;
    const carriers = (Array.isArray(raw.carriers) ? raw.carriers : []).filter(c => typeof c === 'string').join(', ').slice(0,100);
    const departure = String(raw.departure_at || '').slice(0,40);
    const signature = JSON.stringify([amount,carriers,departure,raw.duration_min]);
    if (seen.has(signature)) continue;
    seen.add(signature);
    const currency = String(raw.price.currency || 'RUB').slice(0,8);
    lines.push(`${amount} ${currency === 'RUB' ? '₽' : currency} · ${carriers || 'перевозчик не указан'} · ${departure}`);
    if (lines.length === 3) break;
  }
  if (!lines.length) return 'Подходящих рейсов в ответе Tutu не найдено. Попробуйте другие даты или бюджет. Демо-анкета завершена; заявка не отправлялась.';
  return `✈️ Рейсы Tutu: ${args.origin} → ${args.destination}\nТуда ${args.departure_date}, обратно ${args.return_date}\nПассажиры: ${args.adults} взр. по тарифу, ${args.children} детей, ${args.infants} младенцев.\n\n${lines.join('\n')}\n\nСуммы приведены как в ответе Tutu для выбранного состава. Это перелёт, без отеля и трансфера. Цены и наличие могут измениться. Покупка не выполнялась.`;
}
export async function findFlights(fetch, data) {
  const args = searchArgs(data);
  if (!args) return 'Автопоиск доступен для точных дат и группы до 9 пассажиров, не более одного младенца на взрослого. Данные не отправлены в Tutu.';
  try {
    const response = await fetch('https://mcp.tutu.ru/mcp', {
      method:'POST', headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream'},
      body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'search_avia',arguments:args}}),
    });
    if (!response.ok) throw new Error('upstream');
    const body = await response.json();
    if (body.error || body.result?.isError) throw new Error('upstream');
    const content = body.result?.content?.find(c => c.type === 'text');
    return formatOffers(JSON.parse(content?.text || '{}'), args);
  } catch {
    return 'Сейчас Tutu не вернул доступные цены. Демо завершено успешно; попробуйте поиск позже. Цены не выдумываем.';
  }
}
