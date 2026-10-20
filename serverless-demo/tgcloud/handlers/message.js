import { process } from '../lib/runtime.js';
export default async function (message,ctx) { await process('message',message,ctx); }
