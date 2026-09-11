import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {canonicalJSON,MONITORING_MANIFEST,parseAuditBatch,parseOTLP,verifyAuditJWS,monitoringHash,PublicMonitoringStore} from '../src/index.ts';
import {protobuf,field,kv,join,str} from './monitoring-fixtures.ts';
const now=Date.parse('2026-09-11T00:00:00.000Z');
const event=()=>({event_id:crypto.randomUUID(),sequence:1,client_time:new Date(now).toISOString(),kind:'runtime_start' as const,attributes:{outcome:'ok' as const}});
const batch=()=>({schema_version:'1' as const,manifest_version:'1.0.0' as const,client_version:'0.3.0',batch_id:crypto.randomUUID(),stream_id:crypto.randomUUID(),first_sequence:1,previous_batch_sha256:null,events:[event()]});
const subject={person_id:'fictional-alice',installation_id:crypto.randomUUID()};
function store(){const db=new DatabaseSync(':memory:');return {db,store:new PublicMonitoringStore({exec(q,...args){const s=db.prepare(q);return s.columns().length?s.all(...args) as Record<string,unknown>[]:(s.run(...args),[]);}},fn=>{db.exec('BEGIN');try{const value=fn();db.exec('COMMIT');return value;}catch(error){db.exec('ROLLBACK');throw error;}})};}
async function signed(value:unknown,key:CryptoKey,kid=subject.installation_id,headerExtra={}){const enc=(v:unknown)=>Buffer.from(canonicalJSON(v)).toString('base64url');const prefix=enc({alg:'ES256',typ:'myhermes-audit+jws',kid,...headerExtra})+'.'+enc(value);return prefix+'.'+Buffer.from(await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},key,new TextEncoder().encode(prefix))).toString('base64url');}
test('public manifest/event allowlist excludes unknown bodies, enum coercion, stale times and hidden aliases',()=>{
  assert.equal(MONITORING_MANIFEST.otlp.events,false);assert.equal(parseAuditBatch(batch(),now).events.length,1);
  for(const bad of [{...batch(),owner:'bob'},{...batch(),manifest_version:'2.0.0'},{...batch(),events:[{...event(),attributes:{outcome:'ok',prompt:'secret'}}]},{...batch(),events:[{...event(),kind:['sync']}]},{...batch(),events:[{...event(),attributes:{outcome:['ok']}}]},{...batch(),events:[{...event(),sequence:2}]},{...batch(),events:[{...event(),client_time:'2026-01-01T00:00:00.000Z'}]}])assert.throws(()=>parseAuditBatch(bad,now));
});
test('signed audit checks registered key, protected fields, canonical payload and authentic metadata',async()=>{
  const keys=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']),jwk=await crypto.subtle.exportKey('jwk',keys.publicKey),b=batch(),jws=await signed(b,keys.privateKey);
  assert.deepEqual((await verifyAuditJWS(jws,jwk,subject.installation_id,now)).batch,b);
  await assert.rejects(verifyAuditJWS(jws,jwk,crypto.randomUUID(),now));await assert.rejects(verifyAuditJWS(jws.slice(0,-10)+'AAAAAAAAAA',jwk,subject.installation_id,now));
  await assert.rejects(verifyAuditJWS(await signed(b,keys.privateKey,subject.installation_id,{jwk}),jwk,subject.installation_id,now));
  await assert.rejects(verifyAuditJWS(await signed({...b,secret:'canary'},keys.privateKey),jwk,subject.installation_id,now));
});
test('OTLP standard trace subset rejects content everywhere and bounded malformed binary',()=>{
  const bytes=protobuf({now}),spans=parseOTLP(bytes,now);assert.equal(spans.length,1);assert.equal(spans[0].event.kind,'runtime_start');assert.equal(spans[0].status_code,1);
  const bad=[protobuf({now,name:'secret prompt'}),protobuf({now,statusMessage:'secret exception'}),protobuf({now,extraAttributes:{'exception.message':'secret'}}),protobuf({now,resourceExtra:field(1,kv('host.name','secret'))}),protobuf({now,extraSpan:field(11,field(2,str('secret')))}),protobuf({now,extraSpan:field(13,new Uint8Array())}),protobuf({now,extraSpan:field(99,str('secret'))}),protobuf({now,extraSpan:field(5,str('duplicate'))}),protobuf({now,extraAttributes:{'myhermes.outcome':'secret'}}),bytes.slice(0,-1),join(bytes,Uint8Array.from([128])),new Uint8Array(262145)];
  for(const b of bad)assert.throws(()=>parseOTLP(b,now));
  assert.throws(()=>parseOTLP(protobuf({now:now-31*86400000}),now));assert.throws(()=>parseOTLP(protobuf({now,traceId:'00'.repeat(16)}),now));
});
test('public SQL deduplicates lost ACK, enforces immutable chain and isolates owner/trust aggregates',async()=>{
  const {store:s,db}=store(),a=batch();assert.deepEqual(await s.acceptAudit(subject,a,'A'.repeat(86),now),{status:'accepted',accepted:1});assert.equal((await s.acceptAudit(subject,a,'B'.repeat(86),now)).status,'duplicate');
  await assert.rejects(s.acceptAudit(subject,{...a,events:[{...a.events[0],attributes:{outcome:'failed'}}]},'A'.repeat(86),now),/monitoring_reused/);
  const next={...batch(),stream_id:a.stream_id,first_sequence:2,previous_batch_sha256:await monitoringHash(a),events:[{...event(),sequence:2}]};
  await assert.rejects(s.acceptAudit(subject,{...next,previous_batch_sha256:null},'A'.repeat(86),now),/monitoring_chain/);
  await s.acceptAudit(subject,next,'A'.repeat(86),now);
  const spans=parseOTLP(protobuf({now,eventId:a.events[0].event_id}),now);s.acceptSpans(subject,spans,now);assert.equal(s.acceptSpans(subject,spans,now).accepted,0);
  assert.throws(()=>s.acceptSpans(subject,[{...spans[0],hidden:'secret'}] as never,now));
  assert.throws(()=>s.summary({...subject,person_id:'bob'},now));
  const summary=s.summary(subject,now);assert.equal(summary.audit.counts[0].count,2);assert.equal(summary.traces.counts[0].count,1);assert.equal('cost_nano' in summary,false);
  s.reject(subject,'traces','monitoring_invalid',now);assert.equal(s.list(subject,'rejections',undefined,now).records[0].code,'monitoring_invalid');
  assert.equal(JSON.stringify(s.list(subject,'batches',undefined,now)).includes('secret'),false);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM monitoring_events').get()!.n,2);
});
test('multi-span validation rollback and finite retention prevent partial writes and ancient recreation',async()=>{
  const {store:s,db}=store(),a=batch();await s.acceptAudit(subject,a,'A'.repeat(86),now);s.reject(subject,'audit','monitoring_signature',now);
  const spans=parseOTLP(protobuf({now}),now);s.acceptSpans(subject,spans,now);
  const newSpan=parseOTLP(protobuf({now,spanId:'33'.repeat(8)}),now)[0];assert.throws(()=>s.acceptSpans(subject,[newSpan,{...spans[0],status_code:2}],now),/monitoring_reused/);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM monitoring_spans').get()!.n,1);
  s.purge(now+31*86400000);assert.equal(s.list(subject,'rejections',undefined,now+31*86400000).records.length,0);assert.equal(s.list(subject,'audit',undefined,now+31*86400000).records.length,1);
  s.purge(now+91*86400000);assert.equal(s.list(subject,'audit',undefined,now+91*86400000).records.length,0);await assert.rejects(s.acceptAudit(subject,a,'A'.repeat(86),now+91*86400000),/monitoring_age/);
});
test('retention reports when the last record expires so idle stores stop scheduled work',async()=>{
  const {store:s,db}=store();
  assert.deepEqual(s.purge(now),{removed:0,has_records:false});
  s.reject(subject,'audit','monitoring_invalid',now);
  assert.equal(s.purge(now+29*86400000).has_records,true);
  assert.deepEqual(s.purge(now+31*86400000),{removed:1,has_records:false});
  await s.acceptAudit(subject,batch(),'A'.repeat(86),now+1);
  assert.equal(s.purge(now+89*86400000).has_records,true);
  assert.equal(s.purge(now+91*86400000).has_records,false);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM monitoring_subject').get()!.n,1);
});
