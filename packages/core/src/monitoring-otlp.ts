/** Narrow, fail-closed decoder for the standard OTLP ExportTraceServiceRequest.
 * Wire field numbers: opentelemetry-proto trace/v1/trace.proto, common/v1/common.proto,
 * resource/v1/resource.proto, collector/trace/v1/trace_service.proto (OTLP 1.11).
 * Unsupported fields are rejected, not silently retained or reflected in errors.
 */
import {ContractError} from './index.ts';
import {MONITORING_LIMITS,MONITORING_VERSION,parseAuditEvent,telemetryError,telemetryHex,telemetryTime,telemetryVersion,type TelemetrySpan} from './monitoring.ts';
type Field={wire:number;value:Uint8Array|bigint};
type Message=Map<number,Field[]>;
function decode(bytes:Uint8Array,allowed:Record<number,number>,repeated:number[]=[]):Message{
  let pos=0,fields=0;const result:Message=new Map();
  const varint=()=>{let n=0n;for(let i=0;i<10;i++){if(pos>=bytes.length)return telemetryError();const b=bytes[pos++];if(i===9&&b>1)return telemetryError();n|=BigInt(b&127)<<BigInt(i*7);if(!(b&128)){if(i&&b===0)return telemetryError();return n;}}return telemetryError();};
  while(pos<bytes.length){
    if(++fields>1000)return telemetryError();
    const tag=varint();if(tag>0xffffffffn)return telemetryError();const field=Number(tag>>3n),wire=Number(tag&7n);
    if(!Object.hasOwn(allowed,field)||allowed[field]!==wire||(!repeated.includes(field)&&result.has(field)))return telemetryError();
    let value:Uint8Array|bigint;
    if(wire===0)value=varint();
    else {const length=wire===1?8:wire===5?4:Number(varint());if(!Number.isSafeInteger(length)||length<0||length>bytes.length-pos)return telemetryError();value=bytes.subarray(pos,pos+length);pos+=length;}
    const values=result.get(field)??[];values.push({wire,value});result.set(field,values);
  }
  return result;
}
function bytes(m:Message,n:number):Uint8Array {const f=m.get(n)?.[0];if(!f)return new Uint8Array();if(!(f.value instanceof Uint8Array))return telemetryError();return f.value;}
function text(m:Message,n:number):string{try{return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes(m,n));}catch{return telemetryError();}}
function number(m:Message,n:number):number{const f=m.get(n)?.[0];if(!f)return 0;if(typeof f.value!=='bigint'||f.value>BigInt(Number.MAX_SAFE_INTEGER))return telemetryError();return Number(f.value);}
function fixed(m:Message,n:number,len:number):bigint{const b=bytes(m,n);if(!b.length)return 0n;if(b.length!==len)return telemetryError();let v=0n;for(let i=len-1;i>=0;i--)v=(v<<8n)|BigInt(b[i]);return v;}
function hex(b:Uint8Array,length:number):string{if(b.length!==length)return telemetryError();return telemetryHex(Array.from(b,x=>x.toString(16).padStart(2,'0')).join(''),length);}
function zeroCounters(m:Message,fields:number[]){for(const n of fields)if(number(m,n)!==0)return telemetryError();}
function attrs(m:Message,field:number):Record<string,string|number|boolean>{
  const output:Record<string,string|number|boolean>=Object.create(null);const values=m.get(field)??[];if(values.length>24)return telemetryError();
  for(const f of values){if(!(f.value instanceof Uint8Array))return telemetryError();const kv=decode(f.value,{1:2,2:2}),key=text(kv,1);if(key.length<1||key.length>64||Object.hasOwn(output,key)||!kv.has(2))return telemetryError();
    const value=decode(bytes(kv,2),{1:2,2:0,3:0});if(value.size!==1)return telemetryError();
    if(value.has(1)){const s=text(value,1);if(s.length>128)return telemetryError();output[key]=s;}
    else if(value.has(2)){const b=number(value,2);if(b>1)return telemetryError();output[key]=!!b;}
    else output[key]=number(value,3);
  }
  return output;
}
function metadata(m:Message,n:number,allowed:Record<number,number>):Message{return decode(bytes(m,n),allowed);}
export function parseOTLP(bytesValue:Uint8Array,now=Date.now()):TelemetrySpan[]{
  if(!(bytesValue instanceof Uint8Array)||bytesValue.length>MONITORING_LIMITS.body_bytes)throw new ContractError('monitoring_size');
  const root=decode(bytesValue,{1:2},[1]),output:TelemetrySpan[]=[];
  for(const rf of root.get(1)??[]){
    const rs=decode(rf.value as Uint8Array,{1:2,2:2,3:2},[2]);if(text(rs,3)!=='')return telemetryError();
    const resource=decode(bytes(rs,1),{1:2,2:0},[1]);zeroCounters(resource,[2]);const ra=attrs(resource,1);
    if(Object.keys(ra).length!==2||ra['service.name']!=='myhermes')return telemetryError();const clientVersion=telemetryVersion(ra['service.version']);
    for(const sf of rs.get(2)??[]){
      const ss=decode(sf.value as Uint8Array,{1:2,2:2,3:2},[2]);if(text(ss,3)!=='')return telemetryError();
      const scope=metadata(ss,1,{1:2,2:2,4:0});if(text(scope,1)!=='myhermes'||text(scope,2)!==clientVersion)return telemetryError();zeroCounters(scope,[4]);
      for(const sp of ss.get(2)??[]){
        if(output.length>=MONITORING_LIMITS.batch_events)return telemetryError();
        // Events (11), links (13), and all future/unknown fields are excluded.
        const s=decode(sp.value as Uint8Array,{1:2,2:2,3:2,4:2,5:2,6:0,7:1,8:1,9:2,10:0,12:0,14:0,15:2,16:5},[9]);
        if(text(s,3)!==''||text(s,5)!=='myhermes.activity'||number(s,6)!==1)return telemetryError();zeroCounters(s,[10,12,14]);
        const flags=fixed(s,16,4);if(flags>1023n)return telemetryError();
        const start=fixed(s,7,8),end=fixed(s,8,8);if(start===0n||end<start||end-start>86400000000000n)return telemetryError();
        try{telemetryTime(new Date(Number(start/1000000n)).toISOString(),now);telemetryTime(new Date(Number(end/1000000n)).toISOString(),now);}catch{throw new ContractError('monitoring_age');}
        const status=metadata(s,15,{2:2,3:0});if(text(status,2)!==''||number(status,3)>2)return telemetryError();
        const a=attrs(s,9),eventData:Record<string,unknown>={},attributes:Record<string,unknown>={};
        if(a['myhermes.manifest_version']!==MONITORING_VERSION)throw new ContractError('monitoring_version');
        for(const [key,value] of Object.entries(a)){
          if(key==='myhermes.manifest_version')continue;
          if(!key.startsWith('myhermes.'))return telemetryError();const name=key.slice(9);
          if(['event_id','sequence','client_time','kind'].includes(name))eventData[name]=value;else attributes[name]=value;
        }
        const traceId=hex(bytes(s,1),16),spanId=hex(bytes(s,2),8);
        const event=parseAuditEvent({...eventData,attributes,trace_id:traceId,span_id:spanId},now);
        output.push({trace_id:traceId,span_id:spanId,parent_span_id:bytes(s,4).length?hex(bytes(s,4),8):null,start_time_unix_nano:String(start),end_time_unix_nano:String(end),status_code:number(status,3),client_version:clientVersion,event});
      }
    }
  }
  if(!output.length)return telemetryError();const ids=new Set<string>();for(const s of output){const id=s.trace_id+':'+s.span_id;if(ids.has(id))return telemetryError();ids.add(id);}return output;
}
