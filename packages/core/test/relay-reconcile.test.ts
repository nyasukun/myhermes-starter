import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {PublicRelayLedger,createRelayRequestId,validateGenerationMetadata,lookupGeneration,RELAY_MANIFEST,forwardRelay,parseRelayRequest,type RelayObservation,type RelayPolicy} from '../src/index.ts';
const started=Date.parse('2026-09-11T00:00:00Z'),now=started+601_000;
const expected={generation_id:'gen-fixture-reconcile',model:'fixture/model',provider:'fixture',returned_model:null,returned_provider:null,created_at:new Date(started).toISOString()};
const metadata=(changes:Record<string,unknown>={})=>({data:{id:expected.generation_id,model:expected.model,provider_name:'Fixture',created_at:expected.created_at,finish_reason:'stop',cancelled:false,native_tokens_prompt:10,native_tokens_completion:2,total_cost:0.00000005,...changes}});
const value=()=>validateGenerationMetadata(metadata(),expected,now);
const registration=()=>({person_id:'alice',installation_id:'fixture-device',request_id:createRelayRequestId(started),request_hash:'a'.repeat(64),alias:'economy',model:expected.model,provider:expected.provider,reservation_nano:1000});
function fixture(){const db=new DatabaseSync(':memory:');const open=()=>new PublicRelayLedger({exec(sql,...args){const s=db.prepare(sql);return s.columns().length?s.all(...args) as Record<string,unknown>[]:(s.run(...args),[]);}},fn=>{db.exec('BEGIN');try{const r=fn();db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}});return {db,open,ledger:open()};}
const observation=(changes:Partial<RelayObservation>={}):RelayObservation=>({state:'completed',failure_code:null,generation_id:expected.generation_id,returned_model:expected.model,returned_provider:'Fixture',usage:null,...changes});

test('generation reconciliation requires aged terminal evidence, matching identity and explicit usage',()=>{
  assert.equal(value().usage.cost_nano,50);assert.equal(validateGenerationMetadata(metadata({total_cost:0}),expected,now).usage.cost_nano,0);
  assert.deepEqual(validateGenerationMetadata(metadata({private_content:'PRIVATE_SENTINEL',user_agent:'PRIVATE_SENTINEL'}),expected,now),value());
  assert.throws(()=>validateGenerationMetadata(metadata(),expected,started+599999));
  for(const change of [{finish_reason:null},{finish_reason:['stop']},{cancelled:'false'},{total_cost:undefined},{total_cost:-1},{total_cost:'0'},{native_tokens_prompt:null},{native_tokens_completion:undefined},{native_tokens_prompt:true},{created_at:new Date(now-1000).toISOString()},{created_at:new Date(started-400000).toISOString()},{id:'gen-other'},{model:'other/model'},{provider_name:'Unapproved'}])assert.throws(()=>validateGenerationMetadata(metadata(change),expected,now));
});

test('reconciliation accepts only the canonical model captured at admission, including late response finish',()=>{
  const canonical='fixture/model-2026-01-01',input={...registration(),generation_model:canonical};
  const f=fixture(),row=f.ledger.register(input,1000,started).receipt!;
  assert.equal(row.generation_model,canonical);f.ledger.checkpoint(row.server_request_id,expected.generation_id,started+1);
  const evidence=metadata({model:canonical});
  assert.throws(()=>validateGenerationMetadata(evidence,expected,now),/relay_generation_mismatch/);
  const result=validateGenerationMetadata(evidence,{...expected,generation_model:canonical},now);
  assert.equal(f.ledger.reconcile(row.server_request_id,result,now).kind,'settled');
  assert.equal(f.ledger.finish(row.server_request_id,observation({usage:result.usage}),now+1),true);
  assert.equal(f.ledger.receipt('alice',input.request_id)!.state,'completed');
  assert.equal(f.ledger.summary(row.day,1000).known_cost_nano,result.usage.cost_nano);
  assert.equal(f.ledger.register({...input,generation_model:'fixture/model-new'},1000,now+2).kind,'duplicate');
  assert.equal(f.open().receipt('alice',input.request_id)!.generation_model,canonical);
  assert.equal(f.ledger.reconcile(row.server_request_id,{...result,returned_model:'fixture/model-new'},now+3).kind,'conflict');
  const original=f.db.prepare('SELECT request_hash,finish_value FROM relay_requests').get();
  f.db.exec('ALTER TABLE relay_requests DROP COLUMN generation_model');
  const legacy=f.open().receipt('alice',input.request_id)!;
  assert.equal(legacy.generation_model,input.model);assert.deepEqual(f.db.prepare('SELECT request_hash,finish_value FROM relay_requests').get(),original);
  const policy:RelayPolicy={version:'fixture',daily_budget_nano:10000,timeout_ms:1000,aliases:{economy:{model:expected.model,generation_model:canonical,provider:expected.provider,response_models:[expected.model,canonical],response_providers:['Fixture'],context_tokens:100,prompt_nano_per_token:10,completion_nano_per_token:20,default_output_tokens:10,max_output_tokens:20}}};
  assert.equal(parseRelayRequest({model:'economy',messages:[{role:'user',content:'test'}]},policy).model_policy.generation_model,canonical);
  policy.aliases.economy.generation_model='fixture/unapproved';assert.throws(()=>parseRelayRequest({model:'economy',messages:[{role:'user',content:'test'}]},policy),/relay_invalid_policy/);
});

