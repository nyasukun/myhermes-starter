import test from 'node:test';
import assert from 'node:assert/strict';
import {parseMemberCreate,parseMemberUpdate} from '../src/members.ts';
const request_id='10000000-0000-4000-8000-000000000001';
const access_subject='20000000-0000-4000-8000-000000000001';
const create={schema_version:'1',request_id,access_subject,role:'member'};
const update={schema_version:'1',request_id,base_revision:1,role:'admin',active:true};
test('strict member schemas bind an explicit immutable Access subject and accept boolean activation',()=>{
  assert.deepEqual(parseMemberCreate(create),create);
  assert.deepEqual(parseMemberUpdate(update),update);
  for(const change of [{person_id:access_subject},{email:'person@example.invalid'},{access_subject:'person@example.invalid'},{role:['admin']},{request_id:[request_id]},{schema_version:1}]) assert.throws(()=>parseMemberCreate({...create,...change}));
  for(const change of [{access_subject},{person_id:access_subject},{base_revision:0},{base_revision:Number.MAX_SAFE_INTEGER},{base_revision:'1'},{active:1},{active:'false'},{role:['admin']}]) assert.throws(()=>parseMemberUpdate({...update,...change}));
});
