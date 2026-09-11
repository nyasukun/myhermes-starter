import {ContractError, UUID, boundedString, exact} from './index.ts';

export const CONNECTOR_VERSION = '1.0.0';
export const GITHUB_API_VERSION = '2026-03-10';
export const CAPABILITIES = ['repository.read', 'issues.read'] as const;
export const PERMISSIONS = ['metadata:read', 'issues:read'] as const;
export type Capability = typeof CAPABILITIES[number];
export type Permission = typeof PERMISSIONS[number];
export type GithubResource = {kind: 'github_repository'; owner: string; repository: string};
export type ResourceRule = {owner: string; repository: string};
export type ServiceTemplate = {
  schema_version: '1'; template_id: string; version: string; display_name: string; description: string;
  connector_id: 'github'; connector_version: '1.0.0';
  service: {base_url: 'https://api.github.com'; api_version: '2026-03-10'; allowed_hosts: ['api.github.com']};
  auth: {method: 'github_fine_grained_pat'; required_permissions: Permission[]};
  supported_os: ('ubuntu'|'macos')[];
  input_fields: [{name: 'resources'; type: 'github_repository_list'; required: true}];
  setup: {procedure_id: 'github-fine-grained-pat-v1'; procedure_version: '1.0.0'};
  connection_test: {procedure_id: 'github-read-test-v1'; required_capabilities: Capability[]};
  capabilities: Capability[]; resource_rules: ResourceRule[];
  related_skills: {skill_id: string; version: string}[];
  usage_notice: {version: string; text: string};
};
export type UsageGrantInput = {notice_version: string; accepted: true; operations: Capability[]; resources: GithubResource[]};
export type ConnectionCreate = {
  schema_version: '1'; request_id: string; template_id: string; template_version: string;
  account_kind: 'company'|'client'|'personal'; display_name: string;
  account: {provider_account_id: string; login: string};
  management: {kind: 'company'|'client'|'individual'; label: string};
  project: {id: string; label: string}|null; resources: GithubResource[]; usage_grant: UsageGrantInput;
};
export type GrantUpdate = UsageGrantInput & {schema_version: '1'; request_id: string; base_revision: number};
export const BINDING_ERRORS = ['auth_rejected','resource_forbidden','network_error','account_mismatch','credential_unavailable'] as const;
export type BindingInput = {
  schema_version: '1'; request_id: string; grant_revision: number; status: 'ready'|'needs_auth'|'error';
  verified_account_id: string|null; requested_permissions: Permission[]; tested_capabilities: Capability[];
  tested_resources: GithubResource[]; error_code: typeof BINDING_ERRORS[number]|null;
};
export const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
export const SEMVER = /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/;
export function slug(value: unknown): string {if (typeof value !== 'string' || !SLUG.test(value)) throw new ContractError('invalid_identifier'); return value;}
export function semver(value: unknown): string {if (typeof value !== 'string' || !SEMVER.test(value)) throw new ContractError('invalid_version'); return value;}
function oneOf<T extends string>(value: unknown, choices: readonly T[]): T {if (typeof value !== 'string' || !choices.includes(value as T)) throw new ContractError('invalid_choice');return value as T;}
function equal(value: unknown, expected: unknown) {if (value !== expected) throw new ContractError('invalid_fixed_value');}
function values<T extends string>(value: unknown, allowed: readonly T[], min=1): T[] {
  if (!Array.isArray(value) || value.length < min || value.length > allowed.length || new Set(value).size !== value.length) throw new ContractError('invalid_list');
  return value.map(v=>oneOf(v,allowed));
}
export function githubOwner(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(value)) throw new ContractError('invalid_github_owner');return value;
}
export function githubRepository(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(value) || ['.','..'].includes(value)) throw new ContractError('invalid_github_repository');return value;
}
function accountId(value: unknown): string {if (typeof value !== 'string' || !/^[1-9][0-9]{0,19}$/.test(value)) throw new ContractError('invalid_provider_account');return value;}
export const resourceKey = (resource: Pick<GithubResource,'owner'|'repository'>) => `${resource.owner}/${resource.repository}`.toLowerCase();
export function parseResources(value: unknown, min=1): GithubResource[] {
  if (!Array.isArray(value) || value.length < min || value.length > 50) throw new ContractError('invalid_resources');
  const resources = value.map(v=>{const r=exact(v,['kind','owner','repository']);equal(r.kind,'github_repository');return {kind:'github_repository' as const,owner:githubOwner(r.owner),repository:githubRepository(r.repository)};});
  if(new Set(resources.map(resourceKey)).size!==resources.length) throw new ContractError('duplicate_resource');
  return resources;
}
function envelope(obj: Record<string,unknown>) {
  equal(obj.schema_version,'1');if(typeof obj.request_id!=='string'||!UUID.test(obj.request_id)) throw new ContractError('invalid_request_id');
}
export function parseMutation(value: unknown) {const obj=exact(value,['schema_version','request_id']);envelope(obj);return obj as {schema_version:'1';request_id:string};}
export function parseTemplate(value: unknown): ServiceTemplate {
  const obj=exact(value,['schema_version','template_id','version','display_name','description','connector_id','connector_version','service','auth','supported_os','input_fields','setup','connection_test','capabilities','resource_rules','related_skills','usage_notice']);
  equal(obj.schema_version,'1');slug(obj.template_id);semver(obj.version);boundedString(obj.display_name,120);boundedString(obj.description,2000,0);equal(obj.connector_id,'github');equal(obj.connector_version,CONNECTOR_VERSION);
  const service=exact(obj.service,['base_url','api_version','allowed_hosts']);equal(service.base_url,'https://api.github.com');equal(service.api_version,GITHUB_API_VERSION);
  if(!Array.isArray(service.allowed_hosts)||service.allowed_hosts.length!==1||service.allowed_hosts[0]!=='api.github.com') throw new ContractError('invalid_service_host');
  const auth=exact(obj.auth,['method','required_permissions']);equal(auth.method,'github_fine_grained_pat');const permissions=values(auth.required_permissions,PERMISSIONS);
  values(obj.supported_os,['ubuntu','macos']);
  if(!Array.isArray(obj.input_fields)||obj.input_fields.length!==1) throw new ContractError('invalid_input_fields');
  const field=exact(obj.input_fields[0],['name','type','required']);equal(field.name,'resources');equal(field.type,'github_repository_list');equal(field.required,true);
  const setup=exact(obj.setup,['procedure_id','procedure_version']);equal(setup.procedure_id,'github-fine-grained-pat-v1');equal(setup.procedure_version,'1.0.0');
  const test=exact(obj.connection_test,['procedure_id','required_capabilities']);equal(test.procedure_id,'github-read-test-v1');const tested=values(test.required_capabilities,CAPABILITIES);
  const caps=values(obj.capabilities,CAPABILITIES);
  if(!caps.includes('repository.read')||caps.length!==tested.length||caps.some(c=>!tested.includes(c))||!permissions.includes('metadata:read')||permissions.includes('issues:read')!==caps.includes('issues.read')) throw new ContractError('inconsistent_capabilities');
  if(!Array.isArray(obj.resource_rules)||obj.resource_rules.length<1||obj.resource_rules.length>50) throw new ContractError('invalid_resource_rules');
  for(const value of obj.resource_rules){const rule=exact(value,['owner','repository']);if(rule.owner!=='*')githubOwner(rule.owner);if(rule.repository!=='*')githubRepository(rule.repository);}
  if(new Set(obj.resource_rules.map(r=>resourceKey(r as ResourceRule))).size!==obj.resource_rules.length)throw new ContractError('duplicate_resource_rule');
  if(!Array.isArray(obj.related_skills)||obj.related_skills.length>20)throw new ContractError('invalid_skill_references');
  for(const value of obj.related_skills){const skill=exact(value,['skill_id','version']);slug(skill.skill_id);semver(skill.version);}
  const notice=exact(obj.usage_notice,['version','text']);if(typeof notice.version!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$/.test(notice.version))throw new ContractError('invalid_notice_version');boundedString(notice.text,4000);
  return obj as ServiceTemplate;
}
export function parseGrant(value: unknown): UsageGrantInput {
  const obj=exact(value,['notice_version','accepted','operations','resources']);equal(obj.accepted,true);boundedString(obj.notice_version,32);values(obj.operations,CAPABILITIES);parseResources(obj.resources);return obj as UsageGrantInput;
}
export function parseConnectionCreate(value: unknown): ConnectionCreate {
  const obj=exact(value,['schema_version','request_id','template_id','template_version','account_kind','display_name','account','management','project','resources','usage_grant']);envelope(obj);slug(obj.template_id);semver(obj.template_version);oneOf(obj.account_kind,['company','client','personal']);boundedString(obj.display_name,120);
  const account=exact(obj.account,['provider_account_id','login']);accountId(account.provider_account_id);githubOwner(account.login);
  const management=exact(obj.management,['kind','label']);oneOf(management.kind,['company','client','individual']);boundedString(management.label,120);
  if(obj.project!==null){const project=exact(obj.project,['id','label']);slug(project.id);boundedString(project.label,120);}
  parseResources(obj.resources);parseGrant(obj.usage_grant);return obj as ConnectionCreate;
}
export function parseGrantUpdate(value: unknown): GrantUpdate {
  const obj=exact(value,['schema_version','request_id','base_revision','notice_version','accepted','operations','resources']);envelope(obj);
  if(!Number.isSafeInteger(obj.base_revision)||Number(obj.base_revision)<1)throw new ContractError('invalid_revision');
  parseGrant({notice_version:obj.notice_version,accepted:obj.accepted,operations:obj.operations,resources:obj.resources});return obj as GrantUpdate;
}
export function resourceAllowed(resource: GithubResource, rules: ResourceRule[]): boolean {return rules.some(r=>(r.owner==='*'||r.owner.toLowerCase()===resource.owner.toLowerCase())&&(r.repository==='*'||r.repository.toLowerCase()===resource.repository.toLowerCase()));}
export function assertGrantAllowed(template: ServiceTemplate, connectionResources: GithubResource[], grant: UsageGrantInput) {
  if(grant.notice_version!==template.usage_notice.version)throw new ContractError('notice_version_mismatch');
  if(connectionResources.some(r=>!resourceAllowed(r,template.resource_rules))||grant.operations.some(op=>!template.capabilities.includes(op))||grant.resources.some(r=>!connectionResources.some(c=>resourceKey(c)===resourceKey(r))))throw new ContractError('grant_outside_template');
}
export function parseBinding(value: unknown): BindingInput {
  const obj=exact(value,['schema_version','request_id','grant_revision','status','verified_account_id','requested_permissions','tested_capabilities','tested_resources','error_code']);envelope(obj);
  if(!Number.isSafeInteger(obj.grant_revision)||Number(obj.grant_revision)<1)throw new ContractError('invalid_revision');
  oneOf(obj.status,['ready','needs_auth','error']);values(obj.requested_permissions,PERMISSIONS);const tested=values(obj.tested_capabilities,CAPABILITIES,0);const resources=parseResources(obj.tested_resources,0);
  if(obj.error_code!==null)oneOf(obj.error_code,BINDING_ERRORS);
  if(obj.status==='ready'){accountId(obj.verified_account_id);if(obj.error_code!==null||!tested.length||!resources.length)throw new ContractError('invalid_ready_binding');}
  else if(obj.verified_account_id!==null||tested.length||resources.length||(obj.status==='error'&&obj.error_code===null))throw new ContractError('invalid_inactive_binding');
  return obj as BindingInput;
}
export function assertBindingAllowed(template: ServiceTemplate, account_id: string, grant: UsageGrantInput & {revision:number}, binding: BindingInput) {
  if(binding.grant_revision!==grant.revision)throw new ContractError('stale_grant');
  if(binding.requested_permissions.length!==template.auth.required_permissions.length||binding.requested_permissions.some(p=>!template.auth.required_permissions.includes(p)))throw new ContractError('permission_mismatch');
  if(binding.status==='ready'&&(binding.verified_account_id!==account_id||grant.operations.some(op=>!binding.tested_capabilities.includes(op))||grant.resources.some(r=>!binding.tested_resources.some(t=>resourceKey(r)===resourceKey(t)))||binding.tested_resources.some(r=>!grant.resources.some(g=>resourceKey(g)===resourceKey(r)))||binding.tested_capabilities.some(op=>!grant.operations.includes(op))))throw new ContractError('binding_verification_mismatch');
}
