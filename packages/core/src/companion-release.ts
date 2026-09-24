import {ContractError,exact,semver,parseMutation} from './index.ts';
export type CompanionRelease={version:string;wheel_url:string;sha256:string};
export function parseCompanionRelease(value:unknown):CompanionRelease {
  const obj=exact(value,['version','wheel_url','sha256']);const version=semver(obj.version);
  if(obj.wheel_url!==`https://github.com/nyasukun/myhermes-starter/releases/download/v${version}/myhermes_companion-${version}-py3-none-any.whl`)throw new ContractError('invalid_release_url');
  if(typeof obj.sha256!=='string'||!/^[a-f0-9]{64}$/.test(obj.sha256))throw new ContractError('invalid_release_hash');
  return obj as CompanionRelease;
}
export function parseCompanionReleaseWrite(value:unknown){
  const obj=exact(value,['schema_version','request_id','base_revision','release']);
  parseMutation({schema_version:obj.schema_version,request_id:obj.request_id});
  if(!Number.isSafeInteger(obj.base_revision)||Number(obj.base_revision)<0||Number(obj.base_revision)>=Number.MAX_SAFE_INTEGER)throw new ContractError('invalid_revision');
  if(obj.release!==null)parseCompanionRelease(obj.release);
  return obj as {schema_version:'1';request_id:string;base_revision:number;release:CompanionRelease|null};
}
