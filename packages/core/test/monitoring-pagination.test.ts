import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {PublicMonitoringStore,monitoringHash,type AuditBatch} from '../src/index.ts';

const now=Date.parse('2026-09-11T00:00:00.000Z'),maxBytes=1_048_576;
const subject={person_id:'仮'.repeat(128),installation_id:crypto.randomUUID()};
function batch(large=true):AuditBatch{return {schema_version:'1',manifest_version:'1.0.0',client_version:'0.3.0',batch_id:crypto.randomUUID(),stream_id:crypto.randomUUID(),first_sequence:1,previous_batch_sha256:null,events:Array.from({length:large?100:1},(_,index)=>({event_id:crypto.randomUUID(),sequence:index+1,client_time:new Date(now).toISOString(),kind:'model',attributes:{outcome:'ok',duration_ms:86400000,connection_id:crypto.randomUUID(),request_id:crypto.randomUUID(),model_alias:'economy',prompt_tokens:100000000,completion_tokens:100000000,cost_nano:1_000_000_000_000},trace_id:'11'.repeat(16),span_id:'22'.repeat(8)}))};}
function fixture(){
  const db=new DatabaseSync(':memory:');let batchRowsRead=0;
  const store=new PublicMonitoringStore({exec(q,...args){
    const statement=db.prepare(q);if(!statement.columns().length){statement.run(...args);return [];}
    return (function*(){for(const row of statement.iterate(...args)){if(q.startsWith('SELECT cursor,batch_id,stream_id,'))batchRowsRead++;yield row as Record<string,unknown>;}})();
  }},fn=>{db.exec('BEGIN');try{const value=fn();db.exec('COMMIT');return value;}catch(error){db.exec('ROLLBACK');throw error;}});
  return {db,store,read:()=>batchRowsRead,reset:()=>{batchRowsRead=0;}};
}
const bytes=(value:unknown)=>new TextEncoder().encode(JSON.stringify(value)).length;

test('monitoring byte pages preserve full batches and bound UTF-8 Japanese envelope plus one SQL lookahead',async()=>{
  const f=fixture();try{
    const originals=new Map<string,AuditBatch>();
    for(let index=0;index<50;index++){const value=batch();originals.set(value.batch_id,value);await f.store.acceptAudit(subject,value,'A'.repeat(86),now);}
    f.reset();let page=f.store.list(subject,'batches',undefined,now),count=0;
    assert.ok(bytes(page)<=maxBytes);assert.ok(bytes(page)>JSON.stringify(page).length);
    assert.ok(page.records.length>0&&page.records.length<50);assert.equal(f.read(),page.records.length+1);
    const seen=new Set<string>();
    for(;;){
      assert.ok(bytes(page)<=maxBytes);assert.ok(page.records.length<=100);
      for(const row of page.records){
        assert.equal(seen.has(row.batch_id as string),false);seen.add(row.batch_id as string);
        assert.deepEqual(row.batch,originals.get(row.batch_id as string));
        assert.equal(row.batch_sha256,await monitoringHash(row.batch));assert.equal(row.signature,'A'.repeat(86));count++;
      }
      if(page.next_before===null)break;
      assert.equal(page.next_before,page.records.at(-1)!.cursor);
      const previous=page.records.at(-1)!.cursor as number;
      page=f.store.list(subject,'batches',page.next_before,now);
      assert.ok(page.records.every(row=>Number(row.cursor)<previous));
    }
    assert.equal(count,50);assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM monitoring_events').get()!.n,5000);
  }finally{f.db.close();}
});

test('monitoring record cap and exclusive cursors remain stable across inserts and complete final pages',async()=>{
  const f=fixture();try{
    for(let index=0;index<101;index++)await f.store.acceptAudit(subject,batch(false),'A'.repeat(86),now);
    const first=f.store.list(subject,'batches',undefined,now);assert.equal(first.records.length,100);assert.equal(first.next_before,2);assert.ok(bytes(first)<=maxBytes);
    const second=f.store.list(subject,'batches',first.next_before,now);assert.equal(second.records.length,1);assert.equal(second.records[0].cursor,1);assert.equal(second.next_before,null);
    await f.store.acceptAudit(subject,batch(false),'A'.repeat(86),now);
    assert.deepEqual(f.store.list(subject,'batches',first.next_before,now),second);
    const empty=f.store.list(subject,'batches',1,now);assert.deepEqual(empty.records,[]);assert.equal(empty.next_before,null);
    assert.equal(f.store.list(subject,'batches',102,now).records.length,100);
  }finally{f.db.close();}
});

test('a single maximum metadata batch is returned complete and an oversized corrupt row fails closed',async()=>{
  const f=fixture();try{
    const value=batch();await f.store.acceptAudit(subject,value,'A'.repeat(86),now);
    const page=f.store.list(subject,'batches',undefined,now);assert.equal(page.records.length,1);assert.equal(page.next_before,null);assert.deepEqual(page.records[0].batch,value);assert.ok(bytes(page)<=maxBytes);
    f.db.prepare('UPDATE monitoring_batches SET batch_json=?').run(JSON.stringify({corrupt:'仮'.repeat(maxBytes)}));
    assert.throws(()=>f.store.list(subject,'batches',undefined,now),/monitoring_size/);
    assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM monitoring_batches').get()!.n,1);
  }finally{f.db.close();}
});
