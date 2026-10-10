import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { budget, maskedPhone, kids, dates, moscowToday, flightParty, place } from '../tgcloud/lib/validation.js';
import { empty, reduce, render } from '../tgcloud/lib/dialog.js';
import { handle } from '../tgcloud/lib/service.js';
import { searchArgs, findFlights, readRpcResponse } from '../tgcloud/lib/flights.js';

test('place requires a letter while accepting international names', () => {
  for (const value of ['--','()','..','\u0301\u0300']) assert.equal(place(value),null);
  for (const value of ['Москва','Ростов-на-Дону','Xi’an','São Paulo']) assert.equal(place(value),value);
});

function sse(text) {
  const bytes = new TextEncoder().encode(text);
  return {ok:true,headers:{get:()=> 'text/event-stream; charset=utf-8'},
    body:(async function*() { for (const byte of bytes) yield new Uint8Array([byte]); })()};
}
test('SSE decodes split UTF-8, CRLF, multiline data and skips notifications', async () => {
  const response = sse(': keepalive\r\n\r\ndata: {"method":"notifications/progress"}\r\n\r\n' +
    'event: message\r\ndata: {"jsonrpc":"2.0","id":1,\r\ndata: "result":{"text":"Москва"}}\r\n\r\n');
  assert.equal((await readRpcResponse(response,1)).result.text,'Москва');
});
test('SSE flight results and structured content preserve returned prices', async () => {
  const result = {structuredContent:{offers:[{price:{amount:12345.67,currency:'RUB'},carriers:['Test Air']}]}};
  const text = await findFlights(async()=>sse('data: '+JSON.stringify({id:1,result})+'\n\n'),flow().data);
  assert.match(text,/12345.67/);
});
test('truncated SSE, RPC errors and unknown payloads report unavailable, not no flights', async () => {
  const responses = [sse('data: {"id":1'), sse('data: {"id":1,"error":{"code":-1}}\n\n'),
    {ok:true,json:async()=>({result:{structuredContent:{unexpected:[]}}})}];
  for (const response of responses) assert.match(await findFlights(async()=>response,flow().data),/не вернул/);
});

