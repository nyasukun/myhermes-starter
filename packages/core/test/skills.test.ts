import {test} from "node:test";
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {parseSkillPackage, parseSkillMutation, packageDigest, parsePublication, SKILL_LIMITS} from "../src/skills.ts";
const file = (path:string, content:Buffer|string, executable=false) => {
  const raw = Buffer.from(content);
  return {path,content_base64:raw.toString("base64"),sha256:createHash("sha256").update(raw).digest("hex"),executable};
};
const fixture = () => ({schema_version:"1",skill_id:"example",version:"1.0.0",description:"Fictional package",files:[file("SKILL.md","---\nname: example\ndescription: Fictional skill\n---\nUse the reference."),file("assets/logo.bin",Buffer.from([0,255,1])),file("scripts/check.py","print('example')",true)],requires:{connectors:[],connections:[]}});
test("a complete package validates every byte including binary assets and scripts",async()=>{
  const value = fixture();
  const parsed=await parseSkillPackage(value);
  assert.deepEqual(parsed,value);
  assert.match(await packageDigest(parsed),/^[a-f0-9]{64}$/);
  const reorder={...value,description:value.description};
  assert.equal(await packageDigest(parsed),await packageDigest(await parseSkillPackage(reorder)));
  const corrupted=fixture();corrupted.files[1].content_base64="AA==";
  await assert.rejects(parseSkillPackage(corrupted),/skill_hash_mismatch/);
});
test("paths, aliases, archive metadata, noncanonical encoding and size fail closed",async()=>{
  for(const path of ["../leak","/tmp/leak","scripts/../../leak","scripts\\leak",".env","assets/.git/config","a//b","a/./b","assets/NUL","assets/con.txt","a.","a ","a:b"]){
    const value=fixture();value.files.push(file(path,"x"));await assert.rejects(parseSkillPackage(value),/skill_path_rejected/);
  }
  for(const extra of [{linkname:"/tmp/x"},{mode:511},{secret:"no"}]){
    const value=fixture();Object.assign(value.files[1],extra);await assert.rejects(parseSkillPackage(value));
  }
  const collision=fixture();collision.files.push(file("assets/LOGO.bin","x"));await assert.rejects(parseSkillPackage(collision));
  const nested=fixture();nested.files.push(file("z/SKILL.md","x"));await assert.rejects(parseSkillPackage(nested));
  const parent=fixture();parent.files.push(file("assets","x"));await assert.rejects(parseSkillPackage(parent));
  const encoding=fixture();encoding.files[1].content_base64="AP8B\n";await assert.rejects(parseSkillPackage(encoding));
  const large=fixture();large.files.push(file("large.bin",Buffer.alloc(SKILL_LIMITS.file_bytes+1)));await assert.rejects(parseSkillPackage(large));
});
test("identity, requirements, root SKILL.md and mutation ownership fields are strict",async()=>{
  const value=fixture();value.files=value.files.filter(f=>f.path!=="SKILL.md");await assert.rejects(parseSkillPackage(value));
  const wrong=fixture();wrong.skill_id="another";await assert.rejects(parseSkillPackage(wrong));
  const credential=fixture();credential.files[0]=file("SKILL.md","---\nname: example\ndescription: Example\nrequired_environment_variables: [SECRET]\n---\nExample");await assert.rejects(parseSkillPackage(credential));
  for(const scalar of ['true','null','123','2026-09-11','!!str secret']){const v=fixture();v.files[0]=file('SKILL.md',`---\nname: example\ndescription: ${scalar}\n---\nExample`);await assert.rejects(parseSkillPackage(v));}
  const mutation={schema_version:"1",update_id:crypto.randomUUID(),base_revision:0,skill_id:"example",package:fixture()};
  assert.equal((await parseSkillMutation(mutation)).skill_id,"example");
  await assert.rejects(parseSkillMutation({...mutation,person_id:"other"}));
  await assert.rejects(parseSkillMutation({...mutation,skill_id:"other"}));
  await assert.rejects(parseSkillMutation({...mutation,base_revision:-1}));
  assert.equal((await parseSkillMutation({...mutation,package:null})).package,null);
  assert.throws(()=>parsePublication({publication_id:crypto.randomUUID(),revision:1,audience:{kind:"people",person_ids:[]}}));
  assert.throws(()=>parsePublication({publication_id:crypto.randomUUID(),revision:1,audience:{kind:"all",person_ids:["other"]}}));
});
test("explicit company withdrawal is distinct from a malformed or empty audience",()=>{
  const publication={publication_id:crypto.randomUUID(),revision:1,audience:{kind:"none"}};
  assert.deepEqual(parsePublication(publication),publication);
  assert.throws(()=>parsePublication({...publication,audience:{kind:"none",person_ids:[]}}));
  assert.throws(()=>parsePublication({...publication,audience:{kind:"people",person_ids:[]}}));
});
