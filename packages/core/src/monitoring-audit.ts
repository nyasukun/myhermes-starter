import {canonicalJSON,ContractError,exact} from './index.ts';
import {MONITORING_LIMITS,parseAuditBatch,telemetryId,type AuditBatch} from './monitoring.ts';
function decode(value:string):Uint8Array<ArrayBuffer>{
  if(!/^[A-Za-z0-9_-]+$/.test(value)||value.length%4===1)throw new ContractError('monitoring_signature');
  let binary:string;try{binary=atob(value.replaceAll('-','+').replaceAll('_','/'));}catch{throw new ContractError('monitoring_signature');}
  const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
  if(btoa(binary).replaceAll('+','-').replaceAll('/','_').replaceAll('=','')!==value)throw new ContractError('monitoring_signature');return bytes;
}
function json(value:string):unknown{try{return JSON.parse(new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(decode(value)));}catch{throw new ContractError('monitoring_signature');}}
export async function verifyAuditJWS(jws:string,publicJwk:JsonWebKey,installationId:string,now=Date.now()):Promise<{batch:AuditBatch;signature:string}>{
  if(typeof jws!=='string'||new TextEncoder().encode(jws).length>MONITORING_LIMITS.body_bytes)throw new ContractError('monitoring_size');
  const parts=jws.split('.');if(parts.length!==3)throw new ContractError('monitoring_signature');
  const header=exact(json(parts[0]),['alg','typ','kid']);if(header.alg!=='ES256'||header.typ!=='myhermes-audit+jws'||header.kid!==telemetryId(installationId))throw new ContractError('monitoring_signature');
  const signature=decode(parts[2]);if(signature.length!==64)throw new ContractError('monitoring_signature');
  try{const key=await crypto.subtle.importKey('jwk',publicJwk,{name:'ECDSA',namedCurve:'P-256'},false,['verify']);if(!await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},key,signature,new TextEncoder().encode(parts[0]+'.'+parts[1])))throw new Error('invalid');}catch{throw new ContractError('monitoring_signature');}
  const batch=parseAuditBatch(json(parts[1]),now);
  if(new TextDecoder().decode(decode(parts[1]))!==canonicalJSON(batch))throw new ContractError('monitoring_canonical');
  return {batch,signature:parts[2]};
}