test('fixed metadata GET does not follow redirects, collect extra fields, infer zero or expose errors',async()=>{
  const calls:{url:string;init:RequestInit}[]=[];
  const result=await lookupGeneration(expected,'fixture-secret',{now,fetcher:async(url,init)=>{calls.push({url,init});return Response.json(metadata({private_content:'PRIVATE_SENTINEL'}));}});
  assert.deepEqual(result,value());assert.equal(calls.length,1);assert.equal(calls[0].url,'https://openrouter.ai/api/v1/generation?id=gen-fixture-reconcile');assert.equal(calls[0].init.method,'GET');assert.equal(calls[0].init.body,undefined);assert.equal(calls[0].init.redirect,'manual');
  for(const response of [new Response('PRIVATE_SENTINEL',{status:500}),new Response(null,{status:302,headers:{location:'https://other.invalid/content'}}),new Response('x'.repeat(32769),{headers:{'Content-Type':'application/json'}}),Response.json(metadata({total_cost:null}))])await assert.rejects(lookupGeneration(expected,'fixture-secret',{now,fetcher:async()=>response}),error=>error instanceof Error&&!error.message.includes('PRIVATE'));
  let aborted=false;await assert.rejects(lookupGeneration(expected,'fixture-secret',{now,timeout_ms:5,fetcher:async(_url,init)=>{init.signal?.addEventListener('abort',()=>aborted=true);return new Promise(()=>{});}}));assert(aborted);
});

test('checkpoint is immutable and reconciliation preserves outcome and request-start accounting day',()=>{
  const f=fixture(),input=registration(),row=f.ledger.register(input,1000,started).receipt!;
  assert(f.ledger.checkpoint(row.server_request_id,expected.generation_id,started+1));assert(f.ledger.checkpoint(row.server_request_id,expected.generation_id,started+2));assert.equal(f.ledger.checkpoint(row.server_request_id,'gen-other',started+3),false);
  assert(f.ledger.finish(row.server_request_id,observation({state:'cancelled',failure_code:'client_cancelled'}),started+4));
  assert.equal(f.ledger.reconcile(row.server_request_id,value(),started+1000).kind,'unavailable');
  assert.equal(f.ledger.reconcile(row.server_request_id,value(),now).kind,'settled');
  const settled=f.ledger.receipt('alice',input.request_id)!;assert.equal(settled.state,'cancelled');assert.equal(settled.failure_code,'client_cancelled');assert.equal(settled.day,row.day);assert.equal(settled.usage_source,'generation');assert.equal(settled.cost_nano,50);assert.equal(settled.reconciled_at,new Date(now).toISOString());
  assert.equal(f.open().reconcile(row.server_request_id,value(),now+1).kind,'duplicate');assert.equal(f.ledger.summary(row.day,1000).known_cost_nano,50);assert.equal(f.ledger.summary(row.day,1000).outstanding_reservation_nano,0);
  assert.equal(f.ledger.reconcile(row.server_request_id,{...value(),usage:{...value().usage,cost_nano:51}},now+2).kind,'conflict');
  assert.equal(f.ledger.register(input,1000,now).kind,'duplicate');
});

