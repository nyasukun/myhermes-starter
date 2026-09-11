import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseSkillPackage,sha256} from '../src/skills.ts';
const separators=['\r','\u0085','\u2028','\u2029'];
async function fixture(document:string){const raw=new TextEncoder().encode(document);return {schema_version:'1',skill_id:'demo',version:'1.2.3',description:'Synthetic fixture',files:[{path:'SKILL.md',content_base64:Buffer.from(raw).toString('base64'),sha256:await sha256(raw),executable:false}],requires:{connectors:[],connections:[]}};}
test('YAML header line separators cannot reintroduce prohibited environment or credential fields',async()=>{
  for(const separator of separators)for(const field of ['required_environment_variables','required_credential_files']){
    const document=`---\nname: demo\ndescription: Synthetic${separator}${field}:${separator}  - PRIVATE_FIXTURE_VALUE\n---\nSynthetic body\n`;
    const value=await fixture(document);await assert.rejects(()=>parseSkillPackage(value));
  }
});
test('plain YAML space and tab comments cannot change description types',async()=>{
  for(const scalar of ['true','false','null','yes','No','ON','off','Normal description'])for(const separator of [' ','\t']){
    const value=await fixture(`---\nname: demo\ndescription: ${scalar}${separator}# synthetic\n---\n# Synthetic heading only\n`);
    await assert.rejects(()=>parseSkillPackage(value));
  }
});
test('standard newlines, Japanese plain descriptions, quoted Unicode and Unicode body remain valid',async()=>{
  for(const ending of ['\n','\r\n'])for(const description of ['日本語の説明','Normal\tdescription','Normal:description','Normal:\tdescription','Normal:','Normal#description','NaN','None','y','n','"true"','"true # description"','"true\\t# description"','"\\u0085quoted data"','"\u{11f02} newer Unicode"']){
    const document=['---','name: demo','description: '+description,'---','Unicode body:'].join(ending)+'\n'+separators.join('body')+'\n';
    const value=await fixture(document);assert.deepEqual(await parseSkillPackage(value),value);
  }
});
