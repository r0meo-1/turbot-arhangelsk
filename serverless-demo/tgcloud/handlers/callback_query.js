import { process } from '../lib/runtime.js';
export default async function (query,ctx) { await process('callback',query,ctx); }