const today = new Date('2026-10-10T00:00:00Z');
function flow() {
  let state = empty();
  for (const text of ['/start','Турция','Москва','Через месяц','7','2','5, до года','100 тыс']) state = reduce(state,{text},today).state;
  state = reduce(state,{action:'pick-1'},today).state;
  return reduce(state,{text:'+79001234567'},today).state;
}
test('budget rejects ranges, exponents, overflow and unrelated digits', () => {
  for (const value of ['100 тыс','100к','100 000 ₽','до 100000 руб.']) assert.equal(budget(value),100000);
  for (const value of ['100000–120000','1e5','0','-1','1.5 тыс','123 456 78','999999999999999999999']) assert.equal(budget(value),null);
});
test('phones are validated and returned only as masks', () => {
  for (const value of ['+79001234567','8 (900) 123-45-67','9001234567']) assert.equal(maskedPhone(value),'+7900***4567');
  for (const value of ['123','call +79001234567','+9001234567']) assert.equal(maskedPhone(value),null);
});
test('child ages and airline fare bands preserve infants and teenagers', () => {
  assert.deepEqual(kids('8 месяцев и 4'),[0,4]);
  assert.deepEqual(kids('0'),[]);
  assert.deepEqual(kids('0, 5'),[0,5]);
  assert.equal(kids('18'),null); assert.equal(kids('-1'),null); assert.equal(kids('5,,9'),null);
  assert.deepEqual(flightParty({people:2,kids:[0,1,5,12,17]}),{adults:4,children:1,infants:2});
});
test('calendar month, year boundary, leap year and Moscow midnight', () => {
  assert.equal(dates('Через месяц',today).start,'2026-11-10');
  assert.equal(dates('Через месяц',new Date('2026-01-31T00:00:00Z')).start,'2026-02-28');
  assert.equal(dates('Через месяц',new Date('2028-01-31T00:00:00Z')).start,'2028-02-29');
  assert.equal(dates('Через месяц',new Date('2026-12-31T00:00:00Z')).start,'2027-01-31');
  assert.equal(moscowToday(new Date('2026-10-09T21:30:00Z')).toISOString().slice(0,10),'2026-10-10');
});
test('exact dates reject invalid/past/reversed ranges and preserve ranges', () => {
  for (const value of ['31 ноября','2026-02-30','2020-01-01','22–15 ноября','1–31 ноября']) assert.equal(dates(value,today),null);
  assert.equal(dates('15–22 июня',today).start,'2027-06-15');
  assert.equal(dates('15–22 июня',today).nights,7);
  assert.equal(dates('10.11.2026',today).start,'2026-11-10');
  assert.equal(dates('Даты гибкие',today).start,null);
});
test('complete demo, edit nights, cancel and reset', () => {
  const state = flow();
  assert.equal(state.step,'review');
  assert.equal(state.data.budget,100000);
  assert.equal(state.data.contact,'+7900***4567');
  const view = render(state,123);
  assert.match(view.text,/2026-11-10/); assert.match(view.text,/2026-11-17/);
  assert.ok(!JSON.stringify(state).includes('+79001234567'));
  let edit = reduce(state,{action:'edit'},today).state;
  assert.ok(render(edit,124).reply_markup.inline_keyboard.flat().some(b => b.text === 'Число ночей'));
  edit = reduce(edit,{action:'edit-nights'},today).state;
  const invalid = reduce(edit,{text:'0'},today);
  assert.equal(invalid.state.step,'nights'); assert.match(invalid.notice,/1 до 28/);
  edit = reduce(edit,{text:'10'},today).state;
  assert.equal(edit.step,'review'); assert.equal(edit.data.nights,10); assert.equal(edit.data.budget,100000);
  assert.equal(reduce(edit,{action:'finish'},today).state.step,'done');
  assert.deepEqual(reduce(edit,{text:'/cancel'},today).state.data,{});
  assert.equal(reduce(edit,{text:'/start'},today).state.step,'destination');
});
test('editing contact back does not lose previous answers, free text cannot finish', () => {
  const state = flow();
  assert.equal(reduce(state,{text:'Отправить менеджеру'},today).state.step,'review');
  let edit = reduce(state,{action:'edit'},today).state;
  edit = reduce(edit,{action:'edit-contact'},today).state;
  edit = reduce(edit,{action:'pick-2'},today).state;
  assert.equal(edit.step,'vk');
  edit = reduce(edit,{text:'https://vk.com/demo_profile'},today).state;
  assert.equal(edit.step,'review'); assert.ok(!JSON.stringify(edit).includes('demo_profile'));
});
test('all generated callback payloads fit Telegram limit', () => {
  const s = flow();
  for (const step of ['destination','origin','dates','nights','people','kids','budget','contact','phone','vk','review','edit','done']) {
    for (const b of render({...s,step},2147483647).reply_markup.inline_keyboard.flat()) assert.ok(Buffer.byteLength(b.callback_data)<=64);
  }
});
function harness() {
  const sqlite = new DatabaseSync(':memory:');
  sqlite.exec('CREATE TABLE aprel_demo_sessions(chat_id INTEGER PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,expires INTEGER NOT NULL)');
  const sent = []; let failSend = false, flightCalls = 0;
  const db = {
    async run(sql,params) { await Promise.resolve(); const r=sqlite.prepare(sql).run(params); return {rowsAffected:r.changes}; },
    async get(sql,params) { await Promise.resolve(); return sqlite.prepare(sql).get(params); },
  };
  const deps = {db,api:{async sendMessage(m) {if(failSend){failSend=false;throw new Error('network');} sent.push(m);},async answerCallbackQuery(){}},now:()=>new Date('2026-10-10T09:00:00Z'),findFlights:async()=>{flightCalls++;return 'Рейсы';}};
  let id=1;
  const message = (text,update=id++,messageId=update) => handle(deps,'message',{chat:{id:42,type:'private'},from:{id:42},message_id:messageId,text},{update:{update_id:update}});
  const callback = (data,update=id++) => handle(deps,'callback',{id:'cb'+update,from:{id:42},message:{chat:{id:42,type:'private'},message_id:1},data},{update:{update_id:update}});
  const row = () => sqlite.prepare('SELECT * FROM aprel_demo_sessions').get();
  const state = () => JSON.parse(row().payload);
  const button = action => `d:${row().version}:${action}`;
  return {deps,sent,message,callback,row,state,button,fail:()=>{failSend=true;},flights:()=>flightCalls,sqlite};
}
async function complete(h) {
  for(const text of ['/start','Турция','Москва','Через месяц','7','2','нет','100 тыс']) await h.message(text);
  await h.callback(h.button('pick-0'));
}

test('late text cannot overwrite a newer callback answer or move version backwards',async()=>{
  const h=harness();
  await h.message('/start',1,1);
  await h.callback(h.button('pick-1'),3);
  const before=h.row(); const count=h.sent.length;
  await h.message('Москва',2,2);
  assert.deepEqual(h.row(),before);
  assert.equal(h.sent.length,count);
  await h.message('Москва',4,3);
  assert.equal(h.state().step,'dates');
});

