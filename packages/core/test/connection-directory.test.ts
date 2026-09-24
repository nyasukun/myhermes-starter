import test from 'node:test';
import assert from 'node:assert/strict';
import {parseIntegration,parseConnectionReport,mcpRequirementStatus,parseCompanionRelease} from '../src/index.ts';
const integration=()=>({integration_id:'work-chat',display_name:'Work chat',service:'chat',required:true,enabled:true,source:{kind:'mcp',server_name:'work_chat'},guide:{summary:'Use your work account.',steps:['Ask the administrator for access.'],url:'https://docs.example.com/setup'}});
const report=()=>({schema_version:'1',report_id:crypto.randomUUID(),base_revision:0,companion_version:'0.4.0',collection_status:'ok',connections:[{server_name:'work_chat',status:'connected'}]});
test('company guide references have a bounded strict contract with HTTPS-only links',()=>{
  assert.equal(parseIntegration(integration()).source.kind,'mcp');
  for(const source of [{kind:'mcp',server_name:'bad/name'},{kind:'mcp',server_name:'work_chat',token:'secret'},{kind:'github',template_id:'work-git',server_name:'other'}])assert.throws(()=>parseIntegration({...integration(),source}));
  for(const url of ['javascript:alert(1)','https://user:password@example.com','https://example.com?token=secret','https://example.com/#secret',' https://example.com'])assert.throws(()=>parseIntegration({...integration(),guide:{...integration().guide,url}}));
  assert.throws(()=>parseIntegration({...integration(),guide:{...integration().guide,steps:['\u001b[31m']}}));
  assert.throws(()=>parseIntegration({...integration(),guide:{...integration().guide,steps:Array(12).fill('🙂'.repeat(1000))}}));
});
test('reports reject credential fields, owner selectors, duplicate names and failed empty-success confusion',()=>{
  assert.equal(parseConnectionReport(report()).connections.length,1);
  for(const extra of [{token:'secret'},{installation_id:crypto.randomUUID()},{person_id:'other'},{base_revision:true},{connections:[...report().connections,...report().connections]},{collection_status:'unavailable'},{connections:[{server_name:'work_chat',status:'connected',url:'https://private.example'}]}])assert.throws(()=>parseConnectionReport({...report(),...extra}));
  assert.equal(parseConnectionReport({...report(),collection_status:'unavailable',connections:[]}).connections.length,0);
});
test('missing, unavailable and stale observations never become a fresh disconnected assertion',()=>{
  const now=Date.now(),value={received_at:new Date(now).toISOString(),collection_status:'ok',connections:[]};
  assert.equal(mcpRequirementStatus('work_chat',null,now),'unknown');
  assert.equal(mcpRequirementStatus('work_chat',value,now),'not_connected');
  assert.equal(mcpRequirementStatus('work_chat',{...value,collection_status:'unavailable'},now),'unknown');
  assert.equal(mcpRequirementStatus('work_chat',value,now+300001),'stale');
});
test('companion release is fixed to the public versioned wheel and a complete SHA-256',()=>{
  const release={version:'0.4.0',wheel_url:'https://github.com/nyasukun/myhermes-starter/releases/download/v0.4.0/myhermes_companion-0.4.0-py3-none-any.whl',sha256:'a'.repeat(64)};
  assert.deepEqual(parseCompanionRelease(release),release);
  for(const change of [{wheel_url:'https://evil.example/wheel.whl'},{version:'0.5.0'},{sha256:'a'.repeat(63)},{command:'pip install evil'}])assert.throws(()=>parseCompanionRelease({...release,...change}));
});
