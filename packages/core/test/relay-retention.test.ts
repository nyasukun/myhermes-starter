import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createRelayRequestId,relayRequestBirth,relayRequestAdmissible,RELAY_REQUEST_MAX_AGE_MS,RELAY_KNOWN_RETENTION_MS,PublicRelayLedger,relayDay,type RelayObservation} from '../src/index.ts';
const start=Date.parse('2026-09-11T00:00:00Z'),day=86400000;
const input=(now=start)=>({person_id:'alice',installation_id:'fixture',request_id:createRelayRequestId(now),request_hash:'a'.repeat(64),alias:'economy',model:'fixture/model',provider:'fixture',reservation_nano:1000});
const observation=(cost=50):RelayObservation=>({state:'completed',failure_code:null,generation_id:'gen-fixture',returned_model:'fixture/model',returned_provider:'fixture',usage:{cost_nano:cost,prompt_tokens:1,completion_tokens:1,total_tokens:2}});
function fixture(onExec:(query:string)=>void=()=>{}){const db=new DatabaseSync(':memory:');const open=()=>new PublicRelayLedger({exec(q,...args){onExec(q);const s=db.prepare(q);return s.columns().length?s.all(...args) as Record<string,unknown>[]:(s.run(...args),[]);}},fn=>{db.exec('BEGIN');try{const r=fn();db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}});return {db,open,ledger:open()};}

