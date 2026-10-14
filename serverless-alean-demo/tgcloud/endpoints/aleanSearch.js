import {EndpointError} from 'sdk';
import {assertDemo} from '../lib/aleanConfig.js';
import {search} from '../lib/aleanSearch.js';
export default function(input) {
  assertDemo();
  if(input?.mode && input.mode!=='demo')throw new EndpointError('Живой поиск отключён на демо-инстансе.',{code:'DEMO_ONLY'});
  try {return search(input?.query,input?.scenario||'success');}
  catch {throw new EndpointError('Проверьте направление, даты, состав туристов и бюджет.',{code:'VALIDATION'});}
}
