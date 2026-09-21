'use strict';
const $ = id => document.getElementById(id);
let activeRequestId = '';
let token = '', generation = 0, detailGeneration = 0;
const taskNames = {build_selection:'Подбор',send_options:'Отправить варианты',call_back:'Позвонить',flights:'Авиабилеты',visa:'Виза',documents:'Документы',check_price:'Проверить цену',next_contact:'Следующий контакт'};
function say(message){$('status').textContent=message;}
function clearData(){activeRequestId='';for(const id of ['note','due','taskNote'])$(id).value='';for(const id of ['tasks','summary','quotes','activities'])$(id).replaceChildren();$('detail').hidden=true;}
function logout(){$('refresh').disabled=false;generation++;detailGeneration++;token='';$('token').value='';clearData();$('desk').hidden=true;$('login').hidden=false;say('Вы вышли.');}
async function api(path,body){
 const response=await fetch('/agent-extension/crm/'+path,{method:body?'POST':'GET',body:body?JSON.stringify(body):undefined,headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},cache:'no-store',credentials:'omit'});
 if(response.status===401)throw Error('Ключ доступа не принят. Выйдите и проверьте ключ.');
 if(!response.ok)throw Error('Не удалось загрузить данные. Повторите попытку.');
 const data=await response.json();if(!data.ok)throw Error('Сервер не подтвердил результат.');return data;
}
function paragraph(parent,text){const p=document.createElement('p');p.textContent=text;parent.append(p);}
function date(value){if(!value)return '';const raw=String(value);const d=new Date(typeof value==='number'?value*1000:(/(?:Z|[+-]\d{2}:\d{2})$/i.test(raw)?raw:raw+'Z'));return Number.isNaN(d.getTime())?'Дата не указана':d.toLocaleString('ru-RU');}
async function openRequest(id){
 activeRequestId='';for(const field of ['note','due','taskNote'])$(field).value='';const run=generation,detailRun=++detailGeneration;$('detail').hidden=true;say('Загрузка карточки…');
 try{const data=await api('timeline?requestId='+encodeURIComponent(id));if(run!==generation||detailRun!==detailGeneration)return;
 const t=data.timeline||{},r=t.request||{};for(const key of ['summary','quotes','activities'])$(key).replaceChildren();
 paragraph($('summary'),[r.primary_destination,r.departure_city,r.dates_text,r.adults?`${r.adults} взрослых`:'',(r.children||[]).length?'Дети: '+r.children.map(c=>c.age).join(', '):'',r.budget_amount?`Бюджет: ${r.budget_amount} ${r.budget_currency||''}`:''].filter(Boolean).join(' · '));
 for(const q of t.quotes||[]){const item=document.createElement('article');paragraph(item,[q.hotel,q.operator,q.meal_plan,`${q.price_amount??'Уточнить'} ${q.currency||''}`,date(q.calculated_at)].filter(Boolean).join(' · '));$('quotes').append(item);}
 if(!(t.quotes||[]).length)paragraph($('quotes'),'Предложений пока нет.');
 for(const a of [...(t.activities||[])].reverse().slice(0,20))paragraph($('activities'),[date(a.created_at),a.summary].filter(Boolean).join(' · '));
 if(!(t.activities||[]).length)paragraph($('activities'),'Действий пока нет.');
 activeRequestId=id;$('detail').hidden=false;say('Карточка загружена.');$('detail').scrollIntoView({block:'start'});
 }catch(e){if(run===generation&&detailRun===detailGeneration)say(e.message);}
}
async function refresh(){
 const run=++generation;detailGeneration++;clearData();$('refresh').disabled=true;say('Загрузка очереди…');
 try{const query=new URLSearchParams({now:new Date().toISOString(),tzOffsetMinutes:String(new Date().getTimezoneOffset()),limit:'100'});const data=await api('today?'+query);if(run!==generation)return;
 $('login').hidden=true;$('desk').hidden=false;$('token').value='';
 for(const task of data.tasks||[]){const item=document.createElement('article');paragraph(item,[taskNames[task.type]||task.type,task.destination,task.origin,task.dates,task.note,date(task.dueAt)].filter(Boolean).join(' · '));const button=document.createElement('button');button.textContent='Открыть карточку';button.addEventListener('click',()=>openRequest(task.requestId));item.append(button);$('tasks').append(item);}
 if(!(data.tasks||[]).length)paragraph($('tasks'),'На сегодня задач нет.');say('Очередь обновлена.');
 }catch(e){if(run===generation)say(e.message);}finally{if(run===generation)$('refresh').disabled=false;}
}
$('connect').addEventListener('click',async()=>{token=$('token').value.trim();if(!token){say('Введите ключ доступа.');return;}$('connect').disabled=true;try{await refresh();}finally{$('connect').disabled=false;}});
$('refresh').addEventListener('click',refresh);$('logout').addEventListener('click',logout);
window.addEventListener('pagehide',logout);

async function saveEntry(kind){
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