test('UUIDv7 RFC timestamp and strict immutable admission boundaries',()=>{
  assert.equal(relayRequestBirth('017f22e2-79b0-7cc3-98c4-dc0c0c07398f'),1645557742000);
  const generated=Array.from({length:1000},()=>createRelayRequestId(start));assert.equal(new Set(generated).size,1000);
  for(const id of generated){assert.match(id,/^[a-f0-9]{8}-[a-f0-9]{4}-7[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);assert.equal(relayRequestBirth(id.toUpperCase()),start);}
  for(const [offset,accepted] of [[-RELAY_REQUEST_MAX_AGE_MS,true],[-RELAY_REQUEST_MAX_AGE_MS-1,false],[300000,true],[300001,false]] as const)assert.equal(relayRequestAdmissible(createRelayRequestId(start+offset),start),accepted);
  assert.equal(relayRequestBirth(crypto.randomUUID()),null);assert.equal(relayRequestAdmissible(crypto.randomUUID(),start),false);
  for(const n of [-1,NaN,Infinity,1.5,2**48])assert.throws(()=>createRelayRequestId(n));
  assert.throws(()=>relayRequestBirth('017f22e2-79b0-7cc3-18c4-dc0c0c07398f'));
});

test('legacy absent IDs and expired/future IDs never register; retained receipt lookup comes first',()=>{
  const f=fixture();for(const id of [crypto.randomUUID(),createRelayRequestId(start-31*day),createRelayRequestId(start+300001)])assert.equal(f.ledger.register({...input(),request_id:id},10000,start).kind,'expired');
  assert.equal(f.ledger.list().requests.length,0);
  const a=input(),row=f.ledger.register(a,10000,start).receipt!;
  assert.equal(f.ledger.register({...a,request_id:a.request_id.toUpperCase()},10000,start+40*day).kind,'duplicate');
  assert.equal(f.ledger.register({...a,request_hash:'b'.repeat(64)},10000,start+40*day).kind,'reused');
  assert.equal(f.ledger.receipt('bob',a.request_id),null);assert.equal(f.ledger.receipt('alice',a.request_id)!.server_request_id,row.server_request_id);
});

test('known rows purge in bounded batches while unknown reservations and frozen state remain',()=>{
  const f=fixture(),unknown=input();f.ledger.register(unknown,10000,start);
  const known=[];for(let n=0;n<3;n++){const a=input(),r=f.ledger.register(a,10000,start).receipt!;f.ledger.finish(r.server_request_id,observation(n===0?0:50),start+1);known.push(a);}
  assert.equal(f.ledger.retentionNext(start),start+1+RELAY_KNOWN_RETENTION_MS);
  assert.equal(f.ledger.purge(start+RELAY_KNOWN_RETENTION_MS).deleted,0);
  const at=start+RELAY_KNOWN_RETENTION_MS+1;
  assert.deepEqual(f.ledger.purge(at,2),{deleted:2,more:true,next_due:at});
  assert.deepEqual(f.ledger.purge(at,2),{deleted:1,more:false,next_due:null});
  assert.equal(f.ledger.list().requests.length,1);assert.equal(f.ledger.summary(relayDay(at),10000).outstanding_reservation_nano,1000);
  assert.equal(f.ledger.summary(relayDay(start),10000).history_complete_since,new Date(start+2).toISOString());
  assert.equal(f.ledger.register(known[0],10000,at).kind,'expired');assert.equal(f.ledger.register(unknown,10000,at).kind,'duplicate');
  assert.equal(f.ledger.retentionNext(at),null);
});

test('persistent clock floor prevents post-purge replay after rollback and controls budget day',()=>{
  const f=fixture(),a=input(),row=f.ledger.register(a,10000,start).receipt!;f.ledger.finish(row.server_request_id,observation(),start);
  const later=start+91*day;assert.equal(f.ledger.purge(later).deleted,1);
  const reopened=f.open();assert.equal(reopened.register(a,10000,start).kind,'expired');
  const newInput=input(later);assert.equal(reopened.register(newInput,10000,start).kind,'accepted');
  assert.equal(reopened.receipt('alice',newInput.request_id)!.day,relayDay(later));
  assert.equal(reopened.summary(relayDay(start),10000).admission_day,relayDay(later));
  assert.equal(reopened.finish(row.server_request_id,observation(),start),false);
  assert.equal(reopened.checkpoint(row.server_request_id,'gen-fixture',start),false);
  assert.equal(reopened.reconcile(row.server_request_id,{generation_id:'gen-fixture',returned_model:'fixture/model',returned_provider:'fixture',usage:observation().usage!},later).kind,'unavailable');
});

test('late accounting and execution outcome update renew known retention; duplicate settlement does not',()=>{
  const f=fixture(),a=input(),r=f.ledger.register(a,10000,start).receipt!;f.ledger.checkpoint(r.server_request_id,'gen-fixture',start);
  assert.equal(f.ledger.purge(start+200*day).deleted,0);
  const usage={generation_id:'gen-fixture',returned_model:'fixture/model',returned_provider:'fixture',usage:observation().usage!};
  assert.equal(f.ledger.reconcile(r.server_request_id,usage,start+200*day).kind,'settled');
  assert.equal(f.ledger.retentionNext(start+200*day),null);
  assert.equal(f.ledger.reconcile(r.server_request_id,usage,start+201*day).kind,'duplicate');
  assert.equal(f.ledger.retentionNext(start+201*day),null);
  assert(f.ledger.finish(r.server_request_id,observation(),start+202*day));
  assert.equal(f.ledger.retentionNext(start+202*day),start+292*day);
  assert(f.ledger.finish(r.server_request_id,observation(),start+203*day));
  assert.equal(f.ledger.retentionNext(start+203*day),start+292*day);
});

test('legacy migration retains existing receipts and reservations then safely expires known rows',()=>{
  const f=fixture(),known=input(),unknown=input();const r=f.ledger.register(known,10000,start).receipt!;f.ledger.finish(r.server_request_id,observation(1001),start); // freezes without a zero override
  const legacy=crypto.randomUUID();f.db.prepare('UPDATE relay_requests SET request_id=?').run(legacy);
  // Add an old unknown fixture directly to model a pre-cutover persisted ledger.
  f.db.prepare("INSERT INTO relay_requests(person_id,installation_id,request_id,server_request_id,request_hash,alias,model,provider,day,created_at,updated_at,reservation_nano,state,usage_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)").run('alice','fixture',unknown.request_id,crypto.randomUUID(),'a'.repeat(64),'economy','fixture/model','fixture',relayDay(start),new Date(start).toISOString(),new Date(start).toISOString(),1000,'registered','unknown');
  f.db.exec('DROP INDEX relay_retention');f.db.exec('ALTER TABLE relay_requests DROP COLUMN retire_after_ms');
  f.db.exec('ALTER TABLE relay_settings DROP COLUMN clock_floor_ms');f.db.exec('ALTER TABLE relay_settings DROP COLUMN history_complete_since');
  const migrated=f.open();assert.equal(migrated.register({...known,request_id:legacy},10000,start).kind,'duplicate');
  assert.equal(migrated.register({...known,request_id:crypto.randomUUID()},10000,start).kind,'expired');
  assert.equal(migrated.purge(start+91*day).deleted,1);assert.equal(migrated.register({...known,request_id:legacy},10000,start).kind,'expired');
  assert.equal(migrated.summary(relayDay(start),10000).outstanding_reservation_nano,1000);assert(migrated.summary(relayDay(start),10000).frozen);
  assert.equal(JSON.stringify(migrated.list()).includes('retire_after_ms'),false);
});


test('failed purge rolls clock, coverage and deletions back in one transaction',()=>{
  let fail=false;const f=fixture(query=>{if(fail&&query.startsWith('DELETE FROM relay_requests'))throw new Error('Synthetic storage failure');});
  const a=input(),r=f.ledger.register(a,10000,start).receipt!;f.ledger.finish(r.server_request_id,observation(),start);
  fail=true;assert.throws(()=>f.ledger.purge(start+91*day));fail=false;
  assert.equal(f.ledger.receipt('alice',a.request_id)!.cost_nano,50);
  assert.equal(f.db.prepare('SELECT clock_floor_ms FROM relay_settings').get()!.clock_floor_ms,start);
  assert.equal(f.ledger.summary(relayDay(start),10000).history_complete_since,null);
  assert.equal(f.ledger.purge(start+91*day).deleted,1);
  assert.equal(f.open().register(a,10000,start).kind,'expired');
});

test('pre-cutover future UUIDv7 remains retained until its own admission time has closed',()=>{
  const f=fixture(),a=input(),r=f.ledger.register(a,10000,start).receipt!;f.ledger.finish(r.server_request_id,observation(),start);
  // The previous generic-UUID contract could accept this otherwise-invalid timestamp.
  const future=createRelayRequestId(start+365*day);
  f.db.prepare('UPDATE relay_requests SET request_id=?,retire_after_ms=NULL').run(future);
  const reopened=f.open(),deadline=start+395*day+1;
  assert.equal(reopened.retentionNext(start),deadline);
  assert.equal(reopened.purge(start+91*day).deleted,0);
  assert.equal(reopened.purge(deadline-1).deleted,0);
  assert.equal(reopened.register({...a,request_id:future},10000,deadline-1).kind,'duplicate');
  assert.equal(reopened.purge(deadline).deleted,1);
  assert.equal(reopened.register({...a,request_id:future},10000,deadline).kind,'expired');
});

test('legacy mixed-case IDs normalize atomically; ambiguous duplicates preserve both reservations and fail closed',()=>{
  const f=fixture(),a=input();f.ledger.register(a,10000,start);
  f.db.prepare('UPDATE relay_requests SET request_id=upper(request_id)').run();
  const reopened=f.open();assert.equal(reopened.register({...a,request_id:a.request_id.toUpperCase()},10000,start).kind,'duplicate');
  assert.equal(reopened.list().requests.length,1);assert.equal(reopened.summary(relayDay(start),10000).outstanding_reservation_nano,1000);
  f.db.prepare(`INSERT INTO relay_requests(person_id,installation_id,request_id,server_request_id,request_hash,alias,model,provider,day,created_at,updated_at,reservation_nano,state,usage_state)
    SELECT person_id,installation_id,upper(request_id),?,request_hash,alias,model,provider,day,created_at,updated_at,reservation_nano,state,usage_state FROM relay_requests`).run(crypto.randomUUID());
  assert.throws(()=>f.open(),error=>error instanceof Error&&error.message==='relay_retention_corrupt');
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM relay_requests').get()!.n,2);
  assert.equal(f.db.prepare('SELECT SUM(reservation_nano) AS n FROM relay_requests').get()!.n,2000);
});
