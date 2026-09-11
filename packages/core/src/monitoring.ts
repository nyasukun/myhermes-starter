/** Public collection contract. All free-form user/exception/tool content is excluded. */
import {canonicalJSON,ContractError,exact,UUID} from './index.ts';
export const MONITORING_VERSION='1.0.0';
export const MONITORING_LIMITS={body_bytes:262144,batch_events:100,offline_days:30,accepted_days:90,rejection_days:30,queue_events:10000} as const;
/** Read limits do not alter the immutable event/collection manifest. */
export const MONITORING_READ_LIMITS={page_bytes:1_048_576,page_records:100} as const;
export const ACTIVITY_KINDS=['enrollment','runtime_start','runtime_stop','runtime_update','connection_change','sync','skill_change','tool','model','delivery'] as const;
export const ACTIVITY_OUTCOMES=['ok','failed','conflict','cancelled','unknown'] as const;
export const TOOL_KINDS=['read','write','search','execute','connector','other'] as const;
export type ActivityKind=typeof ACTIVITY_KINDS[number];
export type ActivityAttributes={outcome:typeof ACTIVITY_OUTCOMES[number];duration_ms?:number;connection_id?:string;tool_kind?:typeof TOOL_KINDS[number];model_alias?:'economy';prompt_tokens?:number;completion_tokens?:number;cost_nano?:number;request_id?:string;sent_count?:number;dropped_count?:number};
export type AuditEvent={event_id:string;sequence:number;client_time:string;kind:ActivityKind;attributes:ActivityAttributes;trace_id?:string;span_id?:string};
export type AuditBatch={schema_version:'1';manifest_version:typeof MONITORING_VERSION;client_version:string;batch_id:string;stream_id:string;first_sequence:number;previous_batch_sha256:string|null;events:AuditEvent[]};
export type TelemetrySpan={trace_id:string;span_id:string;parent_span_id:string|null;start_time_unix_nano:string;end_time_unix_nano:string;status_code:number;client_version:string;event:AuditEvent};
export const MONITORING_MANIFEST={
  schema_version:'1',version:MONITORING_VERSION,
  destinations:{audit:'/v1/monitoring/audit',traces:'/v1/monitoring/traces',origin:'configured authenticated MyHermes origin only'},
  transport:{audit:'application/jose; ES256 compact JWS',traces:'OTLP/HTTP application/x-protobuf; uncompressed traces only',authentication:'installation-bound DPoP'},
  delivery:{sampling:1,batch_max_events:100,flush:'after command/session; offline batches retried on next managed command; no background host monitoring',maximum_offline_days:30,queue_max_events:10000},
  events:ACTIVITY_KINDS,required_attributes:['outcome'],optional_attributes:['duration_ms','connection_id','tool_kind','model_alias','prompt_tokens','completion_tokens','cost_nano','request_id','sent_count','dropped_count'],
  identifiers:['event_id','stream_id','sequence','batch_id','previous_batch_sha256','trace_id','span_id','parent_span_id','request_id'],
  otlp:{span_name:'myhermes.activity',scope_name:'myhermes',service_name:'myhermes',resource_attributes:['service.name','service.version'],span_attributes:['myhermes.manifest_version','myhermes.event_id','myhermes.sequence','myhermes.client_time','myhermes.kind','myhermes.outcome','myhermes.duration_ms','myhermes.connection_id','myhermes.tool_kind','myhermes.model_alias','myhermes.prompt_tokens','myhermes.completion_tokens','myhermes.cost_nano','myhermes.request_id','myhermes.sent_count','myhermes.dropped_count'],events:false,links:false,status_message:false},
  exclusions:['prompt','response','conversation','SOUL','memory','personal skill content','document/email content','search terms','file names/paths','commands','tool arguments/results','exception text','arbitrary span names','URLs','authentication headers','cookies','secrets'],
  retention_days:{accepted:90,rejected:30},viewers:['authenticated owner for own records','company administrators for metadata only'],
  trust:{audit:'client_reported_signed; possession and chain continuity, not device truth',traces:'client_reported; DPoP transport',relay:'server_observed; separate authoritative usage ledger'},
  stored_projections:{
    authenticated_subject:['person_id','installation_id'],
    audit_event:['cursor','event_id','batch_id','event','received_at'],
    otlp_span:['cursor','event_id','span','received_at'],
    span_fields:['trace_id','span_id','parent_span_id','start_time_unix_nano','end_time_unix_nano','status_code','client_version','event'],
    event_fields:['event_id','sequence','client_time','kind','attributes','trace_id','span_id'],
    audit_batch:['cursor','batch_id','stream_id','batch_sha256','batch','signature','received_at'],
    rejection:['cursor','transport','code','received_at'],
    stream_head:['stream_id','sequence','batch_sha256','received_at'],
    sql_indexes:['kind','outcome','request_id'],
    list_envelope:['person_id','installation_id','manifest_version','source','trust','records','next_before']
  },
  persisted_server_metadata:['authenticated person_id','authenticated installation_id','received_at','validation outcome','fixed rejection code','public normalized event/span fields','accepted batch hash/signature','request_id correlation'],
  infrastructure:'Application storage excludes IP and User-Agent. Cloudflare Access/edge may process operational network metadata; deployment must disclose its vendor retention. Worker automatic observability is disabled by default.',
  changes:'Any additional field, model alias, sampling or retention change requires a new public manifest/module version and reviewed private pin.'
} as const;
export function telemetryError():never{throw new ContractError('monitoring_invalid');}
export function telemetryId(value:unknown):string{if(typeof value!=='string'||!UUID.test(value))return telemetryError();return value.toLowerCase();}
export function telemetryInteger(value:unknown,max=Number.MAX_SAFE_INTEGER,min=0):number{if(typeof value!=='number'||!Number.isSafeInteger(value)||value<min||value>max)return telemetryError();return value;}
export function telemetryVersion(value:unknown):string{if(typeof value!=='string'||!/^\d{1,6}\.\d{1,6}\.\d{1,6}$/.test(value))return telemetryError();return value;}
export function telemetryHex(value:unknown,bytes:number):string{if(typeof value!=='string'||!(new RegExp(`^[a-f0-9]{${bytes*2}}$`)).test(value)||/^0+$/.test(value))return telemetryError();return value;}
export function telemetryTime(value:unknown,now=Date.now()):string{if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value))return telemetryError();const time=Date.parse(value);if(!Number.isFinite(time)||new Date(time).toISOString()!==value||time>now+300000||time<now-MONITORING_LIMITS.offline_days*86400000)throw new ContractError('monitoring_age');return value;}
export function parseActivityAttributes(value:unknown,kind:ActivityKind):ActivityAttributes{
  const a=exact(value,['outcome','duration_ms','connection_id','tool_kind','model_alias','prompt_tokens','completion_tokens','cost_nano','request_id','sent_count','dropped_count'],['outcome']);
  if(typeof a.outcome!=='string'||!(ACTIVITY_OUTCOMES as readonly string[]).includes(a.outcome))return telemetryError();
  if(a.duration_ms!==undefined)telemetryInteger(a.duration_ms,86400000);
  if(a.connection_id!==undefined)a.connection_id=telemetryId(a.connection_id);
  if(a.request_id!==undefined)a.request_id=telemetryId(a.request_id);
  if(a.tool_kind!==undefined&&(kind!=='tool'||typeof a.tool_kind!=='string'||!(TOOL_KINDS as readonly string[]).includes(a.tool_kind)))return telemetryError();
  if(a.model_alias!==undefined&&(kind!=='model'||a.model_alias!=='economy'))return telemetryError();
  for(const k of ['prompt_tokens','completion_tokens','cost_nano'])if(a[k]!==undefined){if(kind!=='model')return telemetryError();telemetryInteger(a[k],k==='cost_nano'?1e12:100000000);}
  for(const k of ['sent_count','dropped_count'])if(a[k]!==undefined){if(kind!=='delivery')return telemetryError();telemetryInteger(a[k],100000000);}
  return structuredClone(a) as ActivityAttributes;
}
export function parseAuditEvent(value:unknown,now=Date.now()):AuditEvent{
  const e=exact(value,['event_id','sequence','client_time','kind','attributes','trace_id','span_id'],['event_id','sequence','client_time','kind','attributes']);
  if(typeof e.kind!=='string'||!(ACTIVITY_KINDS as readonly string[]).includes(e.kind))return telemetryError();
  if((e.trace_id===undefined)!==(e.span_id===undefined))return telemetryError();
  return {event_id:telemetryId(e.event_id),sequence:telemetryInteger(e.sequence,Number.MAX_SAFE_INTEGER,1),client_time:telemetryTime(e.client_time,now),kind:e.kind as ActivityKind,attributes:parseActivityAttributes(e.attributes,e.kind as ActivityKind),...(e.trace_id===undefined?{}:{trace_id:telemetryHex(e.trace_id,16),span_id:telemetryHex(e.span_id,8)})};
}
export function parseAuditBatch(value:unknown,now=Date.now()):AuditBatch{
  const b=exact(value,['schema_version','manifest_version','client_version','batch_id','stream_id','first_sequence','previous_batch_sha256','events']);
  if(b.schema_version!=='1'||b.manifest_version!==MONITORING_VERSION)throw new ContractError('monitoring_version');
  const first=telemetryInteger(b.first_sequence,Number.MAX_SAFE_INTEGER-100,1);
  if(!Array.isArray(b.events)||b.events.length<1||b.events.length>MONITORING_LIMITS.batch_events)return telemetryError();
  const events=b.events.map(e=>parseAuditEvent(e,now)),ids=new Set<string>();
  for(let i=0;i<events.length;i++){if(events[i].sequence!==first+i||ids.has(events[i].event_id))return telemetryError();ids.add(events[i].event_id);}
  if(b.previous_batch_sha256!==null)telemetryHex(b.previous_batch_sha256,32);
  return {schema_version:'1',manifest_version:MONITORING_VERSION,client_version:telemetryVersion(b.client_version),batch_id:telemetryId(b.batch_id),stream_id:telemetryId(b.stream_id),first_sequence:first,previous_batch_sha256:b.previous_batch_sha256 as string|null,events};
}
export async function monitoringHash(value:unknown):Promise<string>{const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalJSON(value)));return Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,'0')).join('');}
