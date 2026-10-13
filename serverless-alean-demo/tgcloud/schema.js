import { table, integer, text } from 'sdk/db';

// Demo drafts only: no production leads, credentials, names or raw phones.
export const demoSessions = table('aprel_demo_sessions', {
  chatId: integer('chat_id').primaryKey(),
  version: integer('version').notNull(),
  payload: text('payload').notNull(),
  expires: integer('expires').notNull(),
});

// Separate, expiring Alean demo drafts; never contacts, users or CRM leads.
export const aleanDemoSessions = table('alean_readonly_demo_sessions', {
  chatId: integer('chat_id').primaryKey(),
  version: integer('version').notNull(),
  payload: text('payload').notNull(),
  expires: integer('expires').notNull(),
});
