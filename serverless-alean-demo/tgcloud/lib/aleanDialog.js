import {DEMO,NOTICE,PLACES,integer,iso,today,search} from './aleanSearch.js';
import {MINI_APP_URL} from './aleanRelease.js';
export const empty = () => ({index:0,data:{},step:'form',scenario:'success',seen:[]});
const fields = s => ['kind','destination','arrival','departure','adults','children_ages','budget',...(s.data.kind==='tour'?['origin']:[])];
const prompts={kind:'Что ищем в России?',destination:'Направление: Сочи, Адлер, Анапа, Геленджик, Кисловодск или Россия.',
  arrival:'Дата заезда (ГГГГ-ММ-ДД):',departure:'Дата выезда (ГГГГ-ММ-ДД), через 1–28 ночей:',adults:'Сколько взрослых (1–8)?',
  children_ages:'Возраст каждого ребёнка (0–17) через запятую, или «нет»:',budget:'Бюджет на всех в рублях (0 — без ограничения):',origin:'Город вылета, например Москва:'};
const labels={kind:'Поиск',destination:'Направление',arrival:'Заезд',departure:'Выезд',adults:'Взрослых',children_ages:'Возраст детей',budget:'Бюджет на всех, ₽',origin:'Город вылета'};
const display=(key,value)=>key==='kind'?(value==='hotel'?'Отель':'Тур с перелётом'):Array.isArray(value)?value.join(', ')||'нет':String(value);
export function reduce(current,event,now=new Date()) {
  const s={...current,data:{...current.data}}; let notice='';
  const text=String(event.text||'').trim().slice(0,200),command=text.split('@')[0];
  let action=event.action||'';
  if (['/start','/alean'].includes(command) || action==='start') return {state:empty(),notice:DEMO+'\n'+NOTICE};
  if (['/cancel','/delete'].includes(command) || action==='cancel') return {state:{...empty(),step:'cancelled'},notice:'Поиск отменён. Ничего не отправлено.'};
  if (['/help','/privacy'].includes(command)) return {state:s,notice:DEMO+'\n'+NOTICE+'\nКонтакты не принимаются. /alean — поиск; /cancel — отмена.'};
  if (command==='/back') action='back';
  if (action==='back') {s.index=Math.max(0,s.index-1);s.step='form';delete s.result;return {state:s,notice};}
  if (action==='edit') {s.index=0;s.step='form';delete s.result;return {state:s,notice};}
  if (action.startsWith('scenario-')) {const value=action.slice(9);if(['success','empty','timeout','error'].includes(value))s.scenario=value;return {state:s,notice};}
  if (action==='search' && s.index>=fields(s).length) {
    try {s.result=search(s.data,s.scenario,now);s.step='results';return {state:s,notice,loading:true};}
    catch {return {state:s,notice:'Проверьте направление, даты, состав туристов и бюджет.'};}
  }
  if (text.startsWith('/')) return {state:s,notice:'Тестируется только демо Алеан. Используйте /alean, /back или /cancel. Отправка заявок отключена.'};
  const key=fields(s)[s.index];
  if (!key) return {state:s,notice:'Используйте последние кнопки поиска, изменения или отмены.'};
  let value=text;
  if (action==='keep') value=display(key,s.data[key]);
  else if (action.startsWith('kind-') && key==='kind') value=action.slice(5);
  else if (action==='sochi' && key==='destination') value='Сочи';
  else if (action==='adler' && key==='destination') value='Адлер';
  else if (action) return {state:s,notice:'Эта кнопка не относится к текущему шагу.'};
  try {
    if (key==='kind') {
      value=({'отель':'hotel','тур':'tour','тур с перелётом':'tour'})[value.toLowerCase()]||value;
      if (!['hotel','tour'].includes(value)) throw new Error();
    } else if (key==='destination') {
      value=PLACES.find(name=>name.toLowerCase()===value.toLowerCase());if(!value)throw new Error();
    } else if (['arrival','departure'].includes(key)) {
      value=iso(value);if(value<today(now))throw new Error();
      if(key==='departure'){const n=(Date.parse(value)-Date.parse(s.data.arrival))/86400000;if(n<1||n>28)throw new Error();}
    } else if(key==='adults')value=integer(value,1,8);
    else if(key==='budget')value=integer(value.replaceAll(' ',''),0,10000000);
    else if(key==='children_ages') {
      value=['нет','без детей','-'].includes(value.toLowerCase())?[]:value.split(/[,\s]+/).map(age=>integer(age,0,17));
      if(value.length>6)throw new Error();
    } else if(key==='origin' && (value.length<2||value.length>100||/[\x00-\x1f]/.test(value)))throw new Error();
  } catch {return {state:s,notice:'Проверьте ответ: даты ГГГГ-ММ-ДД, целые числа и возраста 0–17.'};}
  s.data[key]=value;
  if(key==='arrival'&&s.data.departure<=value)delete s.data.departure;
  s.index++;s.step='form';return {state:s,notice};
}
export function render(s,version,notice='') {
  const button=(text,action)=>({text,callback_data:`a:${version}:${action}`});
  const nav=[button('← Назад','back'),button('Отмена','cancel')];
  const rows=[];let text=DEMO+'\n'+NOTICE+'\n\n';
  if(notice)text+=notice+'\n\n';
  if(s.step==='cancelled') {text+='Поиск отменён. Новый поиск: /alean.';rows.push([button('Начать поиск Алеан','start')]);}
  else if(s.step==='results') {
    const r=s.result;
    text+=r.error||(!r.offers.length?'Вариантов не найдено. Измените даты или бюджет.':'Демо-результаты:');
    for(const o of r.offers)text+=`\n\n${o.name} • ${o.location}\n${o.arrival} → ${o.departure}\n${o.price.toLocaleString('ru-RU')} ${o.currency}\n${o.availability}`;
    rows.push([button('Изменить параметры','edit'),button('Повторить поиск','search')]);rows.push(nav);
  } else {
    const key=fields(s)[s.index];
    if(!key) {
      text+='Алеан — проверьте параметры:\n'+fields(s).map(k=>`${labels[k]}: ${display(k,s.data[k])}`).join('\n');
      rows.push([button('Искать','search')],nav);
    } else {
      text+='Алеан • '+prompts[key];
      if(key==='kind')rows.push([button('Отель','kind-hotel'),button('Тур с перелётом','kind-tour')]);
      if(key==='destination')rows.push([button('Сочи','sochi'),button('Адлер','adler')]);
      if(s.data[key]!==undefined){text+='\nСохранённый ответ: '+display(key,s.data[key]);rows.push([button('Оставить ответ','keep')]);}
      rows.push(nav);
    }
  }
  if(s.index>=fields(s).length&&s.step!=='cancelled') {
    text+='\n\nДемо-сценарий: '+s.scenario;
    rows.push([button('Результаты','scenario-success'),button('Нет результатов','scenario-empty')],
      [button('Таймаут','scenario-timeout'),button('Ошибка','scenario-error')]);
  }
  rows.push([{text:'🏨 Алеан Mini App',web_app:{url:MINI_APP_URL}}]);
  return {text,reply_markup:{inline_keyboard:rows}};
}
