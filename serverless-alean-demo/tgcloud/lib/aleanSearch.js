import {assertDemo} from './aleanConfig.js';
export const PLACES = ['Сочи','Адлер','Анапа','Геленджик','Кисловодск','Россия'];
export const NOTICE = 'Бронирование, оплата и отправка заявки отключены. Цена и наличие не подтверждены.';
export const DEMO = 'ДЕМО: вымышленные отели, цены и наличие; это не предложения Алеан.';
export const today = (now = new Date()) => new Date(now.getTime()+3*3600000).toISOString().slice(0,10);
export function integer(value,min,max) {
  if (typeof value === 'boolean' || !/^\d{1,8}$/.test(String(value)) || !Number.isSafeInteger(Number(value)) || Number(value)<min || Number(value)>max)
    throw new Error('Некорректное целое число.');
  return Number(value);
}
export function iso(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error('Дата: ГГГГ-ММ-ДД.');
  const parsed = new Date(value+'T00:00:00Z');
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0,10)!==value) throw new Error('Некорректная дата.');
  return value;
}
export function validate(raw,now=new Date()) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Проверьте параметры.');
  const kind=raw.kind;
  if (!['hotel','tour'].includes(kind)) throw new Error('Выберите отель или тур.');
  const destination=PLACES.find(name=>name.toLowerCase()===String(raw.destination||'').trim().toLowerCase());
  if (!destination) throw new Error('Выберите направление в России.');
  const arrival=iso(raw.arrival),departure=iso(raw.departure);
  const nights=(Date.parse(departure)-Date.parse(arrival))/86400000;
  if (arrival<today(now) || nights<1 || nights>28) throw new Error('Заезд не в прошлом; выезд через 1–28 ночей.');
  if (!Array.isArray(raw.children_ages) || raw.children_ages.length>6) throw new Error('Укажите возраст каждого ребёнка.');
  const origin=String(raw.origin||'Москва').trim();
  if (origin.length<2 || origin.length>100 || /[\x00-\x1f]/.test(origin)) throw new Error('Укажите город вылета.');
  return {kind,destination,arrival,departure,nights,adults:integer(raw.adults,1,8),
    children_ages:raw.children_ages.map(age=>integer(age,0,17)),budget:integer(raw.budget,0,10000000),origin};
}
export function search(raw,scenario='success',now=new Date()) {
  assertDemo(); const query=validate(raw,now);
  if (!['success','empty','timeout','error'].includes(scenario)) throw new Error('Неизвестный демо-сценарий.');
  const result={ok:true,state:'results',mode:'demo',query,offers:[],notice:DEMO+' '+NOTICE,
    booking_enabled:false,submission_enabled:false};
  if (['timeout','error'].includes(scenario)) return {...result,ok:false,state:scenario,
    error:scenario==='timeout'?'Демо: время ожидания истекло.':'Демо: провайдер временно недоступен.'};
  if (scenario!=='empty') {
    let base=query.nights*(query.adults*3800+query.children_ages.length*1800);
    if (query.kind==='tour') base+=query.adults*20000;
    ['Морской бриз','Солнечный двор','Тихая гавань'].forEach((name,index)=>{
      const price=base+index*5000;
      if (query.budget && price>query.budget) return;
      result.offers.push({id:`demo-${index}`,name:`Демо • ${name} (вымышленный)`,location:query.destination,
        arrival:query.arrival,departure:query.departure,price,currency:'RUB',availability:'Демо: условное наличие',
        confirmed:false,booking_url:null,mocked:true});
    });
  }
  if (!result.offers.length) result.state='empty';
  return result;
}
