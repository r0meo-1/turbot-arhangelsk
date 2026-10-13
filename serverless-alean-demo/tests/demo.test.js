import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {search,validate} from '../tgcloud/lib/aleanSearch.js';
import {empty,reduce,render} from '../tgcloud/lib/aleanDialog.js';
import {handle} from '../tgcloud/lib/aleanService.js';
import {CONFIG,assertDemo} from '../tgcloud/lib/aleanConfig.js';
const now=new Date('2030-01-01T12:00:00Z');
const query=()=>({kind:'tour',destination:'Сочи',arrival:'2030-02-01',departure:'2030-02-08',adults:2,children_ages:[7],budget:150000,origin:'Москва'});
const answers=['/alean','tour','Сочи','2030-02-01','2030-02-08','2','7','150000','Москва'];
function fixture() {
 const sqlite=new DatabaseSync(':memory:');sqlite.exec('CREATE TABLE alean_readonly_demo_sessions(chat_id INTEGER PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,expires INTEGER NOT NULL)');
 const db={get:async(sql,params={})=>sqlite.prepare(sql).get(params),run:async(sql,params={})=>({rowsAffected:Number(sqlite.prepare(sql).run(params).changes)})};
 const sent=[];let failure=false;
 const api={sendMessage:async data=>{if(failure){failure=false;throw new Error('synthetic send failure');}sent.push(data);},answerCallbackQuery:async()=>{}};
 let id=100;const user=990001;
 const message=(text,extra={})=>handle({db,api,now:()=>now},'message',{message_id:id,chat:{id:user,type:'private'},from:{id:user},text,...extra},{update:{update_id:id++}});
 const callback=async action=>{const row=await db.get('SELECT version FROM alean_readonly_demo_sessions WHERE chat_id=:id',{':id':user});return handle({db,api,now:()=>now},'callback',{id:'fake',from:{id:user},data:`a:${row.version}:${action}`,message:{chat:{id:user,type:'private'}}},{update:{update_id:id++}});};
 return {sqlite,db,api,sent,message,callback,user,getId:()=>id,setFailure:()=>{failure=true}};
}
test('configuration is immutable and all real side effects are disabled',()=>{
 assertDemo();assert.equal(CONFIG.ALEAN_DEMO_ONLY,true);for(const [k,v] of Object.entries(CONFIG))if(k!=='ALEAN_DEMO_ONLY')assert.equal(v,false);
 assert.throws(()=>{CONFIG.supplierCalls=true;});
});
test('mock inventory supports hotels/tours, budgets and all provider states',()=>{
 for(const kind of ['hotel','tour']){const r=search({...query(),kind},'success',now);assert.equal(r.offers.length,3);assert.equal(r.booking_enabled,false);for(const o of r.offers){assert.equal(o.mocked,true);assert.equal(o.confirmed,false);assert.equal(o.booking_url,null);}}
 assert.equal(search({...query(),budget:1},'success',now).state,'empty');
 for(const s of ['empty','timeout','error'])assert.equal(search(query(),s,now).state,s);
});
test('dates, party and budget validation reject invalid values',()=>{
 for(const [key,value] of [['adults',true],['adults',0],['children_ages',[18]],['children_ages',[true]],['arrival','2030-02-30'],['departure','2030-01-01'],['destination','Турция'],['budget',-1]])assert.throws(()=>validate({...query(),[key]:value},now));
});
test('pure /alean route renders demo rather than legacy /start fallback',()=>{
 let s=empty();for(const text of answers)s=reduce(s,{text},now).state;
 assert.match(render(s,9).text,/проверьте параметры/);s=reduce(s,{action:'search'},now).state;assert.match(render(s,10).text,/вымышленный/);
 s=reduce(s,{action:'edit'},now).state;assert.equal(s.data.destination,'Сочи');s=reduce(s,{action:'kind-hotel'},now).state;s=reduce(s,{action:'adler'},now).state;assert.equal(s.data.destination,'Адлер');
 assert.deepEqual(reduce(s,{text:'/cancel'},now).state.data,{});
});
test('real SQLite service complete flow, Back, retry, Cancel, no lead tables',async()=>{
 const f=fixture();try{for(const text of answers)await f.message(text);await f.callback('back');assert.match(f.sent.at(-1).text,/Москва/);await f.callback('keep');await f.callback('search');assert.match(f.sent.at(-1).text,/вымышленный/);
 for(const scenario of ['empty','timeout','error']){await f.callback('scenario-'+scenario);await f.callback('search');assert.match(f.sent.at(-1).text,new RegExp(scenario==='empty'?'не найдено':scenario==='timeout'?'истекло':'недоступен'));}
 await f.callback('scenario-success');await f.callback('search');assert.match(f.sent.at(-1).text,/вымышленный/);await f.message('/cancel');assert.match(f.sent.at(-1).text,/Ничего не отправлено/);
 assert.deepEqual(f.sqlite.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(r=>r.name),['alean_readonly_demo_sessions']);assert.ok(f.sent.every(r=>r.chat_id===f.user));
 }finally{f.sqlite.close();}
});
test('contacts and legacy Mini App submissions never enter drafts',async()=>{
 const f=fixture();try{await f.message('',{contact:{phone_number:'FICTIONAL_PHONE'}});await f.message('',{web_app_data:{data:'{"type":"trip_request"}'}});assert.equal(f.sqlite.prepare('SELECT count(*) AS n FROM alean_readonly_demo_sessions').get().n,0);assert.ok(f.sent.every(r=>/отключены/.test(r.text)));assert.ok(!JSON.stringify(f.sent).includes('FICTIONAL_PHONE'));}finally{f.sqlite.close();}
});
test('legacy completion/flight buttons and stale buttons are blocked',async()=>{
 const f=fixture();try{await f.message('/alean');for(const data of ['d:100:finish','d:100:flights','c:confirm:legacy'])await handle({db:f.db,api:f.api,now:()=>now},'callback',{id:'fake',from:{id:f.user},data,message:{chat:{id:f.user,type:'private'}}},{update:{update_id:999}});assert.ok(f.sent.slice(1).every(r=>/устарела/.test(r.text)));assert.deepEqual(JSON.parse(f.sqlite.prepare('SELECT payload FROM alean_readonly_demo_sessions').get().payload).data,{});}finally{f.sqlite.close();}
});
test('duplicate deliveries do not repeat state transitions',async()=>{
 const f=fixture();try{await f.message('/alean');const n=f.sent.length;await handle({db:f.db,api:f.api,now:()=>now},'message',{message_id:100,chat:{id:f.user,type:'private'},from:{id:f.user},text:'/alean'},{update:{update_id:100}});assert.equal(f.sent.length,n);}finally{f.sqlite.close();}
});
test('pending prompt can recover after send failure without advancing twice',async()=>{
 const f=fixture();try{f.setFailure();await assert.rejects(f.message('/alean'));await handle({db:f.db,api:f.api,now:()=>now},'message',{message_id:100,chat:{id:f.user,type:'private'},from:{id:f.user},text:'/alean'},{update:{update_id:100}});assert.match(f.sent.at(-1).text,/Что ищем/);assert.equal(JSON.parse(f.sqlite.prepare('SELECT payload FROM alean_readonly_demo_sessions').get().payload).index,0);}finally{f.sqlite.close();}
});
