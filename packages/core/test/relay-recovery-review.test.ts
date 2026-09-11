import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {PublicRelayLedger,createRelayRequestId,type RelayObservation} from '../src/index.ts';
function fixture(){
  const db=new DatabaseSync(':memory:');
  const open=()=>new PublicRelayLedger({exec(sql,...args){const statement=db.prepare(sql);return statement.columns().length?statement.all(...args) as Record<string,unknown>[]:(statement.run(...args),[]);}},fn=>{db.exec('BEGIN');try{const result=fn();db.exec('COMMIT');return result;}catch(error){db.exec('ROLLBACK');throw error;}});
  return {db,open,ledger:open()};
}
const input=(now=Date.now())=>({person_id:'fixture-owner',installation_id:'fixture-installation',request_id:createRelayRequestId(now),request_hash:'a'.repeat(64),alias:'economy',model:'fixture/model',provider:'fixture',reservation_nano:1000});
const observation=(usage:RelayObservation['usage']):RelayObservation=>({state:'completed',failure_code:null,generation_id:'gen-fixture-recovery',returned_model:'fixture/model',returned_provider:'Fixture',usage});
const known={prompt_tokens:10,completion_tokens:2,total_tokens:12,cost_nano:50};

test('recovery review: crash registration survives restart and still consumes budget after 91 days',()=>{
  const f=fixture(),day=Date.parse('2026-09-11T00:00:00Z'),request=input(day);
  const accepted=f.ledger.register(request,1000,day);assert.equal(accepted.kind,'accepted');
  const reopened=f.open();assert.equal(reopened.register(request,1000,day+91*86400000).kind,'duplicate');
  assert.equal(reopened.receipt(request.person_id,request.request_id)!.state,'registered');
  assert.equal(reopened.register(input(day+91*86400000),1000,day+91*86400000).kind,'budget');
  assert.equal(reopened.summary('2026-12-11',1000).outstanding_reservation_nano,1000);
});

test('recovery review: finished unknown usage cannot be upgraded via repeated finish',()=>{
  const f=fixture(),request=input();const accepted=f.ledger.register(request,1000);
  assert.equal(f.ledger.finish(accepted.receipt!.server_request_id,observation(null)),true);
  assert.equal(f.open().finish(accepted.receipt!.server_request_id,observation(known)),false);
  const receipt=f.ledger.receipt(request.person_id,request.request_id)!;
  assert.equal(receipt.usage_state,'unknown');assert.equal(receipt.cost_nano,null);assert.equal(receipt.generation_id,'gen-fixture-recovery');
  assert.equal(f.ledger.register(input(),1000).kind,'budget');
});

test('recovery review: retained known receipts enforce capacity while never dropping idempotency',()=>{
  const f=fixture(),start=Date.parse('2026-09-11T00:00:00Z'),request=input(start);const accepted=f.ledger.register(request,1000,start);
  f.ledger.finish(accepted.receipt!.server_request_id,observation(known),Date.parse('2026-09-11T00:00:01Z'));
  // Populate synthetic historical metadata directly to test the capacity bound
  // without 100k O(n) admission scans. All rows have known explicit zero cost.
  f.db.exec(`WITH RECURSIVE n(x) AS(SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<99999)
    INSERT INTO relay_requests(person_id,installation_id,request_id,server_request_id,request_hash,alias,model,provider,day,created_at,updated_at,reservation_nano,cost_nano,prompt_tokens,completion_tokens,total_tokens,state,usage_state)
    SELECT 'fixture-owner','fixture-installation',printf('00000000-0000-4000-8000-%012d',x),printf('10000000-0000-4000-8000-%012d',x),'${'a'.repeat(64)}','economy','fixture/model','fixture','2026-09-11','2026-09-11T00:00:00Z','2026-09-11T00:00:01Z',1000,0,0,0,0,'completed','known' FROM n`);
  const reopened=f.open();assert.equal(reopened.summary('2026-09-12',1000).available_nano,1000);
  assert.equal(reopened.register(input(start+86400000),1000,start+86400000).kind,'capacity');
  assert.equal(reopened.register(request,1000,start+86400000).kind,'duplicate');assert.equal(reopened.list().requests.length,100);
  const later=start+91*86400000;assert.equal(reopened.register(input(later),1000,later).kind,'accepted');
  assert.equal(reopened.register(request,1000,later).kind,'expired');
  assert.equal(reopened.summary('2026-12-11',1000).outstanding_reservation_nano,1000);
});
