import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {parseRelayRequest,createRelayRequestId,relayUsage,relayDay,relayRequestFingerprint,PublicRelayLedger,forwardRelay,RELAY_URL,type RelayPolicy,type RelayObservation} from '../src/index.ts';
const policy:RelayPolicy={version:'fixture',daily_budget_nano:10000,timeout_ms:1000,aliases:{economy:{model:'fixture/model',provider:'fixture',response_models:['fixture/model'],response_providers:['Fixture'],context_tokens:100,prompt_nano_per_token:10,completion_nano_per_token:20,default_output_tokens:10,max_output_tokens:20}}};
const input=(more:Record<string,unknown>={})=>({model:'economy',messages:[{role:'user',content:'sensitive prompt'}],...more});
const usage={prompt_tokens:10,completion_tokens:2,total_tokens:12,cost:0.0000002};
const response=(more:Record<string,unknown>={})=>({id:'gen_fixture',model:'fixture/model',provider:'Fixture',object:'chat.completion',choices:[{index:0,message:{role:'assistant',content:'private response'},finish_reason:'stop'}],usage,...more});
function ledger(){const db=new DatabaseSync(':memory:');return new PublicRelayLedger({exec(sql,...args){const statement=db.prepare(sql);return statement.columns().length?statement.all(...args) as Record<string,unknown>[]:(statement.run(...args),[]);}},fn=>{db.exec('BEGIN');try{const result=fn();db.exec('COMMIT');return result;}catch(error){db.exec('ROLLBACK');throw error;}});}
const registration=(more={},now=Date.now())=>({person_id:'alice',installation_id:'device',request_id:createRelayRequestId(now),request_hash:'a'.repeat(64),alias:'economy',model:'fixture/model',provider:'fixture',reservation_nano:1200,...more});
const observation=(more:Partial<RelayObservation>={}):RelayObservation=>({state:'completed',failure_code:null,generation_id:'gen_fixture',returned_model:'fixture/model',returned_provider:'Fixture',usage:relayUsage(usage),...more});

