import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Worker} from 'node:worker_threads';
import * as core from '../src/index.ts';
const now=Date.parse('2026-09-11T00:00:00Z'),budget=1_000_000_000,day=86400000;
const input=(person='alice',at=now)=>({person_id:person,installation_id:'synthetic',request_id:core.createRelayRequestId(at),request_hash:'a'.repeat(64),alias:'economy',model:'fixture/model',provider:'fixture',reservation_nano:1000});
const observation=(state:core.RelayObservation['state']='completed'):core.RelayObservation=>({state,failure_code:state==='completed'?null:state==='cancelled'?'client_cancelled':state==='timeout'?'relay_timeout':'upstream_unavailable',generation_id:null,returned_model:null,returned_provider:null,usage:null});
const policy:core.RelayPolicy={version:'fixture',daily_budget_nano:budget,timeout_ms:1000,aliases:{economy:{model:'fixture/model',provider:'fixture',response_models:['fixture/model'],response_providers:['fixture'],context_tokens:1000,prompt_nano_per_token:1,completion_nano_per_token:1,default_output_tokens:10,max_output_tokens:20}}};
function fixture(path=':memory:'){
  const db=new DatabaseSync(path);db.exec('PRAGMA busy_timeout=10000');let fail:string|null=null;
  const open=()=>new core.PublicRelayLedger({exec(q,...args){if(fail&&q.startsWith(fail))throw new Error('synthetic SQL interruption');const s=db.prepare(q);return s.columns().length?s.all(...args) as Record<string,unknown>[]:(s.run(...args),[]);}},fn=>{db.exec('BEGIN IMMEDIATE');try{const r=fn();db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}});
  return {db,open,ledger:open(),setFailure:(prefix:string|null)=>{fail=prefix;}};
}

test('concurrency policy is optional default 8, strictly bounded and server-only',()=>{
  assert.equal(core.relayConcurrencyLimit(),8);
  for(const cap of [1,8,128]){assert.equal(core.relayConcurrencyLimit(cap),cap);core.validateRelayPolicy({...policy,max_concurrent_requests:cap});}
  for(const cap of [0,129,-1,1.5,null,true,'8',NaN,Infinity]){
    assert.throws(()=>core.validateRelayPolicy({...policy,max_concurrent_requests:cap as number}));
    const f=fixture();assert.throws(()=>f.ledger.register(input(),budget,now,cap as number));assert.equal(f.ledger.list().requests.length,0);f.db.close();
  }
  assert.throws(()=>core.validateRelayPolicy({...policy,concurrency:8} as core.RelayPolicy));
  assert.throws(()=>core.parseRelayRequest({model:'economy',messages:[{role:'user',content:'synthetic'}],max_concurrent_requests:128},policy));
  const schema=JSON.parse(readFileSync(new URL('../../../schemas/relay-summary.v1.schema.json',import.meta.url),'utf8')),f=fixture();
  assert.deepEqual(Object.keys(f.ledger.summary(core.relayDay(now),budget)).sort(),schema.required.toSorted());
  assert.deepEqual(schema.properties.max_concurrent_requests,{type:'integer',minimum:1,maximum:128});
  assert.equal(core.RELAY_MANIFEST.concurrency.default,core.relayConcurrencyLimit());f.db.close();
});

test('default cap and explicit maximum count every owner, installation and day without returning another owner receipt',()=>{
  for(const cap of [8,128]){
    const f=fixture();for(let n=0;n<cap;n++)assert.equal(f.ledger.register({...input(n%2?'alice':'bob'),installation_id:'fixture-'+n},budget,now,...(cap===8?[]:[cap])).kind,'accepted');
    const rejected=f.ledger.register(input('carol',now+day),budget,now+day,...(cap===8?[]:[cap]));assert.deepEqual(rejected,{kind:'concurrency'});
    const totals=f.ledger.summary(core.relayDay(now+day),budget,cap);assert.equal(totals.requests,0);assert.equal(totals.active_requests,cap);assert.equal(totals.max_concurrent_requests,cap);
    assert.equal(f.open().summary(core.relayDay(now),budget,cap).active_requests,cap);assert.equal(f.ledger.list().requests.length,Math.min(cap,100));f.db.close();
  }
});

