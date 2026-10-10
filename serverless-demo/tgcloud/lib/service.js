import { empty, reduce, render } from './dialog.js';
import { moscowToday } from './validation.js';

// SDK raw SQL supports named bound parameters. Each CAS is one atomic statement;
// do not use a JS mutex or BEGIN/COMMIT across independent SDK calls.
export async function handle(deps, kind, input, ctx) {
  const {db,api,findFlights} = deps;
  const message = kind === 'callback' ? input.message : input;
  if (!message || message.chat?.type !== 'private' || message.chat.id !== input.from?.id) {
    if (kind === 'callback') await api.answerCallbackQuery({callback_query_id:input.id,text:'Откройте личный чат с ботом.'});
    return;
  }
  const chatId = message.chat.id;
  const updateId = ctx?.update?.update_id;
  if (!Number.isSafeInteger(updateId)) throw new Error('Missing update_id');
  const now = deps.now ? deps.now() : new Date();
  const ts = Math.floor(now.getTime()/1000);
  if (kind === 'callback') {
    try { await api.answerCallbackQuery({callback_query_id:input.id}); } catch { /* old Telegram callback may have expired */ }
  }
  // Opportunistic expiry. There is no undocumented scheduler in the runtime.
  await db.run('DELETE FROM aprel_demo_sessions WHERE expires < :now',{':now':ts});
  const callback = kind === 'callback' ? /^d:(\d+):([a-z0-9-]+)$/.exec(input.data || '') : null;
  for (let attempt = 0; attempt < 5; attempt++) {
    const row = await db.get('SELECT version, payload FROM aprel_demo_sessions WHERE chat_id = :id',{':id':chatId});
    const current = row ? JSON.parse(row.payload) : empty();
    // Replaying a delivered update never advances the dialog or re-runs Tutu.
    // If Telegram delivery failed after the save, retry the current prompt.
    if (current.seen.includes(updateId) || (kind === 'message' && message.message_id <= (current.lastMessage || 0))) {
      if (current.pending && row.version === updateId) {
        if (current.deliveryUntil > ts) throw new Error('Prompt delivery in progress; retry later');
        const resumed = {...current,deliveryUntil:ts+60};
        const claimed = await db.run('UPDATE aprel_demo_sessions SET payload=:payload WHERE chat_id=:id AND version=:version AND payload=:previous', {
          ':payload':JSON.stringify(resumed),':id':chatId,':version':row.version,':previous':row.payload,
        });
        if (!claimed.rowsAffected) throw new Error('Prompt delivery claimed; retry later');
        await deliver(db,api,chatId,row.version,resumed,current.notice || '');
      }
      return;
    }
    if (kind === 'callback' && (!callback || !row || Number(callback[1]) !== row.version)) {
      await api.sendMessage({chat_id:chatId,text:'Эта кнопка устарела. Используйте последние кнопки или /start.'});
      return;
    }
    // Telegram contacts must belong to the sender; never persist contact payloads.
    if (message.contact && message.contact.user_id !== input.from.id) {
      await api.sendMessage({chat_id:chatId,text:'Используйте тестовый номер +79001234567, не чужой контакт.'}); return;
    }
    const event = kind === 'callback' ? {action:callback[2]} : {text:message.contact?.phone_number || message.text || ''};
    const outcome = reduce(current,event,moscowToday(now));
    const next = outcome.state;
    next.seen = [...current.seen,updateId].slice(-64);
    next.lastMessage = kind === 'message' ? message.message_id : current.lastMessage || 0;
    next.pending = true;
    next.deliveryUntil = ts+60;
    next.notice = outcome.notice;
    const params = {':id':chatId,':version':updateId,':payload':JSON.stringify(next),':expires':ts+86400};
    let saved;
    if (row) {
      params[':previous'] = row.version;
      saved = await db.run('UPDATE aprel_demo_sessions SET version=:version, payload=:payload, expires=:expires WHERE chat_id=:id AND version=:previous',params);
    } else {
      saved = await db.run('INSERT INTO aprel_demo_sessions (chat_id,version,payload,expires) VALUES (:id,:version,:payload,:expires) ON CONFLICT(chat_id) DO NOTHING',params);
    }
    if (!saved.rowsAffected) continue;
    await deliver(db,api,chatId,updateId,next,outcome.effect ? 'Ищем рейсы Tutu…' : outcome.notice);
    if (outcome.effect === 'flights') {
      const text = await findFlights(next.data);
      // A cancel/restart while HTTP is in flight must not leak an obsolete reply.
      const latest = await db.get('SELECT version, payload FROM aprel_demo_sessions WHERE chat_id=:id',{':id':chatId});
      if (latest?.version === updateId) await api.sendMessage({chat_id:chatId,text});
    }
    return;
  }
  throw new Error('Concurrent session update; retry delivery');
}
async function deliver(db,api,chatId,version,state,notice) {
  try {
    await api.sendMessage({chat_id:chatId,...render(state,version,notice)});
  } catch (error) {
    await db.run('UPDATE aprel_demo_sessions SET payload=:payload WHERE chat_id=:id AND version=:version',{
      ':id':chatId,':version':version,':payload':JSON.stringify({...state,deliveryUntil:0}),
    });
    throw error;
  }
  await markDelivered(db,chatId,version,state);
}
async function markDelivered(db,chatId,version,state) {
  const delivered = {...state,pending:false,notice:''};
  await db.run('UPDATE aprel_demo_sessions SET payload=:payload WHERE chat_id=:id AND version=:version',{
    ':id':chatId,':version':version,':payload':JSON.stringify(delivered),
  });
}
