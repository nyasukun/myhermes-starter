import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseSyncAcknowledgement,installationSyncStatus,SYNC_STATUS_MANIFEST} from '../src/sync-status.ts';
test('applied revision ACK strictly validates metadata without clocks, identities or content',()=>{
  for(const revision of [0,1,Number.MAX_SAFE_INTEGER])assert.deepEqual(parseSyncAcknowledgement({schema_version:'1',revision}),{schema_version:'1',revision});
  for(const revision of [-1,1.1,Number.MAX_SAFE_INTEGER+1,NaN,Infinity,'1',true,null,[],{}])assert.throws(()=>parseSyncAcknowledgement({schema_version:'1',revision}));
  for(const value of [null,[],{}, {schema_version:1,revision:0},{schema_version:'1',revision:0,person_id:'another'},{schema_version:'1',revision:0,received_at:'forged'},{schema_version:'1',revision:0,content:'body'}])assert.throws(()=>parseSyncAcknowledgement(value));
});
test('public projection distinguishes no report, explicit empty revision and upload observation',()=>{
  const empty={sync_revision:0,last_sync_at:null,applied_revision:null,applied_revision_received_at:null};
  assert.deepEqual(installationSyncStatus(empty),{server_observed:{source:'server_observed',accepted_revision:null,received_at:null},client_reported:{source:'client_reported',applied_revision:null,received_at:null}});
  const received_at='2026-09-11T03:00:00.000Z';
  assert.deepEqual(installationSyncStatus({...empty,applied_revision:0,applied_revision_received_at:received_at}).client_reported,{source:'client_reported',applied_revision:0,received_at});
  assert.deepEqual(installationSyncStatus({...empty,sync_revision:4,last_sync_at:received_at}).server_observed,{source:'server_observed',accepted_revision:4,received_at});
});
test('distributed sync collection manifest and schema match executable public contract',()=>{
  assert.deepEqual(JSON.parse(readFileSync(new URL('../../../monitoring/sync-status-manifest.v1.json',import.meta.url),'utf8')),SYNC_STATUS_MANIFEST);
  const schema=JSON.parse(readFileSync(new URL('../../../schemas/sync-ack.v1.schema.json',import.meta.url),'utf8'));
  assert.deepEqual(schema.required,['schema_version','revision']);assert.equal(schema.additionalProperties,false);assert.equal(schema.properties.revision.maximum,Number.MAX_SAFE_INTEGER);
});