test('retained duplicate and changed-payload decisions precede cap, budget and freeze; lowering cap never cancels work',()=>{
  const f=fixture(),a=input(),b=input('bob');f.ledger.register(a,budget,now,2);f.ledger.register(b,budget,now,2);
  assert.deepEqual(f.ledger.register(input(),budget,now,1),{kind:'concurrency'});
  f.db.exec('UPDATE relay_settings SET frozen=1');
  assert.equal(f.ledger.register(a,1,now+31*day,1).kind,'duplicate');assert.equal(f.ledger.register({...a,request_hash:'b'.repeat(64)},1,now+31*day,1).kind,'reused');
  assert.equal(f.ledger.summary(core.relayDay(now),budget,1).active_requests,2);assert.equal(f.ledger.list().requests.length,2);f.db.close();
});

test('each durable terminal outcome releases exactly one slot but keeps unknown accounting reserved',()=>{
  for(const state of ['completed','failed','cancelled','timeout'] as const){
    const f=fixture(),a=input(),r=f.ledger.register(a,budget,now,1).receipt!;
    assert.deepEqual(f.ledger.register(input(),budget,now,1),{kind:'concurrency'});
    assert(f.ledger.finish(r.server_request_id,observation(state),now));
    const next=input();assert.equal(f.ledger.register(next,budget,now,1).kind,'accepted');
    assert(f.ledger.finish(r.server_request_id,observation(state),now));
    assert.deepEqual(f.ledger.register(input(),budget,now,1),{kind:'concurrency'});
    const totals=f.ledger.summary(core.relayDay(now),budget,1);assert.equal(totals.active_requests,1);assert.equal(totals.outstanding_reservation_nano,2000);assert.equal(totals.unknown_requests,2);f.db.close();
  }
});

test('unfinished registered request retains its slot after billing reconciliation, restart and retention age',()=>{
  const f=fixture(),r=f.ledger.register(input(),budget,now,1).receipt!;f.ledger.checkpoint(r.server_request_id,'gen-synthetic',now);
  const value={generation_id:'gen-synthetic',returned_model:'fixture/model',returned_provider:'fixture',usage:{cost_nano:10,prompt_tokens:1,completion_tokens:1,total_tokens:2}};
  assert.equal(f.ledger.reconcile(r.server_request_id,value,now+600000).kind,'settled');assert.equal(f.ledger.retentionNext(now),null);
  assert.equal(f.ledger.purge(now+200*day).deleted,0);const reopened=f.open();assert.equal(reopened.retentionNext(now),null);
  assert.deepEqual(reopened.register(input('bob',now+200*day),budget,now+200*day,1),{kind:'concurrency'});
  assert.equal(reopened.summary(core.relayDay(now),budget,1).outstanding_reservation_nano,0);
  assert(reopened.finish(r.server_request_id,{...observation(),...value},now+200*day));
  assert.equal(reopened.retentionNext(now),now+290*day);assert.equal(reopened.register(input('bob',now+200*day),budget,now+200*day,1).kind,'accepted');f.db.close();
});

test('migration removes old registered-known deadlines without deleting receipts or releasing slots',()=>{
  const f=fixture(),r=f.ledger.register(input(),budget,now,1).receipt!;
  f.db.prepare("UPDATE relay_requests SET usage_state='known',usage_source='generation',cost_nano=10,retire_after_ms=? WHERE server_request_id=?").run(now,r.server_request_id);
  const migrated=f.open();assert.equal(migrated.retentionNext(now),null);assert.equal(migrated.purge(now+200*day).deleted,0);
  assert.equal(migrated.summary(core.relayDay(now),budget,1).active_requests,1);assert(migrated.serverReceipt(r.server_request_id));f.db.close();
});

test('interrupted insert or finish transaction cannot leak or prematurely release an execution slot',()=>{
  const f=fixture();f.setFailure('INSERT INTO relay_requests');assert.throws(()=>f.ledger.register(input(),budget,now,1));f.setFailure(null);
  assert.equal(f.ledger.summary(core.relayDay(now),budget,1).active_requests,0);const r=f.ledger.register(input(),budget,now,1).receipt!;
  f.setFailure('UPDATE relay_requests SET retire_after_ms');assert.throws(()=>f.ledger.finish(r.server_request_id,observation(),now));f.setFailure(null);
  assert.equal(f.open().summary(core.relayDay(now),budget,1).active_requests,1);assert.deepEqual(f.ledger.register(input(),budget,now,1),{kind:'concurrency'});
  assert(f.ledger.finish(r.server_request_id,observation(),now));assert.equal(f.ledger.register(input(),budget,now,1).kind,'accepted');f.db.close();
});

