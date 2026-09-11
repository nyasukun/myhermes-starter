/** Synthetic standard protobuf wire fixtures. Actual Python SDK interoperability is
 * separately checked by the cross-language scenario; no user data appears here. */
export const join=(...parts:Uint8Array[])=>Uint8Array.from(parts.flatMap(p=>Array.from(p)));
export function varint(value:bigint|number){let n=BigInt(value),out:number[]=[];do{let b=Number(n&127n);n>>=7n;if(n)b|=128;out.push(b);}while(n);return Uint8Array.from(out);}
export function field(n:number,value:Uint8Array|number|bigint,wire=typeof value==='number'||typeof value==='bigint'?0:2){const tag=varint((n<<3)|wire);if(wire===0)return join(tag,varint(value as number));const b=value as Uint8Array;return join(tag,...(wire===2?[varint(b.length)]:[]),b);}
export const str=(s:string)=>new TextEncoder().encode(s);
export const fixed=(v:bigint,bytes=8)=>Uint8Array.from(Array.from({length:bytes},(_,i)=>Number((v>>BigInt(i*8))&255n)));
export function kv(key:string,value:string|number){return join(field(1,str(key)),field(2,typeof value==='string'?field(1,str(value)):field(3,value)));}
export function protobuf({now=Date.now(),eventId=crypto.randomUUID(),extraAttributes={},extraSpan=new Uint8Array(),resourceExtra=new Uint8Array(),name='myhermes.activity',statusMessage='',traceId='11'.repeat(16),spanId='22'.repeat(8)}:{now?:number;eventId?:string;extraAttributes?:Record<string,string|number>;extraSpan?:Uint8Array;resourceExtra?:Uint8Array;name?:string;statusMessage?:string;traceId?:string;spanId?:string}={}){
  const attrs={'myhermes.manifest_version':'1.0.0','myhermes.event_id':eventId,'myhermes.sequence':1,'myhermes.client_time':new Date(now).toISOString(),'myhermes.kind':'runtime_start','myhermes.outcome':'ok',...extraAttributes};
  const span=join(field(1,Uint8Array.from(Buffer.from(traceId,'hex'))),field(2,Uint8Array.from(Buffer.from(spanId,'hex'))),field(5,str(name)),field(6,1),field(7,fixed(BigInt(now)*1000000n),1),field(8,fixed(BigInt(now)*1000000n+5000000n),1),...Object.entries(attrs).map(([k,v])=>field(9,kv(k,v))),field(15,join(field(2,str(statusMessage)),field(3,1))),field(16,fixed(257n,4),5),extraSpan);
  const resource=join(field(1,kv('service.name','myhermes')),field(1,kv('service.version','0.3.0')),resourceExtra);
  const scope=join(field(1,str('myhermes')),field(2,str('0.3.0')));
  return field(1,join(field(1,resource),field(2,join(field(1,scope),field(2,span)))));
}
