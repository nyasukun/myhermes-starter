import {boundedString, canonicalJSON, ContractError, exact, UUID} from "./index.ts";

export const SKILL_LIMITS = {files:100,file_bytes:524288,total_bytes:2097152,request_bytes:3000000} as const;
export const SKILL_ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
export const SKILL_VERSION = /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/;
const HASH = /^[a-f0-9]{64}$/;
export type SkillFile = {path:string;content_base64:string;sha256:string;executable:boolean};
export type SkillPackage = {
  schema_version:"1";skill_id:string;version:string;description:string;files:SkillFile[];
  requires:{connectors:{connector_id:string;version:string}[];connections:string[]};
  derived_from?:{scope:"company"|"personal";skill_id:string;version:string;sha256:string};
};
export type SkillMutation = {schema_version:"1";update_id:string;base_revision:number;skill_id:string;package:SkillPackage|null;resolves_update_id?:string};
export type SkillResult = {status:"applied"|"conflict";revision:number;update_id:string;skill_id:string};
export type SkillAudience = {kind:"all"}|{kind:"none"}|{kind:"people";person_ids:string[]};
export type SkillPublication = {publication_id:string;revision:number;audience:SkillAudience};
export function skillId(value:unknown):string {
  const result=boundedString(value,40);
  if(!SKILL_ID.test(result)) throw new ContractError("invalid_skill_id");
  return result;
}
export function skillVersion(value:unknown):string {
  const result=boundedString(value,32);
  if(!SKILL_VERSION.test(result)) throw new ContractError("invalid_skill_version");
  return result;
}
export function skillPath(value:unknown):string {
  const path=boundedString(value,240);
  const parts=path.split("/");
  if(!/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(path) || parts.some(p=>!p || p.startsWith(".") || p.endsWith(".") || /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(p))) throw new ContractError("skill_path_rejected");
  return path;
}
export async function sha256(raw:Uint8Array):Promise<string> {
  const hash=await crypto.subtle.digest("SHA-256",new Uint8Array(raw));
  return Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,"0")).join("");
}
export async function packageDigest(value:SkillPackage):Promise<string> {return sha256(new TextEncoder().encode(canonicalJSON(value)));}
function decodeBase64(value:unknown):Uint8Array {
  if(typeof value!=="string" || value.length>Math.ceil(SKILL_LIMITS.file_bytes/3)*4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw new ContractError("skill_encoding_rejected");
  const binary=atob(value);
  if(btoa(binary)!==value) throw new ContractError("skill_encoding_rejected");
  return Uint8Array.from(binary,c=>c.charCodeAt(0));
}
export async function parseSkillPackage(value:unknown):Promise<SkillPackage> {
  const obj=exact(value,["schema_version","skill_id","version","description","files","requires","derived_from"],["schema_version","skill_id","version","description","files","requires"]);
  if(obj.schema_version!=="1") throw new ContractError("invalid_skill_package");
  const id=skillId(obj.skill_id);skillVersion(obj.version);boundedString(obj.description,500);
  if(!Array.isArray(obj.files)||obj.files.length<1||obj.files.length>SKILL_LIMITS.files) throw new ContractError("skill_file_limit");
  const paths=new Set<string>(),prefixes=new Map<string,string>();let total=0,skillDocument:string|undefined,previousPath="";
  for(const entry of obj.files){
    const file=exact(entry,["path","content_base64","sha256","executable"]);
    const path=skillPath(file.path),folded=path.toLowerCase();
    if(path<=previousPath||(path.split("/").at(-1)?.toLowerCase()==="skill.md"&&path!=="SKILL.md"))throw new ContractError("skill_path_rejected");
    previousPath=path;
    const segments=path.split("/");for(let i=1;i<=segments.length;i++){const prefix=segments.slice(0,i).join("/"),foldedPrefix=prefix.toLowerCase();if(prefixes.has(foldedPrefix)&&prefixes.get(foldedPrefix)!==prefix)throw new ContractError("skill_path_collision");prefixes.set(foldedPrefix,prefix);}
    if(paths.has(folded)||typeof file.executable!=="boolean"||typeof file.sha256!=="string"||!HASH.test(file.sha256)) throw new ContractError("invalid_skill_file");
    paths.add(folded);
    const raw=decodeBase64(file.content_base64);total+=raw.byteLength;
    if(raw.byteLength>SKILL_LIMITS.file_bytes||total>SKILL_LIMITS.total_bytes) throw new ContractError("skill_size_limit");
    if(await sha256(raw)!==file.sha256) throw new ContractError("skill_hash_mismatch");
    if(path==="SKILL.md"){
      try{skillDocument=new TextDecoder("utf-8",{fatal:true,ignoreBOM:true}).decode(raw);}catch{throw new ContractError("skill_encoding_rejected");}
    }
  }
  for(const path of paths){const parts=path.split("/");for(let i=1;i<parts.length;i++)if(paths.has(parts.slice(0,i).join("/")))throw new ContractError("skill_path_collision");}
  // Require a simple portable frontmatter identity. Package content remains data.
  const front=skillDocument?.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1];
  const lines=front?.split(/\r?\n/);
  // YAML recognizes these additional line breaks; keep them from adding hidden header fields.
  if(!lines||lines.length!==2||lines.some(line=>/[\r\n\u0085\u2028\u2029]/u.test(line))||lines[0]!=="name: "+id||!lines[1].startsWith("description: ")||skillDocument?.includes("\0")) throw new ContractError("skill_document_invalid");
  const description=lines[1].slice(13);
  // Upstream frontmatter can request credentials; permit only these two fields.
  if(description.startsWith('"')){try{boundedString(JSON.parse(description),500);}catch{throw new ContractError("skill_document_invalid");}}
  else if(!/^[\p{L}_]/u.test(description)||/^(?:true|false|null|yes|no|on|off)$/i.test(description.trim())||description.includes(": ")||/[ \t]#/u.test(description)||[...description].length>500)throw new ContractError("skill_document_invalid");
  const required=exact(obj.requires,["connectors","connections"]);
  if(!Array.isArray(required.connectors)||required.connectors.length>20||!Array.isArray(required.connections)||required.connections.length>50) throw new ContractError("skill_requirements_invalid");
  const connectors=new Set<string>();
  for(const entry of required.connectors){const connector=exact(entry,["connector_id","version"]);const name=skillId(connector.connector_id);skillVersion(connector.version);if(connectors.has(name))throw new ContractError("skill_requirements_invalid");connectors.add(name);}
  const connections=new Set<string>();
  for(const connection of required.connections){if(typeof connection!=="string"||!UUID.test(connection)||connections.has(connection))throw new ContractError("skill_requirements_invalid");connections.add(connection);}
  if(obj.derived_from!==undefined){const derived=exact(obj.derived_from,["scope","skill_id","version","sha256"]);if(typeof derived.scope!=="string"||!["company","personal"].includes(derived.scope)||typeof derived.sha256!=="string"||!HASH.test(derived.sha256))throw new ContractError("skill_derivation_invalid");skillId(derived.skill_id);skillVersion(derived.version);}
  return obj as SkillPackage;
}
export async function parseSkillMutation(value:unknown):Promise<SkillMutation> {
  const obj=exact(value,["schema_version","update_id","base_revision","skill_id","package","resolves_update_id"],["schema_version","update_id","base_revision","skill_id","package"]);
  if(obj.schema_version!=="1"||typeof obj.update_id!=="string"||!UUID.test(obj.update_id)||!Number.isSafeInteger(obj.base_revision)||Number(obj.base_revision)<0) throw new ContractError("invalid_skill_update");
  skillId(obj.skill_id);
  if(obj.resolves_update_id!==undefined&&(typeof obj.resolves_update_id!=="string"||!UUID.test(obj.resolves_update_id))) throw new ContractError("invalid_resolution");
  if(obj.package!==null){const pkg=await parseSkillPackage(obj.package);if(pkg.skill_id!==obj.skill_id)throw new ContractError("skill_identity_mismatch");}
  return obj as SkillMutation;
}
export function parsePublication(value:unknown):SkillPublication {
  const obj=exact(value,["publication_id","revision","audience"]);
  if(typeof obj.publication_id!=="string"||!UUID.test(obj.publication_id)||!Number.isSafeInteger(obj.revision)||Number(obj.revision)<1)throw new ContractError("invalid_publication");
  const audience=exact(obj.audience,["kind","person_ids"],["kind"]);
  if(audience.kind==="all"||audience.kind==="none"){exact(audience,["kind"]);}else if(audience.kind==="people"){
    if(!Array.isArray(audience.person_ids)||audience.person_ids.length<1||audience.person_ids.length>1000)throw new ContractError("invalid_audience");
    const seen=new Set<string>();for(const id of audience.person_ids){const person=boundedString(id,100);if(seen.has(person))throw new ContractError("invalid_audience");seen.add(person);}
  }else throw new ContractError("invalid_audience");
  return obj as SkillPublication;
}
