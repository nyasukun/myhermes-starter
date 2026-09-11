/** Independent wire/privacy and idle-chain retention regressions. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {PublicMonitoringStore,monitoringHash,parseOTLP,MONITORING_MANIFEST} from '../src/index.ts';
import {protobuf,field} from './monitoring-fixtures.ts';

test('a retired server chain requires a new stream after long client inactivity',async()=>{
  const db=new DatabaseSync(':memory:');
  const store=new PublicMonitoringStore({exec(q,...args){const s=db.prepare(q);return s.columns().length?s.all(...args) as Record<string,unknown>[]:(s.run(...args),[]);}},fn=>{db.exec('BEGIN');try{const value=fn();db.exec('COMMIT');return value;}catch(error){db.exec('ROLLBACK');throw error;}});
  const now=Date.parse('2026-09-11T00:00:00Z'),later=now+91*86400000;
  const subject={person_id:'synthetic-owner',installation_id:crypto.randomUUID()};
  const event=(time:number,sequence:number)=>({event_id:crypto.randomUUID(),sequence,client_time:new Date(time).toISOString(),kind:'sync' as const,attributes:{outcome:'ok' as const}});
  const first={schema_version:'1' as const,manifest_version:'1.0.0' as const,client_version:'0.3.0',batch_id:crypto.randomUUID(),stream_id:crypto.randomUUID(),first_sequence:1,previous_batch_sha256:null,events:[event(now,1)]};
  await store.acceptAudit(subject,first,'A'.repeat(86),now);
  const stale={...first,batch_id:crypto.randomUUID(),first_sequence:2,previous_batch_sha256:await monitoringHash(first),events:[event(later,2)]};
  await assert.rejects(store.acceptAudit(subject,stale,'A'.repeat(86),later),/monitoring_chain/);
  const fresh={...first,batch_id:crypto.randomUUID(),stream_id:crypto.randomUUID(),events:[event(later,1)]};
  assert.equal((await store.acceptAudit(subject,fresh,'A'.repeat(86),later)).accepted,1);
  db.close();
});

test('the accepted OTLP parent span identifier is explicitly declared in the public manifest',()=>{
  const now=Date.parse('2026-09-11T00:00:00Z');
  const wire=protobuf({now,extraSpan:field(4,Uint8Array.from([1,2,3,4,5,6,7,8]))});
  assert.equal(parseOTLP(wire,now)[0].parent_span_id,'0102030405060708');
  assert.equal(JSON.stringify(MONITORING_MANIFEST).includes('"parent_span_id"'),true);
});