test('finish and reconciliation ordering never double counts or overwrites confirmed usage',()=>{
  for(const first of ['finish','reconcile']){
    const f=fixture(),input=registration(),row=f.ledger.register(input,1000,started).receipt!;f.ledger.checkpoint(row.server_request_id,expected.generation_id,started+1);
    if(first==='finish')assert(f.ledger.finish(row.server_request_id,observation({usage:value().usage}),now));
    assert.equal(f.ledger.reconcile(row.server_request_id,value(),now).kind,first==='finish'?'duplicate':'settled');
    assert(f.ledger.finish(row.server_request_id,observation({usage:value().usage}),now));
    assert.equal(f.ledger.summary(row.day,1000).known_cost_nano,50);assert.equal(f.ledger.receipt('alice',input.request_id)!.state,'completed');
  }
  const f=fixture(),row=f.ledger.register(registration(),1000,started).receipt!;f.ledger.checkpoint(row.server_request_id,expected.generation_id,started+1);f.ledger.reconcile(row.server_request_id,value(),now);
  assert.equal(f.ledger.finish(row.server_request_id,observation({usage:{...value().usage,cost_nano:51}}),now),false);
  assert.equal(f.ledger.reconcile(row.server_request_id,{...value(),usage:{...value().usage,cost_nano:1001}},now).kind,'conflict');assert(f.ledger.summary(row.day,1000).frozen);
});

test('existing SQLite ledger migration preserves exact finishes and IDs; public manifest declares internal metadata',()=>{
  const f=fixture(),input=registration(),row=f.ledger.register(input,1000,started).receipt!;f.ledger.finish(row.server_request_id,observation({usage:value().usage}),started+1);
  const original=f.db.prepare('SELECT finish_value,request_hash FROM relay_requests').get();
  for(const column of ['usage_source','reconciled_at','reconcile_value'])f.db.exec('ALTER TABLE relay_requests DROP COLUMN '+column);
  const migrated=f.open();assert.equal(migrated.receipt('alice',input.request_id)!.usage_source,'response');assert.deepEqual(f.db.prepare('SELECT finish_value,request_hash FROM relay_requests').get(),original);assert.equal(migrated.register(input,1000,now).kind,'duplicate');
  assert.deepEqual(JSON.parse(readFileSync(new URL('../../../monitoring/relay-manifest.v1.json',import.meta.url),'utf8')),RELAY_MANIFEST);
  for(const key of ['finish_value','reconcile_value','request_hash'])assert(RELAY_MANIFEST.persisted_fields.some(field=>field.startsWith(key)));
  assert.equal(JSON.stringify(migrated.list()).includes('request_hash'),false);
});

test('forwarder checkpoints observed header or first chunk before returning client bytes',async()=>{
  const policy:RelayPolicy={version:'fixture',daily_budget_nano:10000,timeout_ms:1000,aliases:{economy:{model:expected.model,provider:expected.provider,response_models:[expected.model],response_providers:['Fixture'],context_tokens:100,prompt_nano_per_token:10,completion_nano_per_token:20,default_output_tokens:10,max_output_tokens:20}}};
  for(const header of [true,false]){
    const events:string[]=[];const chunk={id:expected.generation_id,model:expected.model,provider:'Fixture',choices:[{index:0,delta:{content:'PRIVATE_SENTINEL'}}]};
    const response=await forwardRelay({request:parseRelayRequest({model:'economy',messages:[{role:'user',content:'test'}],stream:true},policy),request_id:'fixture',server_request_id:'fixture',secret:'fixture',timeout_ms:1000,checkpoint:async id=>{assert.equal(id,expected.generation_id);events.push('checkpoint');},finish:async()=>{events.push('finish');},fetcher:async()=>new Response('data: '+JSON.stringify(chunk)+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream',...(header?{'X-Generation-Id':expected.generation_id}:{})}})});
    if(header)assert.deepEqual(events,['checkpoint']);else assert.deepEqual(events,[]);
    assert.match(await response.text(),/PRIVATE_SENTINEL/);assert.deepEqual(events,['checkpoint','finish']);
  }
  const ended:RelayObservation[]=[];
  const interrupted=await forwardRelay({request:parseRelayRequest({model:'economy',messages:[{role:'user',content:'test'}]},policy),request_id:'fixture',server_request_id:'fixture',secret:'fixture',timeout_ms:5,checkpoint:async()=>{await new Promise(resolve=>setTimeout(resolve,20));},finish:async value=>{ended.push(value);},fetcher:async()=>Response.json({id:expected.generation_id,model:expected.model,provider:'Fixture',choices:[{message:{content:'PRIVATE_SENTINEL'}}]})});
  assert.equal(interrupted.status,504);assert.equal((await interrupted.text()).includes('PRIVATE_SENTINEL'),false);assert.equal(ended.length,1);assert.equal(ended[0].state,'timeout');
});
