import {api,db} from 'sdk';
import {handle} from './aleanService.js';
export const process=(kind,input,ctx)=>handle({api,db},kind,input,ctx);
