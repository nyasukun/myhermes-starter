/** Fixed-origin usage lookup. This module never requests generation content. */
import {record,ContractError} from './index.ts';
import {relayGenerationId,relayInteger,relayUsage,type RelayUsage} from './relay.ts';
export const RELAY_GENERATION_URL='https://openrouter.ai/api/v1/generation';
export const RELAY_RECONCILE_MIN_AGE_MS=600_000;
export const RELAY_MANIFEST={
  version:'1.4.0',source:'server_observed',body_storage:false,
  persisted_fields:['cursor','person_id','installation_id','request_id','server_request_id','request_hash (internal HMAC only; never projected)','alias','model','generation_model (immutable admission policy identity)','provider','generation_id','returned_model','returned_provider','day','created_at','updated_at','reservation_nano','cost_nano','prompt_tokens','completion_tokens','total_tokens','state','usage_state','failure_code','finish_value (internal exact metadata receipt)','usage_source','reconciled_at','reconcile_value (internal exact metadata receipt)','retire_after_ms (internal known-row retention deadline)'],
  settings:['frozen (admission stops after observed cost exceeds reservation)','clock_floor_ms (monotonic admission/retention clock)','history_complete_since (timestamp after newest deleted request start; null before any purge)'],
  usage_sources:['response','generation'],unknown_usage:'Reservation remains outstanding; absent cost is never zero.',
  reconciliation:{endpoint:RELAY_GENERATION_URL,minimum_age_ms:RELAY_RECONCILE_MIN_AGE_MS,timeout_ms:10000,max_response_bytes:32768,requires:['recorded generation ID','matching original model/provider','terminal finish reason','explicit total_cost','native token counts'],client_values_allowed:false,manual_zero_or_unfreeze:false},
  concurrency:{policy_field:'max_concurrent_requests',default:8,minimum:1,maximum:128,scope:'all owners, installations, models and days in the company ledger',active_state:'registered',admission:'count and insert in one transaction after retained duplicate/reuse lookup',release_states:['completed','failed','cancelled','timeout'],missing_finish:'indefinite; restart, checkpoint, reconciliation and retention do not release a slot',summary_fields:['active_requests','max_concurrent_requests'],storage:'derived from execution state; no new request content or stored counter'},
  retention:{known_ms:90*86400000,known_clock:'terminal execution only: last settlement or outcome update; additionally after embedded ID admission expires',registered:'indefinite even when accounting is known; no slot release by retention',unknown:'indefinite; reservation and frozen state are never cleared by retention',max_records:100000,batch_size:100,admission:{id_version:7,max_age_ms:30*86400000,future_tolerance_ms:300000,legacy:'retained receipts only; absent non-v7 IDs rejected'},idempotency:'Existing receipts checked first; purged IDs cannot be admitted again; persistent clock floor prevents rollback replay; capacity rejects new inference.',coverage:'summary history_complete_since identifies retention-limited history'},
  viewers:['authenticated owner for own receipts','browser administrators for metadata only'],
} as const;
export type GenerationExpectation={generation_id:string;model:string;generation_model?:string;provider:string;returned_model:string|null;returned_provider:string|null;created_at:string};
export type RelayReconciliation={generation_id:string;returned_model:string;returned_provider:string;usage:RelayUsage};
export function validateGenerationMetadata(value:unknown,expected:GenerationExpectation,now=Date.now()):RelayReconciliation {
  const data=record(record(value).data),created=typeof data.created_at==='string'?Date.parse(data.created_at):NaN,start=Date.parse(expected.created_at);
  if(!relayGenerationId(expected.generation_id)||data.id!==expected.generation_id||!Number.isFinite(created)||!Number.isFinite(start)
    ||now-start<RELAY_RECONCILE_MIN_AGE_MS||now-created<RELAY_RECONCILE_MIN_AGE_MS||Math.abs(created-start)>300_000)throw new ContractError('relay_generation_incomplete');
  if(typeof data.finish_reason!=='string'||!['stop','length','tool_calls','content_filter','error'].includes(data.finish_reason)||typeof data.cancelled!=='boolean')throw new ContractError('relay_generation_incomplete');
  if(typeof data.model!=='string'||![expected.model,expected.returned_model,expected.generation_model??expected.model].includes(data.model)
    ||typeof data.provider_name!=='string'||(data.provider_name!==expected.returned_provider&&data.provider_name.toLowerCase()!==expected.provider.toLowerCase()))throw new ContractError('relay_generation_mismatch');
  const prompt=relayInteger(data.native_tokens_prompt,0,100_000_000),completion=relayInteger(data.native_tokens_completion,0,100_000_000);
  const usage=relayUsage({prompt_tokens:prompt,completion_tokens:completion,total_tokens:prompt+completion,cost:data.total_cost});
  if(!usage)throw new ContractError('relay_generation_incomplete');
  return {generation_id:expected.generation_id,returned_model:data.model,returned_provider:data.provider_name,usage};
}
export async function lookupGeneration(expected:GenerationExpectation,secret:string,options:{now?:number;timeout_ms?:number;fetcher?:(url:string,init:RequestInit)=>Promise<Response>}={}):Promise<RelayReconciliation>{
  const now=options.now??Date.now();
  if(!relayGenerationId(expected.generation_id)||!Number.isFinite(Date.parse(expected.created_at))||now-Date.parse(expected.created_at)<RELAY_RECONCILE_MIN_AGE_MS)throw new ContractError('relay_generation_incomplete');
  const abort=new AbortController(),timeout=options.timeout_ms??10000;
  relayInteger(timeout,1,10000);
  let timer:ReturnType<typeof setTimeout>|undefined;
  const timed=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{abort.abort();reject(new ContractError('relay_generation_unavailable'));},timeout);});
  async function read(){
    const fetcher=options.fetcher??((url:string,init:RequestInit)=>fetch(url,init));
    const response=await fetcher(RELAY_GENERATION_URL+'?id='+encodeURIComponent(expected.generation_id),{method:'GET',headers:{Accept:'application/json',Authorization:'Bearer '+secret},redirect:'manual',signal:abort.signal});
    if(!response.ok||response.headers.get('Content-Type')?.split(';')[0].trim()!=='application/json'||!response.body){await response.body?.cancel();throw new ContractError('relay_generation_unavailable');}
    const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
    try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>32768){await reader.cancel();throw new ContractError('relay_generation_unavailable');}chunks.push(value);}}
    finally{reader.releaseLock();}
    const raw=new Uint8Array(size);let offset=0;for(const chunk of chunks){raw.set(chunk,offset);offset+=chunk.length;}
    return validateGenerationMetadata(JSON.parse(new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw)),expected,now);
  }
  try{return await Promise.race([read(),timed]);}catch(error){if(error instanceof ContractError&&['relay_generation_incomplete','relay_generation_mismatch'].includes(error.message))throw error;throw new ContractError('relay_generation_unavailable');}
  finally{if(timer!==undefined)clearTimeout(timer);abort.abort();}
}
