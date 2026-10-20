import { api, db, fetch } from 'sdk';
import { handle } from './service.js';
import { findFlights } from './flights.js';
export const process = (kind,input,ctx) => handle({api,db,findFlights:data => findFlights(fetch,data)},kind,input,ctx);
