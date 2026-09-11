/** Public control-plane metadata pagination; cursors select position, never identity scope. */
import {boundedString,canonicalJSON,ContractError,exact} from './index.ts';
import {slug,semver} from './connections.ts';
export const METADATA_PAGE_SIZE=100;
export type MetadataCursorKind='templates'|'content_editors';
function keys(kind:MetadataCursorKind,value:unknown):[string,string]{
  if(!Array.isArray(value)||value.length!==2)throw new ContractError('invalid_cursor');
  if(kind==='templates')return [slug(value[0]),semver(value[1])];
  const person=boundedString(value[0],128);
  if(value[1]!=='template'&&value[1]!=='skill')throw new ContractError('invalid_cursor');
  return [person,value[1]];
}
export function metadataCursor(kind:MetadataCursorKind,value:[string,string]):string{
  const raw=new TextEncoder().encode(canonicalJSON({kind,keys:keys(kind,value)}));
  return btoa(String.fromCharCode(...raw)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}
export function parseMetadataCursor(value:unknown,kind:MetadataCursorKind):[string,string]{
  try{
    if(typeof value!=='string'||value.length<1||value.length>1024||!/^[A-Za-z0-9_-]+$/.test(value))throw new Error();
    const bytes=Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
    const data=exact(JSON.parse(new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes)),['kind','keys']);
    if(data.kind!==kind)throw new Error();const result=keys(kind,data.keys);
    if(metadataCursor(kind,result)!==value)throw new Error();return result;
  }catch{throw new ContractError('invalid_cursor');}
}