test('update watermark rejects old callbacks even after bounded seen history rolls over',async()=>{
  const h=harness(); await h.message('/start',100,1);
  const button=h.button('pick-1');
  const state=h.state(); state.seen=[];
  h.sqlite.prepare('UPDATE aprel_demo_sessions SET payload=?').run(JSON.stringify(state));
  const before=h.row(); const count=h.sent.length;
  await h.callback(button,99);
  assert.deepEqual(h.row(),before);
  assert.equal(h.sent.length,count);
});
test('actual SQLite: duplicate and simultaneous update processing is idempotent',async()=>{
  const h=harness();
  const concurrent = await Promise.allSettled([h.message('/start',1),h.message('/start',1)]);
  assert.ok(concurrent.some(r=>r.status==='fulfilled'));
  await h.message('/start',1);
  assert.equal(h.state().step,'destination'); assert.equal(h.sent.length,1);
  const old=h.button('pick-1');
  await h.callback(old,2); await h.callback(old,3);
  assert.equal(h.state().step,'origin'); assert.match(h.sent.at(-1).text,/устарела/);
});
test('actual SQLite: duplicate completion, old buttons after restart, and no CRM actions',async()=>{
  const h=harness(); await complete(h);
  const finish=h.button('finish');
  await Promise.all([h.callback(finish,50),h.callback(finish,51)]);
  assert.equal(h.state().step,'done'); assert.equal(h.flights(),0);
  await h.message('/start',60); await h.callback(finish,61);
  assert.equal(h.state().step,'destination'); assert.deepEqual(h.state().data,{});
});
test('actual SQLite: save before send, recover failed prompt on retry without losing input',async()=>{
  const h=harness(); h.fail();
  await assert.rejects(h.message('/start',1));
  assert.equal(h.state().step,'destination'); assert.equal(h.state().pending,true);
  await h.message('/start',1);
  assert.equal(h.sent.length,1); assert.equal(h.state().pending,false);
});
test('actual SQLite: raw phone is not stored and delete clears trip immediately',async()=>{
  const h=harness();
  for(const text of ['/start','Турция','Москва','Через месяц','7','2','нет','100 тыс']) await h.message(text);
  await h.callback(h.button('pick-1')); await h.message('+79001234567');
  assert.ok(!h.row().payload.includes('1234567')); assert.match(h.state().data.contact,/\*\*\*/);
  await h.message('/delete'); assert.deepEqual(h.state().data,{});
});
test('actual SQLite: expired drafts are removed on traffic and callbacks cannot restore them',async()=>{
  const h=harness(); await h.message('/start'); const old=h.button('pick-1');
  h.deps.now=()=>new Date('2026-10-12T09:00:00Z');
  await h.callback(old); assert.equal(h.row(),undefined); assert.match(h.sent.at(-1).text,/устарела/);
});
test('foreign chat/contact and missing update context never save a draft',async()=>{
  const h=harness();
  await handle(h.deps,'message',{chat:{id:-42,type:'group'},from:{id:42},text:'/start'},{update:{update_id:1}});
  assert.equal(h.row(),undefined);
  await assert.rejects(handle(h.deps,'message',{chat:{id:42,type:'private'},from:{id:42},text:'/start'},{}));
  await handle(h.deps,'message',{chat:{id:42,type:'private'},from:{id:42},contact:{user_id:99,phone_number:'+79001234567'}},{update:{update_id:1}});
  assert.equal(h.row(),undefined);
});
test('flight search excludes contacts, respects age bands and uses return date',async()=>{
  const data=flow().data;
  const args=searchArgs(data);
  assert.equal(args.destination,'Анталья'); assert.equal(args.return_date,'2026-11-17');
  assert.deepEqual([args.adults,args.children,args.infants],[2,1,1]);
  let captured;
  const text=await findFlights(async(url,opts)=>{captured=opts.body;return {ok:true,json:async()=>({result:{content:[{type:'text',text:JSON.stringify({offers:[{price:{amount:12345.67,currency:'RUB'},carriers:['Test Air'],departure_at:'2026-11-10T12:00:00'}]})}]}})};},data);
  assert.ok(!captured.includes('contact')); assert.ok(!captured.includes('7900')); assert.match(text,/12345.67/);
});
test('upstream failure and oversized party do not fabricate prices',async()=>{
  const data=flow().data;
  assert.match(await findFlights(async()=>{throw new Error('network');},data),/не вернул/);
  assert.match(await findFlights(async()=>{throw new Error('must not call');},{...data,people:50}),/до 9/);
});