test('validated SSE completion settles and reaches the client even when upstream cancellation stalls',async()=>{
  let release:()=>void=()=>{};const cancelled=new Promise<void>(resolve=>{release=resolve;});
  const observations:RelayObservation[]=[];
  const upstream=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify(response())+'\n\ndata: [DONE]\n\n'));},cancel(){return cancelled;}});
  const result=await forwardRelay({request:parseRelayRequest(input({stream:true}),policy),request_id:'fixture',server_request_id:'fixture',secret:'fixture',timeout_ms:10,fetcher:async()=>new Response(upstream,{headers:{'Content-Type':'text/event-stream'}}),finish:async value=>{observations.push(value);}});
  let timeout:ReturnType<typeof setTimeout>|undefined;
  try{
    const text=await Promise.race([result.text(),new Promise<never>((_,reject)=>{timeout=setTimeout(()=>reject(new Error('completed_stream_waited_for_cancel')),80);})]);
    assert.match(text,/\[DONE\]/);assert.equal(observations.length,1);assert.equal(observations[0].state,'completed');assert.equal(observations[0].usage?.cost_nano,relayUsage(usage)!.cost_nano);
  }finally{clearTimeout(timeout);release();}
});
test('strict policy parser accepts text/function transcript, forbids routing and excess content',()=>{
  const valid=parseRelayRequest(input({stream:true,stream_options:{include_usage:true},tools:[{type:'function',function:{name:'find_issue',parameters:{type:'object',properties:{id:{type:'integer'}}}}}],tool_choice:{type:'function',function:{name:'find_issue'}}}),policy);
  assert.equal(valid.upstream.model,'fixture/model');assert.equal(valid.reservation_nano,1200);
  assert.deepEqual(valid.upstream.provider,{only:['fixture'],order:['fixture'],allow_fallbacks:false,require_parameters:true,data_collection:'deny',zdr:true,max_price:{prompt:.01,completion:.02,request:0}});
  assert.equal(valid.upstream.stream_options,undefined);
  for(const more of [{model:'fixture/model'},{provider:{}},{plugins:[]},{models:[]},{user:'secret'},{max_tokens:21},{n:2},{stream_options:{include_usage:false}},{messages:[{role:'user',content:[{type:'image_url',image_url:{url:'https://example.com'}}]}]},{tools:[{type:'function',function:{name:'tool',parameters:{$ref:'https://example.com/schema'}}}]}])assert.throws(()=>parseRelayRequest(input(more),policy));
  assert.doesNotThrow(()=>parseRelayRequest(input({messages:[{role:'assistant',content:null,tool_calls:[{id:'call1',type:'function',function:{name:'tool',arguments:'{"x":1}'}}]},{role:'tool',tool_call_id:'call1',content:'private tool result'}]}),policy));
  for(const more of [{messages:[{role:['user'],content:'x'}]},{response_format:{type:['text']}},{tools:[{type:'function',function:{name:'tool'}}],tool_choice:['auto']},{tools:[{type:'function',function:{name:'tool'}}],tool_choice:{type:'function',function:{name:['tool']}}}])assert.throws(()=>parseRelayRequest(input(more),policy));
});
test('usage absence is unknown; explicit zero is known; JST rolls at UTC15',()=>{
  assert.equal(relayUsage({prompt_tokens:1,completion_tokens:2,total_tokens:3}),null);
  assert.equal(relayUsage({...usage,cost:-1}),null);assert.equal(relayUsage({...usage,total_tokens:11}),null);
  assert.equal(relayUsage({...usage,cost:0})?.cost_nano,0);assert.equal(relayDay(Date.parse('2026-09-10T15:00:00Z')),'2026-09-11');
});
test('company policy maps the bounded client token limit to one supported upstream parameter',()=>{
  const azurePolicy:RelayPolicy={...policy,aliases:{economy:{...policy.aliases.economy,output_parameter:'max_completion_tokens'}}};
  const mapped=parseRelayRequest(input({max_tokens:12}),azurePolicy);
  assert.equal(mapped.upstream.max_completion_tokens,12);assert.equal(Object.hasOwn(mapped.upstream,'max_tokens'),false);
  assert.equal(mapped.reservation_nano,1240);
  assert.equal(parseRelayRequest(input(),azurePolicy).upstream.max_completion_tokens,10);
  assert.equal(parseRelayRequest(input({max_tokens:12}),policy).upstream.max_tokens,12);
  assert.throws(()=>parseRelayRequest(input({max_tokens:21}),azurePolicy));
  assert.throws(()=>parseRelayRequest(input({max_completion_tokens:12}),azurePolicy));
  assert.throws(()=>parseRelayRequest(input(),{...policy,aliases:{economy:{...policy.aliases.economy,output_parameter:'other' as never}}}));
});
test('Hermes non-reasoning and structured output options remain bounded data',()=>{
  const format={type:'json_schema',json_schema:{name:'skill_summary',description:'Synthetic structured reply',schema:{type:'object',properties:{summary:{type:'string'}},required:['summary'],additionalProperties:false},strict:true}};
  const parsed=parseRelayRequest(input({reasoning_effort:'none',response_format:format}),policy);
  assert.deepEqual(parsed.upstream.response_format,format);assert.equal(Object.hasOwn(parsed.upstream,'reasoning_effort'),false);
  for(const effort of ['low','minimal','high',null,{},['none']])assert.throws(()=>parseRelayRequest(input({reasoning_effort:effort}),policy));
  for(const change of [{json_schema:{name:'x',schema:{$ref:'https://fixture.invalid/private'}}},{json_schema:{name:'x',schema:{$dynamicRef:'https://fixture.invalid'}}},{json_schema:{name:'x',schema:{},strict:1}},{json_schema:{name:'x',schema:true}},{json_schema:{name:'x',schema:{},description:1}},{json_schema:{name:'x',schema:{},hidden:'body'}},{json_schema:{name:'with spaces',schema:{}}},{extra:'body'}]){
    assert.throws(()=>parseRelayRequest(input({response_format:{...format,...change}}),policy));
  }
  let deep:Record<string,unknown>={};for(let i=0;i<18;i++)deep={items:deep};
  assert.throws(()=>parseRelayRequest(input({response_format:{type:'json_schema',json_schema:{name:'x',schema:deep}}}),policy));
  assert.throws(()=>parseRelayRequest(input({response_format:{type:'text',json_schema:{name:'x',schema:{}}}}),policy));
});
test('request fingerprints are canonical, secret keyed and owner/request scoped',async()=>{
  const key='synthetic-fixture-secret-for-fingerprints',id=crypto.randomUUID();
  const fingerprint=await relayRequestFingerprint(key,'alice',id,{z:1,a:2});
  assert.match(fingerprint,/^[a-f0-9]{64}$/);
  assert.equal(fingerprint,await relayRequestFingerprint(key,'alice',id.toUpperCase(),{a:2,z:1}));
  for(const args of [[key+'-rotated','alice',id,{a:2,z:1}],[key,'bob',id,{a:2,z:1}],[key,'alice',crypto.randomUUID(),{a:2,z:1}],[key,'alice',id,{a:3,z:1}]] as const){
    assert.notEqual(fingerprint,await relayRequestFingerprint(...args));
  }
  const publicDigest=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode('{"a":2,"z":1}'))).toString('hex');
  assert.notEqual(fingerprint,publicDigest);
  await assert.rejects(relayRequestFingerprint('short','alice',id,{}));
  await assert.rejects(relayRequestFingerprint(key,'alice','not-a-uuid',{}));
});
test('internal fingerprints never escape receipts and key rotation cannot execute an existing ID twice',async()=>{
  const l=ledger(),key='synthetic-fixture-secret-for-fingerprints',id=createRelayRequestId();
  const fingerprint=await relayRequestFingerprint(key,'alice',id,input());
  const a=registration({request_id:id,request_hash:fingerprint});
  const accepted=l.register(a,2400),duplicate=l.register(a,2400);
  const rotated=l.register({...a,request_hash:await relayRequestFingerprint(key+'-rotated','alice',id,input())},2400);
  assert.equal(accepted.kind,'accepted');assert.equal(duplicate.kind,'duplicate');assert.equal(rotated.kind,'reused');
  assert.equal(accepted.receipt!.server_request_id,rotated.receipt!.server_request_id);
  assert.equal(l.list().requests.length,1);assert.equal(l.summary(relayDay(Date.now()),2400).outstanding_reservation_nano,1200);
  for(const projection of [accepted,duplicate,rotated,l.receipt('alice',id),l.list()]){
    assert.equal(JSON.stringify(projection).includes('request_hash'),false);
    assert.equal(JSON.stringify(projection).includes(fingerprint),false);
  }
});
test('ledger atomic receipts, exact settlement and persistent unknown day-crossing reservation',async()=>{
  const l=ledger(),start=Date.parse('2026-09-10T14:59:00Z'),a=registration({},start);
  const accepted=l.register(a,2400,start);assert.equal(accepted.kind,'accepted');
  assert.equal(l.register(a,2400,start).kind,'duplicate');assert.equal(l.register({...a,request_hash:'b'.repeat(64)},2400,start).kind,'reused');
  assert.equal(l.register(registration({},start),2400,start).kind,'accepted');assert.equal(l.register(registration({},start),2400,start).kind,'budget');
  assert.equal(l.summary('2026-09-11',2400).outstanding_reservation_nano,2400);
  assert.equal(l.register(registration({},start),2400,start+120000).kind,'budget');
  assert.equal(l.finish(accepted.receipt!.server_request_id,observation(),start+1000),true);
  assert.equal(l.finish(accepted.receipt!.server_request_id,observation(),start+1000),true);
  assert.equal(l.finish(accepted.receipt!.server_request_id,observation({usage:{...relayUsage(usage)!,cost_nano:0}}),start+1000),false);
  assert.equal(l.summary('2026-09-10',2400).known_cost_nano,200);assert.equal(l.summary('2026-09-11',2400).outstanding_reservation_nano,1200);
  assert.equal(l.register(registration({},start),2400,start+120000).kind,'accepted');
  assert.equal(l.receipt('bob',a.request_id),null);
  assert.equal(JSON.stringify(l.list()).includes('sensitive'),false);
  const secret='synthetic-fixture-secret-for-fingerprints',id=crypto.randomUUID();
  assert.equal(await relayRequestFingerprint(secret,'alice',id,{z:1,a:2}),await relayRequestFingerprint(secret,'alice',id,{a:2,z:1}));
  assert.throws(()=>l.register({...registration(),hidden:'private body'} as never,2400));
  assert.throws(()=>l.finish(accepted.receipt!.server_request_id,{...observation(),hidden:'private body'} as never));
});
test('ledger freezes admission on observed cost exceeding reservation',()=>{
  const l=ledger(),r=l.register(registration(),10000);l.finish(r.receipt!.server_request_id,observation({usage:{...relayUsage(usage)!,cost_nano:1201}}));assert.equal(l.summary(relayDay(Date.now()),10000).frozen,true);assert.equal(l.register(registration(),10000).kind,'frozen');
});
test('normal forward sends one fixed-host request and persists only projected usage',async()=>{
  const finished:RelayObservation[]=[],calls:unknown[]=[];
  const r=await forwardRelay({request:parseRelayRequest(input(),policy),request_id:'client',server_request_id:'server',secret:'fixture-secret',timeout_ms:1000,finish:async o=>{finished.push(o);},fetcher:async(url,init)=>{calls.push({url,init});return Response.json(response());}});
  assert.equal(r.status,200);assert.match(await r.text(),/private response/);assert.equal(calls.length,1);
  const call=calls[0] as {url:string;init:RequestInit};assert.equal(call.url,RELAY_URL);assert.equal(call.init.redirect,'manual');assert.equal((call.init.headers as Record<string,string>).Authorization,'Bearer fixture-secret');
  assert.deepEqual(finished,[observation()]);assert.equal(JSON.stringify(finished).includes('private response'),false);
});
test('split SSE handles comments, tool argument fragments, usage and DONE with bounded consumer reads',async()=>{
  let pulls=0;const finished:RelayObservation[]=[],encoder=new TextEncoder();
  const chunks=[': keepalive\r\n\r\n','data: '+JSON.stringify(response({object:'chat.completion.chunk',choices:[{index:0,delta:{tool_calls:[{index:0,id:'call1',type:'function',function:{name:'find_issue',arguments:'{"x":'}}]}}],usage:null}))+'\n\n','data: '+JSON.stringify(response({object:'chat.completion.chunk',choices:[{index:0,delta:{tool_calls:[{index:0,function:{arguments:'"😀"}'}}]},finish_reason:'tool_calls'}],usage:null}))+'\n\n','data: '+JSON.stringify(response({choices:[]}))+'\n\n','data: [DONE]\n\n'];
  const bytes=encoder.encode(chunks.join(''));let offset=0;
  const upstream=new ReadableStream({pull(c){pulls++;if(offset===bytes.length){c.close();return;}const end=Math.min(offset+17,bytes.length);c.enqueue(bytes.slice(offset,end));offset=end;}},{highWaterMark:0});
  const r=await forwardRelay({request:parseRelayRequest(input({stream:true}),policy),request_id:'client',server_request_id:'server',secret:'fixture',timeout_ms:1000,finish:async o=>{finished.push(o);},fetcher:async()=>new Response(upstream,{headers:{'Content-Type':'text/event-stream'}})});
  assert.equal(pulls,0);const text=await r.text();assert.match(text,/tool_calls/);assert.match(text,/😀/);assert.match(text,/\[DONE\]/);assert.equal(finished[0].usage?.cost_nano,200);assert.equal(finished.length,1);
});
test('missing usage and interrupted/cancelled/error/timeout forwarding keep usage unknown',async()=>{
  for(const scenario of ['missing','interrupted','error','cancel','timeout'] as const){
    const finished:RelayObservation[]=[];let upstreamAborted=false;
    const streaming=scenario!=='missing';const encoder=new TextEncoder();
    const r=await forwardRelay({request:parseRelayRequest(input({stream:streaming}),policy),request_id:'id',server_request_id:'sid',secret:'fixture',timeout_ms:scenario==='timeout'?15:1000,finish:async o=>{finished.push(o);},fetcher:async(_url,init)=>{
      init.signal?.addEventListener('abort',()=>{upstreamAborted=true;});
      if(scenario==='missing')return Response.json(response({usage:undefined}));
      if(scenario==='error')return new Response('secret upstream error',{status:500});
      if(scenario==='timeout')return new Promise((_resolve,reject)=>init.signal?.addEventListener('abort',()=>reject(new Error('secret timeout'))));
      return new Response(new ReadableStream({start(c){c.enqueue(encoder.encode('data: '+JSON.stringify(response({usage:null}))+'\n\n'));if(scenario==='interrupted')c.close();},cancel(){upstreamAborted=true;}},{highWaterMark:0}),{headers:{'Content-Type':'text/event-stream'}});
    }});
    if(scenario==='cancel')await r.body!.cancel();else assert.equal((await r.text()).includes('secret upstream error'),false);
    assert.equal(finished.length,1,scenario);assert.equal(finished[0].usage,null,scenario);if(['cancel','timeout'].includes(scenario))assert.equal(upstreamAborted,true);
    assert.equal(finished[0].state,scenario==='missing'?'completed':scenario==='timeout'?'timeout':scenario==='cancel'?'cancelled':'failed');
  }
});
test('midstream errors and policy mismatches are sanitized without usage settlement',async()=>{
  for(const payload of [{error:{message:'sensitive provider payload',metadata:{raw_error:'secret'}}},response({provider:'Unapproved',usage})]){
    const observations:RelayObservation[]=[];const r=await forwardRelay({request:parseRelayRequest(input({stream:true}),policy),request_id:'id',server_request_id:'sid',secret:'fixture',timeout_ms:1000,finish:async o=>{observations.push(o);},fetcher:async()=>new Response('data: '+JSON.stringify(payload)+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}})});
    const text=await r.text();assert.equal(text.includes('sensitive provider payload'),false);assert.equal(text.includes('Unapproved'),false);assert.equal(observations[0].usage,null);
  }
});
