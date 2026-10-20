// Explicitly run through tgcloud before switching update delivery. No messages
// or database writes: exercises the actual hosted SDK and pure demo modules.
import { api, db } from 'sdk';
import { empty, reduce, render } from './dialog.js';
import { dates, budget, maskedPhone } from './validation.js';
export default async function () {
  const bot = await api.getMe();
  if (bot.id !== 8886586430 || bot.username !== 'apreltour_bot') throw new Error('Wrong target bot');
  const check = await db.get('SELECT 1 AS ok');
  if (check.ok !== 1) throw new Error('Database query failed');
  const today = new Date('2026-10-10T00:00:00Z');
  if (dates('Через месяц', today).start !== '2026-11-10') throw new Error('Calendar check failed');
  if (budget('100 тыс') !== 100000 || budget('100000–120000') !== null) throw new Error('Budget check failed');
  if (maskedPhone('+79001234567') !== '+7900***4567') throw new Error('Phone check failed');
  let state = empty();
  for (const text of ['/start','Турция','Москва','Через месяц','7','2','нет','100 тыс']) state = reduce(state,{text},today).state;
  state = reduce(state,{action:'pick-0'},today).state;
  if (state.step !== 'review' || !render(state,1).text.includes('2026-11-10')) throw new Error('Review check failed');
  state = reduce(state,{action:'finish'},today).state;
  if (state.step !== 'done') throw new Error('Completion check failed');
  return {bot:bot.username,botId:bot.id,checks:'passed',sentMessages:0,databaseWrites:0};
}
