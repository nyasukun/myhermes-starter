/** Public text/tool relay contract. No company policy or secret belongs here. */
import {boundedString, canonicalJSON, ContractError, exact, record, UUID} from './index.ts';

export const RELAY_URL = 'https://openrouter.ai/api/v1/chat/completions';
export const RELAY_MAX_REQUEST_BYTES = 262_144;
export type RelayModelPolicy = {
  model: string; provider: string; response_models: readonly string[]; response_providers: readonly string[];
  context_tokens: number; prompt_nano_per_token: number; completion_nano_per_token: number;
  default_output_tokens: number; max_output_tokens: number;
  output_parameter?: 'max_tokens'|'max_completion_tokens';
  generation_model?: string;
};
export type RelayPolicy = {version: string; daily_budget_nano: number; timeout_ms: number; aliases: Record<string, RelayModelPolicy>};
export type RelayRequest = {alias: string; model_policy: RelayModelPolicy; stream: boolean; upstream: Record<string, unknown>; reservation_nano: number};
export type RelayUsage = {prompt_tokens: number; completion_tokens: number; total_tokens: number; cost_nano: number};
export type RelayObservation = {
  state: 'completed'|'failed'|'cancelled'|'timeout';
  failure_code: null|'upstream_rejected'|'upstream_unavailable'|'upstream_invalid'|'upstream_policy_mismatch'|'stream_interrupted'|'client_cancelled'|'relay_timeout';
  generation_id: string|null; returned_model: string|null; returned_provider: string|null; usage: RelayUsage|null;
};
export function relayInteger(value: unknown, min: number, max: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < min || Number(value) > max) throw new ContractError('relay_invalid_integer');
  return Number(value);
}
function finite(value: unknown, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) throw new ContractError('relay_invalid_number');
  return value;
}
function name(value: unknown): string {const s=boundedString(value,64);if(!/^[a-zA-Z0-9_-]+$/.test(s))throw new ContractError('relay_invalid_name');return s;}
function content(value: unknown): unknown {
  if (typeof value === 'string') return boundedString(value,131072,0);
  if (!Array.isArray(value)||value.length<1||value.length>32) throw new ContractError('relay_text_required');
  return value.map(part=>{const p=exact(part,['type','text']);if(p.type!=='text')throw new ContractError('relay_text_required');return {type:'text',text:boundedString(p.text,131072,0)};});
}
function schema(value: unknown, depth=0): void {
  if(depth>16)throw new ContractError('relay_schema_depth');
  if(typeof value==='string'){boundedString(value,16384,0);return;}
  if(typeof value==='number'){finite(value,-Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER);return;}
  if(value===null||typeof value==='boolean')return;
  if(Array.isArray(value)){if(value.length>256)throw new ContractError('relay_schema_size');for(const v of value)schema(v,depth+1);return;}
  const obj=record(value);if(Object.keys(obj).length>256)throw new ContractError('relay_schema_size');
  for(const [key,v] of Object.entries(obj)){boundedString(key,256);if(['$ref','$dynamicRef','$recursiveRef'].includes(key)&& (typeof v!=='string'||!v.startsWith('#/')))throw new ContractError('relay_remote_schema');schema(v,depth+1);}
}
function messages(value: unknown): unknown[] {
  if(!Array.isArray(value)||value.length<1||value.length>256)throw new ContractError('relay_invalid_messages');
  return value.map(value=>{
    const m=record(value);
    if(typeof m.role==='string'&&['system','developer','user'].includes(m.role)){exact(m,['role','content','name'],['role','content']);return {...m,content:content(m.content),...(m.name===undefined?{}:{name:name(m.name)})};}
    if(m.role==='tool'){exact(m,['role','content','tool_call_id']);return {...m,content:content(m.content),tool_call_id:boundedString(m.tool_call_id,128)};}
    if(m.role==='assistant'){
      exact(m,['role','content','tool_calls','name'],['role']);
      if(m.content===undefined&&m.tool_calls===undefined)throw new ContractError('relay_invalid_assistant');
      const out:Record<string,unknown>={role:'assistant'};
      if(m.content!==undefined)out.content=m.content===null?null:content(m.content);
      if(m.name!==undefined)out.name=name(m.name);
      if(m.tool_calls!==undefined){
        if(!Array.isArray(m.tool_calls)||m.tool_calls.length<1||m.tool_calls.length>32)throw new ContractError('relay_invalid_tool_calls');
        out.tool_calls=m.tool_calls.map(v=>{const t=exact(v,['id','type','function']);if(t.type!=='function')throw new ContractError('relay_function_required');const f=exact(t.function,['name','arguments']);return {id:boundedString(t.id,128),type:'function',function:{name:name(f.name),arguments:boundedString(f.arguments,65536,0)}};});
      }
      return out;
    }
    throw new ContractError('relay_invalid_role');
  });
}
export function validateRelayPolicy(policy: RelayPolicy): void {
  boundedString(policy.version,64);relayInteger(policy.daily_budget_nano,1,1_000_000_000_000);relayInteger(policy.timeout_ms,100,300000);
  if(Object.keys(policy.aliases).length<1||Object.keys(policy.aliases).length>16)throw new ContractError('relay_invalid_policy');
  for(const [alias,p] of Object.entries(policy.aliases)){
    name(alias);boundedString(p.model,128);boundedString(p.provider,64);
    if(!p.response_models.includes(p.model)||!p.response_providers.length)throw new ContractError('relay_invalid_policy');
    if(p.generation_model!==undefined&&(typeof p.generation_model!=='string'||!p.response_models.includes(p.generation_model)))throw new ContractError('relay_invalid_policy');
    for(const label of [...p.response_models,...p.response_providers])boundedString(label,128);
    relayInteger(p.context_tokens,1,10_000_000);relayInteger(p.prompt_nano_per_token,1,1_000_000);relayInteger(p.completion_nano_per_token,1,1_000_000);
    relayInteger(p.max_output_tokens,1,32768);relayInteger(p.default_output_tokens,1,p.max_output_tokens);
    if(p.output_parameter!==undefined&&p.output_parameter!=='max_tokens'&&p.output_parameter!=='max_completion_tokens')throw new ContractError('relay_invalid_policy');
    relayInteger(p.context_tokens*p.prompt_nano_per_token+p.max_output_tokens*p.completion_nano_per_token,1,Number.MAX_SAFE_INTEGER);
  }
}
export function parseRelayRequest(value: unknown, policy: RelayPolicy): RelayRequest {
  validateRelayPolicy(policy);
  const o=exact(value,['model','messages','stream','max_tokens','temperature','top_p','seed','stop','tools','tool_choice','parallel_tool_calls','response_format','stream_options','reasoning_effort'],['model','messages']);
  const alias=boundedString(o.model,64);
  if(!Object.hasOwn(policy.aliases,alias))throw new ContractError('relay_model_not_allowed');
  const p=policy.aliases[alias];
  if(o.stream!==undefined&&typeof o.stream!=='boolean')throw new ContractError('relay_invalid_stream');
  const stream=o.stream===true;
  const max_tokens=o.max_tokens===undefined?p.default_output_tokens:relayInteger(o.max_tokens,1,p.max_output_tokens);
  const upstream:Record<string,unknown>={model:p.model,messages:messages(o.messages),stream,[p.output_parameter??'max_tokens']:max_tokens};
  if(o.temperature!==undefined)upstream.temperature=finite(o.temperature,0,2);
  if(o.top_p!==undefined)upstream.top_p=finite(o.top_p,0,1);
  if(o.seed!==undefined)upstream.seed=relayInteger(o.seed,0,2147483647);
  if(o.stop!==undefined){const stops=Array.isArray(o.stop)?o.stop:[o.stop];if(stops.length<1||stops.length>4)throw new ContractError('relay_invalid_stop');upstream.stop=stops.map(s=>boundedString(s,128));}
  const toolNames=new Set<string>();
  if(o.tools!==undefined){
    if(!Array.isArray(o.tools)||o.tools.length<1||o.tools.length>64)throw new ContractError('relay_invalid_tools');
    upstream.tools=o.tools.map(t=>{const tool=exact(t,['type','function']);if(tool.type!=='function')throw new ContractError('relay_function_required');const f=exact(tool.function,['name','description','parameters','strict'],['name']);const n=name(f.name);if(toolNames.has(n))throw new ContractError('relay_duplicate_tool');toolNames.add(n);const result:Record<string,unknown>={name:n};if(f.description!==undefined)result.description=boundedString(f.description,16384,0);if(f.parameters!==undefined){record(f.parameters);schema(f.parameters);result.parameters=f.parameters;}if(f.strict!==undefined){if(typeof f.strict!=='boolean')throw new ContractError('relay_invalid_strict');result.strict=f.strict;}return {type:'function',function:result};});
  }
  if(o.tool_choice!==undefined){
    if(!toolNames.size)throw new ContractError('relay_tools_required');
    if(typeof o.tool_choice==='string'&&['auto','none','required'].includes(o.tool_choice))upstream.tool_choice=o.tool_choice;
    else{const t=exact(o.tool_choice,['type','function']);const f=exact(t.function,['name']);if(t.type!=='function'||typeof f.name!=='string'||!toolNames.has(f.name))throw new ContractError('relay_invalid_tool_choice');upstream.tool_choice={type:'function',function:{name:f.name}};}
  }
  if(o.parallel_tool_calls!==undefined){if(typeof o.parallel_tool_calls!=='boolean'||!toolNames.size)throw new ContractError('relay_invalid_parallel_tools');upstream.parallel_tool_calls=o.parallel_tool_calls;}
  if(o.reasoning_effort!==undefined&&o.reasoning_effort!=='none')throw new ContractError('relay_reasoning_not_supported');
  if(o.response_format!==undefined){
    const f=record(o.response_format);
    if(f.type==='json_schema'){
      exact(f,['type','json_schema']);const j=exact(f.json_schema,['name','description','schema','strict'],['name','schema']);
      const result:Record<string,unknown>={name:name(j.name)};record(j.schema);schema(j.schema);result.schema=j.schema;
      if(j.description!==undefined)result.description=boundedString(j.description,16384,0);
      if(j.strict!==undefined){if(typeof j.strict!=='boolean')throw new ContractError('relay_invalid_strict');result.strict=j.strict;}
      upstream.response_format={type:'json_schema',json_schema:result};
    }else{exact(f,['type']);if(typeof f.type!=='string'||!['text','json_object'].includes(f.type))throw new ContractError('relay_response_format_not_supported');upstream.response_format=f;}
  }
  if(o.stream_options!==undefined){const s=exact(o.stream_options,['include_usage']);if(s.include_usage!==true||!stream)throw new ContractError('relay_invalid_stream_options');}
  upstream.provider={only:[p.provider],order:[p.provider],allow_fallbacks:false,require_parameters:true,data_collection:'deny',zdr:true,max_price:{prompt:p.prompt_nano_per_token/1000,completion:p.completion_nano_per_token/1000,request:0}};
  if(new TextEncoder().encode(canonicalJSON(upstream)).length>RELAY_MAX_REQUEST_BYTES)throw new ContractError('relay_request_too_large');
  return {alias,model_policy:p,stream,upstream,reservation_nano:p.context_tokens*p.prompt_nano_per_token+max_tokens*p.completion_nano_per_token};
}
export function relayRequestId(value: unknown): string {if(typeof value!=='string'||!UUID.test(value))throw new ContractError('relay_request_id_required');return value.toLowerCase();}
export const RELAY_REQUEST_MAX_AGE_MS=30*86400000;
export const RELAY_REQUEST_FUTURE_MS=300000;
export const RELAY_KNOWN_RETENTION_MS=90*86400000;
/** RFC 9562 UUIDv7. Random tail; no host identifier or monotonic ordering claim. */
export function createRelayRequestId(now=Date.now()):string {
  relayInteger(now,0,2**48-1);
  const bytes=crypto.getRandomValues(new Uint8Array(16));let timestamp=now;
  for(let i=5;i>=0;i--){bytes[i]=timestamp%256;timestamp=Math.floor(timestamp/256);}
  bytes[6]=(bytes[6]&15)|0x70;bytes[8]=(bytes[8]&63)|0x80;
  const hex=[...bytes].map(byte=>byte.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
export function relayRequestBirth(value:unknown):number|null {
  const id=relayRequestId(value);return id[14]==='7'?Number.parseInt(id.replaceAll('-','').slice(0,12),16):null;
}
export function relayRequestAdmissible(value:unknown,now=Date.now()):boolean {
  relayInteger(now,0,8640000000000000);const birth=relayRequestBirth(value);
  return birth!==null&&birth>=now-RELAY_REQUEST_MAX_AGE_MS&&birth<=now+RELAY_REQUEST_FUTURE_MS;
}
/** Internal deduplication only. Never return this fingerprint in a user/admin receipt. */
export async function relayRequestFingerprint(secret:string,person_id:string,request_id:string,value:unknown):Promise<string>{
  boundedString(secret,4096,32);boundedString(person_id,128);const id=relayRequestId(request_id);
  const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign']);
  const input=canonicalJSON(['myhermes.relay.request.v1',person_id,id,value]);
  return [...new Uint8Array(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(input)))].map(v=>v.toString(16).padStart(2,'0')).join('');
}
export function relayDay(now: number): string {return new Date(now+9*60*60*1000).toISOString().slice(0,10);}
export function relayGenerationId(value: unknown): string|null {return typeof value==='string'&&/^[a-zA-Z0-9_-]{1,128}$/.test(value)?value:null;}
export function relayUsage(value: unknown): RelayUsage|null {
  try{const u=record(value);const prompt_tokens=relayInteger(u.prompt_tokens,0,100_000_000),completion_tokens=relayInteger(u.completion_tokens,0,100_000_000),total_tokens=relayInteger(u.total_tokens,0,200_000_000);if(total_tokens!==prompt_tokens+completion_tokens)return null;const cost=finite(u.cost,0,1_000_000);const cost_nano=Math.ceil(cost*1_000_000_000);if(!Number.isSafeInteger(cost_nano))return null;return {prompt_tokens,completion_tokens,total_tokens,cost_nano};}catch{return null;}
}
