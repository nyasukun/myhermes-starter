import {ContractError, exact, UUID} from './index.ts';

export type MemberRole = 'member' | 'admin';
export type MemberCreate = {schema_version:'1'; request_id:string; access_subject:string; role:MemberRole};
export type MemberUpdate = {schema_version:'1'; request_id:string; base_revision:number; role:MemberRole; active:boolean};
export type MemberMetadata = {person_id:string; access_subject:string; role:MemberRole; active:boolean; revision:number; created_at:string; updated_at:string};
export const MEMBER_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

function common(obj:Record<string, unknown>) {
  if(obj.schema_version!=='1' || typeof obj.request_id!=='string' || !UUID.test(obj.request_id)
    || typeof obj.role!=='string' || !['member','admin'].includes(obj.role)) throw new ContractError('invalid_member_mutation');
}
export function parseMemberCreate(value:unknown):MemberCreate {
  const obj=exact(value,['schema_version','request_id','access_subject','role']);
  common(obj);
  if(typeof obj.access_subject!=='string' || !UUID.test(obj.access_subject)) throw new ContractError('invalid_access_subject');
  return obj as MemberCreate;
}
export function parseMemberUpdate(value:unknown):MemberUpdate {
  const obj=exact(value,['schema_version','request_id','base_revision','role','active']);
  common(obj);
  if(typeof obj.active!=='boolean' || !Number.isSafeInteger(obj.base_revision) || Number(obj.base_revision)<1 || Number(obj.base_revision)>=Number.MAX_SAFE_INTEGER) throw new ContractError('invalid_member_update');
  return obj as MemberUpdate;
}
