'use strict';
const $ = id => document.getElementById(id);
let activeRequestId = '';
let token = '', generation = 0, detailGeneration = 0;
let demoMode = false;
const telegram = window.Telegram?.WebApp;
const telegramInitData = telegram?.initData || '';
const taskNames = {build_selection:'Подбор',send_options:'Отправить варианты',call_back:'Позвонить',flights:'Авиабилеты',visa:'Виза',documents:'Документы',check_price:'Проверить цену',next_contact:'Следующий контакт'};
function say(message){$('status').textContent=message;}
function clearData(){activeRequestId='';for(const id of ['note','due','taskNote','hotel','price','operator','meal','carrier'])$(id).value='';for(const id of ['analytics','tasks','assignment','summary','replyTemplate','quotes','activities'])$(id).replaceChildren();$('detail').hidden=true;}
function logout(){$('refresh').disabled=false;generation++;detailGeneration++;token='';$('token').value='';clearData();$('desk').hidden=true;$('login').hidden=false;say('Вы вышли.');}
function showDemo(){demoMode=true;generation++;detailGeneration++;clearData();$('login').hidden=true;$('desk').hidden=false;$('refresh').textContent='Обновить пример';$('logout').textContent='Закрыть пример';$('status').textContent='Демо-режим: синтетические данные, CRM не подключена.';renderDemo();}
function renderDemo(){const fixture=window.MANAGER_DEMO_FIXTURE;renderAnalytics({newLeads:1,channels:[{channel:'demo',count:1}],delivery:{managerNotified:1,pending:0,p95Seconds:12}});const task=fixture.task;const item=document.createElement('article');paragraph(item,[taskNames[task.type]||task.type,task.destination,task.origin,task.dates,task.note,task.budget].filter(Boolean).join(' · '));const button=document.createElement('button');button.textContent='Открыть карточку';button.addEventListener('click',()=>{activeRequestId='demo';$('detail').hidden=false;for(const id of ['summary','replyTemplate','quotes','activities'])$(id).replaceChildren();paragraph($('summary'),fixture.summary);renderReplyTemplate('Здравствуйте! Вижу вашу тестовую заявку. Уже смотрю варианты.');paragraph($('quotes'),fixture.quote);paragraph($('activities'),fixture.activity);});item.append(button);$('tasks').append(item);}
async function api(path,body){
 const authHeaders=telegramInitData?{'X-Telegram-Init-Data':telegramInitData}:{Authorization:'Bearer '+token};
 const response=await fetch('/agent-extension/crm/'+path,{method:body?'POST':'GET',body:body?JSON.stringify(body):undefined,headers:{...authHeaders,...(body?{'Content-Type':'application/json'}:{})},cache:'no-store',credentials:'omit'});
 if(response.status===401)throw Error('Ключ доступа не принят. Выйдите и проверьте ключ.');
 if(response.status===403)throw Error('Действие недоступно: проверьте роль менеджера и текущее назначение.');
 if(response.status===409){const conflict=await response.json();if(conflict.error==='assignment_changed')throw Error('Назначение уже изменилось. Обновите карточку.');throw Error('Заявка уже назначена: '+(conflict.assignment?.name||'другому менеджеру')+'.');}
 if(!response.ok)throw Error('Не удалось загрузить данные. Повторите попытку.');
 const data=await response.json();if(!data.ok)throw Error('Сервер не подтвердил результат.');return data;
}
function paragraph(parent,text){const p=document.createElement('p');p.textContent=text;parent.append(p);}
function metric(label,value){const item=document.createElement('article');item.className='metric';const strong=document.createElement('strong');strong.textContent=String(value);const text=document.createElement('span');text.textContent=label;item.append(strong,text);$('analytics').append(item);}
function renderAnalytics(summary){$('analytics').replaceChildren();metric('Новые лиды',summary.newLeads??0);metric('Доставлено менеджеру',summary.delivery?.managerNotified??0);metric('Ожидают доставки',summary.delivery?.pending??0);metric('p95 доставки',summary.delivery?.p95Seconds==null?'—':summary.delivery.p95Seconds+' с');for(const item of summary.channels||[])metric('Канал: '+item.channel,item.count);}
function renderAssignment(assignment){$('assignment').replaceChildren();if(assignment?.assigned){paragraph($('assignment'),'Ответственный: '+assignment.name);if(assignment.canRelease){const release=document.createElement('button');release.textContent='Освободить заявку';release.addEventListener('click',()=>releaseRequest(release));$('assignment').append(release);}return;}paragraph($('assignment'),'Ответственный ещё не назначен.');const button=document.createElement('button');button.textContent='Взять в работу';button.addEventListener('click',()=>claimRequest(button));$('assignment').append(button);}
function renderReplyTemplate(text){const parent=$('replyTemplate');parent.replaceChildren();if(!text){paragraph(parent,'Шаблон не настроен.');return;}const area=document.createElement('textarea');area.readOnly=true;area.value=text;area.setAttribute('aria-label','Шаблон первого ответа');const button=document.createElement('button');button.textContent='Копировать ответ';button.addEventListener('click',async()=>{try{if(!navigator.clipboard?.writeText)throw Error('clipboard unavailable');await navigator.clipboard.writeText(area.value);say('Шаблон скопирован. Проверьте текст и отправьте его вручную в нужном канале.');}catch(_){area.focus();area.select();say('Автокопирование недоступно. Текст выделен — скопируйте его вручную.');}});parent.append(area,button);}
async function claimRequest(button){if(!activeRequestId)return;button.disabled=true;say('Назначение…');try{const data=await api('assign',{requestId:activeRequestId});renderAssignment(data.assignment);say(data.duplicate?'Заявка уже была назначена вам.':'Заявка назначена вам.');}catch(e){say(e.message);}finally{button.disabled=false;}}
async function releaseRequest(button){if(!activeRequestId)return;button.disabled=true;say('Освобождение…');try{const data=await api('unassign',{requestId:activeRequestId});renderAssignment(data.assignment);say(data.duplicate?'Заявка уже была свободна.':'Назначение снято. Другой менеджер сможет взять заявку.');}catch(e){say(e.message);}finally{button.disabled=false;}}
function date(value){if(!value)return '';const raw=String(value);const d=new Date(typeof value==='number'?value*1000:(/(?:Z|[+-]\d{2}:\d{2})$/i.test(raw)?raw:raw+'Z'));return Number.isNaN(d.getTime())?'Дата не указана':d.toLocaleString('ru-RU');}
async function openRequest(id){
 activeRequestId='';for(const field of ['note','due','taskNote','hotel','price','operator','meal','carrier'])$(field).value='';const run=generation,detailRun=++detailGeneration;$('detail').hidden=true;say('Загрузка карточки…');
 try{const data=await api('timeline?requestId='+encodeURIComponent(id));if(run!==generation||detailRun!==detailGeneration)return;
 const t=data.timeline||{},r=t.request||{};for(const key of ['summary','quotes','activities'])$(key).replaceChildren();
 renderAssignment(t.assignment);paragraph($('summary'),[r.primary_destination,r.departure_city,r.dates_text,r.adults?`${r.adults} взрослых`:'',(r.children||[]).length?'Дети: '+r.children.map(c=>c.age).join(', '):'',r.budget_amount?`Бюджет: ${r.budget_amount} ${r.budget_currency||''}`:''].filter(Boolean).join(' · '));renderReplyTemplate(data.replyTemplate||'');
 for(const q of t.quotes||[]){const item=document.createElement('article');paragraph(item,[q.hotel,q.operator,q.meal_plan,`${q.price_amount??'Уточнить'} ${q.currency||''}`,date(q.calculated_at)].filter(Boolean).join(' · '));renderReaction(item,q,t.quote_reactions||[],id);$('quotes').append(item);}
 if(!(t.quotes||[]).length)paragraph($('quotes'),'Предложений пока нет.');
 for(const a of [...(t.activities||[])].reverse().slice(0,20))paragraph($('activities'),[date(a.created_at),a.summary].filter(Boolean).join(' · '));
 if(!(t.activities||[]).length)paragraph($('activities'),'Действий пока нет.');
 activeRequestId=id;$('detail').hidden=false;say('Карточка загружена.');$('detail').scrollIntoView({block:'start'});
 }catch(e){if(run===generation&&detailRun===detailGeneration)say(e.message);}
}
async function refresh(){
 if(demoMode){clearData();renderDemo();say('Демо обновлено; CRM не подключена.');return;}
 const run=++generation;detailGeneration++;clearData();$('refresh').disabled=true;say('Загрузка очереди…');
 try{const query=new URLSearchParams({now:new Date().toISOString(),tzOffsetMinutes:String(new Date().getTimezoneOffset()),limit:'100'});const [summaryData,data]=await Promise.all([api('summary'),api('today?'+query)]);if(run!==generation)return;renderAnalytics(summaryData.summary||{});
 $('login').hidden=true;$('desk').hidden=false;$('token').value='';
 for(const task of data.tasks||[]){const item=document.createElement('article');paragraph(item,[taskNames[task.type]||task.type,task.destination,task.origin,task.dates,task.note,date(task.dueAt),task.assignment?.assigned?'Ответственный: '+task.assignment.name:'Не назначено'].filter(Boolean).join(' · '));const button=document.createElement('button');button.textContent='Открыть карточку';button.addEventListener('click',()=>openRequest(task.requestId));item.append(button);$('tasks').append(item);}
 if(!(data.tasks||[]).length)paragraph($('tasks'),'На сегодня задач нет.');say('Очередь обновлена.');
 }catch(e){if(run===generation)say(e.message);}finally{if(run===generation)$('refresh').disabled=false;}
}
$('connect').addEventListener('click',async()=>{token=$('token').value.trim();if(!token){say('Введите ключ доступа.');return;}$('connect').disabled=true;try{await refresh();}finally{$('connect').disabled=false;}});
$('demo').addEventListener('click',showDemo);
$('refresh').addEventListener('click',refresh);$('logout').addEventListener('click',logout);
window.addEventListener('pagehide',logout);

