/** Body-free forwarding. Bounded in-memory frames; no replay, cache, log or persistence. */
import {record} from './index.ts';
import {RELAY_URL,relayGenerationId,relayUsage,type RelayObservation,type RelayRequest,type RelayUsage} from './relay.ts';
export type RelayForwardOptions={
  request:RelayRequest;request_id:string;server_request_id:string;secret:string;timeout_ms:number;
  signal?:AbortSignal;fetcher?:(url:string,init:RequestInit)=>Promise<Response>;
  finish:(observation:RelayObservation)=>Promise<unknown>;waitUntil?:(promise:Promise<unknown>)=>void;
  checkpoint?:(generation_id:string)=>Promise<unknown>;
};
const MAX_FRAME=131072,MAX_RESPONSE=4*1024*1024;
class ForwardError extends Error {code:RelayObservation['failure_code'];constructor(code:RelayObservation['failure_code']){super(code??'upstream_invalid');this.code=code;}}
export async function forwardRelay(options:RelayForwardOptions):Promise<Response>{
  const {request}=options,abort=new AbortController();let timedOut=false,ended=false;
  let generation_id:string|null=null,returned_model:string|null=null,returned_provider:string|null=null,usage:RelayUsage|null=null,checkpointed:string|null=null;
  let wake:()=>void=()=>{};const lifetime=new Promise<void>(resolve=>{wake=resolve;});options.waitUntil?.(lifetime);
  const timer=setTimeout(()=>{timedOut=true;abort.abort();void finish('timeout','relay_timeout');},options.timeout_ms);
  const clientAbort=()=>{abort.abort();void finish('cancelled','client_cancelled');};options.signal?.addEventListener('abort',clientAbort,{once:true});
  if(options.signal?.aborted)abort.abort();
  const headers={'Cache-Control':'private, no-store','MyHermes-Request-Id':options.request_id,'MyHermes-Server-Request-Id':options.server_request_id};
  async function finish(state:RelayObservation['state'],failure_code:RelayObservation['failure_code']){
    if(ended)return;ended=true;clearTimeout(timer);options.signal?.removeEventListener('abort',clientAbort);
    try{await options.finish({state,failure_code,generation_id,returned_model,returned_provider,usage:state==='completed'?usage:null});}catch{/* The existing reservation remains unknown; never log storage exceptions. */}finally{wake();}
  }
  async function checkpoint(){if(generation_id!==null&&generation_id!==checkpointed){await options.checkpoint?.(generation_id);if(abort.signal.aborted)throw new ForwardError('stream_interrupted');checkpointed=generation_id;}}
  async function inspect(value:unknown){
    const obj=record(value);
    if(obj.error!==undefined)throw new ForwardError('upstream_rejected');
    if(obj.id!==undefined){const id=relayGenerationId(obj.id);if(!id||(generation_id!==null&&generation_id!==id))throw new ForwardError('upstream_invalid');generation_id=id;}
    if(obj.model!==undefined){if(typeof obj.model!=='string'||!request.model_policy.response_models.includes(obj.model))throw new ForwardError('upstream_policy_mismatch');returned_model=obj.model;}
    if(obj.provider!==undefined){if(typeof obj.provider!=='string'||!request.model_policy.response_providers.includes(obj.provider))throw new ForwardError('upstream_policy_mismatch');returned_provider=obj.provider;}
    if(!Array.isArray(obj.choices))throw new ForwardError('upstream_invalid');
    if(obj.usage!==undefined&&obj.usage!==null){const incoming=relayUsage(obj.usage);if(usage&&JSON.stringify(usage)!==JSON.stringify(incoming))throw new ForwardError('upstream_invalid');usage=incoming;}
    await checkpoint();return obj;
  }
  function failure(error:unknown):{state:RelayObservation['state'];code:RelayObservation['failure_code']} {
    if(timedOut)return {state:'timeout',code:'relay_timeout'};
    if(options.signal?.aborted)return {state:'cancelled',code:'client_cancelled'};
    return {state:'failed',code:error instanceof ForwardError?error.code:'upstream_unavailable'};
  }
  let response:Response;
  try{
    if(abort.signal.aborted)throw new ForwardError('client_cancelled');
    // The Workers native fetch needs its global receiver; do not detach it via ?? .
    const fetcher=options.fetcher??((url:string,init:RequestInit)=>fetch(url,init));
    // workerd rejects redirect:'error'; manual plus !ok rejects every 3xx
    // without forwarding the company Authorization header to another origin.
    response=await fetcher(RELAY_URL,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+options.secret},body:JSON.stringify(request.upstream),signal:abort.signal,redirect:'manual'});
    generation_id=relayGenerationId(response.headers.get('X-Generation-Id'));
    if(abort.signal.aborted)throw new ForwardError('stream_interrupted');
    await checkpoint();
    if(!response.ok){await response.body?.cancel();throw new ForwardError('upstream_rejected');}
    if(!response.body)throw new ForwardError('upstream_invalid');
  }catch(error){const f=failure(error);await finish(f.state,f.code);return Response.json({error:{code:f.code,message:'The relay could not complete this request. Do not automatically retry with a new request ID.'}},{status:f.state==='timeout'?504:502,headers});}
  const reader=response.body.getReader();
  if(!request.stream){
    try{
      if(response.headers.get('Content-Type')?.split(';')[0].trim()!=='application/json')throw new ForwardError('upstream_invalid');
      const chunks:Uint8Array[]=[];let length=0;
      for(;;){if(abort.signal.aborted)throw new ForwardError('stream_interrupted');const result=await reader.read();if(abort.signal.aborted)throw new ForwardError('stream_interrupted');if(result.done)break;length+=result.value.byteLength;if(length>MAX_RESPONSE)throw new ForwardError('upstream_invalid');chunks.push(result.value);}
      const data=new Uint8Array(length);let offset=0;for(const c of chunks){data.set(c,offset);offset+=c.length;}
      const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(data);await inspect(JSON.parse(text));
      await finish('completed',null);
      return new Response(data,{headers:{...headers,'Content-Type':'application/json'}});
    }catch(error){abort.abort();await reader.cancel().catch(()=>{});const f=failure(error);await finish(f.state,f.code);return Response.json({error:{code:f.code,message:'The relay could not complete this request. Do not automatically retry with a new request ID.'}},{status:f.state==='timeout'?504:502,headers});}
  }
  if(response.headers.get('Content-Type')?.split(';')[0].trim()!=='text/event-stream'){
    abort.abort();await reader.cancel().catch(()=>{});await finish('failed','upstream_invalid');return Response.json({error:{code:'upstream_invalid',message:'Invalid upstream stream.'}},{status:502,headers});
  }
  const decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}),encoder=new TextEncoder();let buffer='',total=0,doneSeen=false;
  // One outstanding read and one bounded output chunk. No tee or detached drain.
  const stream=new ReadableStream<Uint8Array>({
    async pull(controller){
      try{
        let output='';
        while(!output){
          if(abort.signal.aborted)throw new ForwardError('stream_interrupted');
          const next=await reader.read();
          if(abort.signal.aborted)throw new ForwardError('stream_interrupted');
          if(next.done){if(!doneSeen)throw new ForwardError('stream_interrupted');await finish('completed',null);controller.close();return;}
          total+=next.value.byteLength;if(total>MAX_RESPONSE||next.value.byteLength>MAX_FRAME)throw new ForwardError('upstream_invalid');
          buffer+=decoder.decode(next.value,{stream:true});if(buffer.length>MAX_FRAME)throw new ForwardError('upstream_invalid');
          let match:RegExpExecArray|null;
          while((match=/\r\n\r\n|\n\n|\r\r/.exec(buffer))){
            const frame=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
            const data=frame.split(/\r\n|\n|\r/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
            if(!data){output+=': keepalive\n\n';continue;}
            if(data==='[DONE]'){doneSeen=true;output+='data: [DONE]\n\n';if(buffer.trim())throw new ForwardError('upstream_invalid');break;}
            if(doneSeen)throw new ForwardError('upstream_invalid');
            await inspect(JSON.parse(data));output+='data: '+data+'\n\n';
          }
        }
        if(doneSeen){
          // Completion and its observed usage win before asynchronous transport
          // cleanup. A stalled cancel must not reclassify a completed response.
          await finish('completed',null);abort.abort();void reader.cancel().catch(()=>{});
          controller.enqueue(encoder.encode(output));controller.close();return;
        }
        controller.enqueue(encoder.encode(output));
      }catch(error){abort.abort();await reader.cancel().catch(()=>{});const f=failure(error);await finish(f.state,f.code);controller.enqueue(encoder.encode('data: '+JSON.stringify({error:{code:f.code,message:'Relay stream interrupted; do not automatically retry.'},choices:[{index:0,delta:{},finish_reason:'error'}]})+'\n\n'));controller.close();}
    },
    async cancel(){abort.abort();await reader.cancel().catch(()=>{});await finish(timedOut?'timeout':'cancelled',timedOut?'relay_timeout':'client_cancelled');}
  },{highWaterMark:0});
  return new Response(stream,{headers:{...headers,'Content-Type':'text/event-stream','X-Accel-Buffering':'no'}});
}
