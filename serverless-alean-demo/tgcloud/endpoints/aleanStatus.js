import {api,db} from 'sdk';
import {CONFIG,assertDemo} from '../lib/aleanConfig.js';
import {REVISION} from '../lib/aleanRelease.js';
import {search} from '../lib/aleanSearch.js';
import {empty,reduce,render} from '../lib/aleanDialog.js';
export default async function() {
  assertDemo();const bot=await api.getMe();
  if(bot.id!==8886586430||bot.username!=='apreltour_bot')throw new Error('Wrong demo bot');
  const database=await db.get('SELECT 1 AS ok');if(database.ok!==1)throw new Error('Database not ready');
  // Check schema readiness without fetching any user's draft or writing rows.
  await db.get('SELECT version FROM alean_readonly_demo_sessions WHERE 0');
  const now=new Date('2030-01-01T00:00:00Z');
  let s=empty();
  for(const text of ['/alean','tour','Сочи','2030-02-01','2030-02-08','2','7','150000','Москва'])s=reduce(s,{text},now).state;
  if(!render(s,1).text.includes('проверьте параметры'))throw new Error('Handler routing check failed');
  s=reduce(s,{action:'search'},now).state;
  if(!render(s,2).text.includes('вымышленный'))throw new Error('Demo result check failed');
  for(const scenario of ['empty','timeout','error']) {
    const r=search(s.data,scenario,now);if(r.state!==scenario)throw new Error('Failure scenario check failed');
  }
  const cancelled=reduce(s,{text:'/cancel'},now).state;
  if(Object.keys(cancelled.data).length)throw new Error('Cancel check failed');
  const legacy=reduce(empty(),{action:'finish'},now);
  if(legacy.state.step==='done')throw new Error('Legacy completion enabled');
  return {health:'ok',bot:bot.username,botId:bot.id,revision:REVISION,config:CONFIG,
    handlerCheck:'passed',searchCheck:'passed',failureCheck:'passed',cancelCheck:'passed',
    sentMessages:0,databaseWrites:0};
}