async function saveEntry(kind){
 if(demoMode){say('Демо-режим только для просмотра: изменения не сохраняются.');return;}
 const id=activeRequestId,run=generation,detailRun=detailGeneration;
 if(!id)return;
 const button=$(kind==='activity'?'saveNote':'saveTask');
 let body;
 if(kind==='activity'){
  const summary=$('note').value.trim();if(!summary){say('Введите заметку.');return;}
  body={requestId:id,type:'note',summary};
 }else{
  const due=new Date($('due').value);if(!$('due').value||Number.isNaN(due.getTime())){say('Укажите дату и время задачи.');return;}
  body={requestId:id,type:$('taskType').value,dueAt:due.toISOString(),note:$('taskNote').value.trim(),priority:2};
 }
 button.disabled=true;say('Сохранение…');
 try{
  await api(kind,body);
  if(run!==generation||detailRun!==detailGeneration)return;
  if(kind==='activity'){$('note').value='';paragraph($('activities'),body.summary);}
  else{$('due').value='';$('taskNote').value='';}
  say(kind==='activity'?'Заметка сохранена.':'Задача сохранена. Обновите очередь, чтобы увидеть задачи на сегодня.');
 }catch(e){if(run===generation&&detailRun===detailGeneration)say('Сохранение не подтверждено. Перед повтором проверьте историю или очередь: запись могла сохраниться.');}
 finally{button.disabled=false;}
}
$('saveNote').addEventListener('click',()=>saveEntry('activity'));
$('saveTask').addEventListener('click',()=>saveEntry('task'));
const reactions={draft:'Черновик',sent:'Отправлено',viewed:'Просмотрено',too_expensive:'Дорого',thinking:'Думает',wants_alternative:'Другой вариант',accepted:'Подходит',rejected:'Отказ'};
function renderReaction(item,quote,events,requestId){
 const label=document.createElement('label');label.textContent='Реакция клиента';
 const select=document.createElement('select');select.setAttribute('aria-label','Реакция клиента');const matches=events.filter(e=>e.quote_id===quote.quote_id);
 const current=matches.length?matches[matches.length-1].reaction:quote.reaction;
 for(const [value,text] of Object.entries(reactions)){const option=document.createElement('option');option.value=value;option.textContent=text;option.selected=value===current;select.append(option);}
 label.append(select);const button=document.createElement('button');button.textContent='Сохранить реакцию';
 button.addEventListener('click',()=>writeQuote('reaction',{requestId,quoteId:quote.quote_id,reaction:select.value},button));item.append(label,button);
}
async function writeQuote(path,body,button){
 if(demoMode){say('Демо-режим только для просмотра: изменения не сохраняются.');return;}
 const run=generation,detailRun=detailGeneration;button.disabled=true;say('Сохранение…');
 try{await api(path,body);if(run!==generation||detailRun!==detailGeneration)return;
  if(path==='quote'){for(const id of ['hotel','price','operator','meal','carrier'])$(id).value='';}
  say('Сохранено. Откройте карточку заново для обновления истории.');
 }catch(e){if(run===generation&&detailRun===detailGeneration)say('Сохранение не подтверждено. Проверьте историю перед повтором.');}
 finally{button.disabled=false;}
}
$('saveQuote').addEventListener('click',()=>{
 if(!activeRequestId)return;
 const hotel=$('hotel').value.trim(),price=$('price').value.trim();
 if(!hotel||!/^\d+$/.test(price)||!Number.isSafeInteger(Number(price))||Number(price)<=0){say('Укажите отель и положительную целую стоимость на всех.');return;}
 writeQuote('quote',{requestId:activeRequestId,hotel,priceAmount:Number(price),currency:$('currency').value,operator:$('operator').value.trim(),mealPlan:$('meal').value.trim(),carrier:$('carrier').value.trim()},$('saveQuote'));
});

if(telegramInitData){
 telegram.ready();telegram.expand();
 $('loginHint').textContent='Проверяем подписанный доступ менеджера через Telegram…';
 refresh();
}
