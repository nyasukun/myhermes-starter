import test from 'node:test';
import assert from 'node:assert/strict';
import {metadataCursor,parseMetadataCursor,METADATA_PAGE_SIZE} from '../src/pagination.ts';
test('metadata cursors have canonical bounded typed tuples, without owner selectors',()=>{
  assert.equal(METADATA_PAGE_SIZE,100);
  for(const [kind,tuple] of [['templates',['github-read','1.0.42']],['content_editors',['仮の利用者','skill']]] as const){
    const token=metadataCursor(kind,[...tuple]);assert.deepEqual(parseMetadataCursor(token,kind),tuple);
    assert.throws(()=>parseMetadataCursor(token,kind==='templates'?'content_editors':'templates'));
    assert.throws(()=>parseMetadataCursor(token+'=',kind));
  }
  for(const value of ['',null,[],true,'////','A'.repeat(1025),btoa('{"kind":"templates","keys":["../owner","1.0.0"]}')])assert.throws(()=>parseMetadataCursor(value,'templates'));
});