// Independent connections and actual worker threads verify the count/insert
// transaction, rather than Promise.all over synchronous calls on one connection.
async function concurrent(path:string,actions:Record<string,unknown>[]){
  const gate=new SharedArrayBuffer(4),view=new Int32Array(gate),workers:Worker[]=[];
  const code=`const {parentPort,workerData}=require('node:worker_threads');const {DatabaseSync}=require('node:sqlite');
  (async()=>{const core=await import(workerData.module);const db=new DatabaseSync(workerData.path);db.exec('PRAGMA busy_timeout=10000');
  const ledger=new core.PublicRelayLedger({exec(q,...args){const s=db.prepare(q);return s.columns().length?s.all(...args):(s.run(...args),[]);}},fn=>{db.exec('BEGIN IMMEDIATE');try{const r=fn();db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}});
  parentPort.postMessage({ready:true});Atomics.wait(new Int32Array(workerData.gate),0,0);
  const a=workerData.action;const result=a.finish?ledger.finish(a.finish,a.observation,workerData.now):ledger.register(a.input,workerData.budget,workerData.now,a.cap);
  db.close();parentPort.postMessage({result});})().catch(()=>{parentPort.postMessage({error:'synthetic_worker_failed'});process.exitCode=1;});`;
  try{
    let ready=0;let release!:()=>void;const allReady=new Promise<void>(resolve=>{release=resolve;});
    const pending=actions.map(action=>new Promise<unknown>((resolve,reject)=>{
      const w=new Worker(code,{eval:true,workerData:{module:new URL('../src/index.ts',import.meta.url).href,path,gate,action,now,budget}});workers.push(w);
      w.on('message',m=>{if(m.ready){if(++ready===actions.length)release();}else if(m.error)reject(new Error(m.error));else resolve(m.result);});w.on('error',reject);w.on('exit',code=>{if(code)reject(new Error('synthetic_worker_exit'));});
    }));
    const results=Promise.all(pending);await Promise.race([allReady,results.then(()=>{})]);Atomics.store(view,0,1);Atomics.notify(view,0);return await results;
  }finally{await Promise.all(workers.map(w=>w.terminate()));}
}

test('simultaneous SQLite writers cannot over-admit; racing finish and register releases at most one slot',{timeout:30000},async()=>{
  const dir=mkdtempSync(join(tmpdir(),'myhermes-relay-cap-')),path=join(dir,'fixture.sqlite');const f=fixture(path);
  try{
    const results=await concurrent(path,Array.from({length:12},(_,n)=>({input:input(n%2?'alice':'bob'),cap:3}))) as {kind:string}[];
    assert.equal(results.filter(r=>r.kind==='accepted').length,3);assert.equal(results.filter(r=>r.kind==='concurrency').length,9);assert.equal(f.ledger.summary(core.relayDay(now),budget,3).active_requests,3);
    for(const r of f.ledger.list().requests)f.ledger.finish(r.server_request_id,observation(),now);
    const r=f.ledger.register(input(),budget,now,1).receipt!,candidate=input();
    const race=await concurrent(path,[{finish:r.server_request_id,observation:observation()},{finish:r.server_request_id,observation:observation()},{input:candidate,cap:1}]);
    assert.equal(race[0],true);assert.equal(race[1],true);assert(['accepted','concurrency'].includes((race[2] as {kind:string}).kind));
    const retry=f.ledger.register(candidate,budget,now,1);assert(['accepted','duplicate'].includes(retry.kind));
    assert.equal(f.ledger.summary(core.relayDay(now),budget,1).active_requests,1);assert.deepEqual(f.ledger.register(input(),budget,now,1),{kind:'concurrency'});
  }finally{f.db.close();rmSync(dir,{recursive:true,force:true});}
});
