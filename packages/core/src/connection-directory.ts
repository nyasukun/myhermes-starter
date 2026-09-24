import {ContractError, exact, boundedString, UUID, slug, semver, parseMutation} from './index.ts';

export const CONNECTION_DIRECTORY_LIMIT = 100;
export const INTEGRATION_MAX_BYTES = 16000;
export const CONNECTION_REPORT_MAX_BYTES = 16384;
export const CONNECTION_REPORT_FRESH_SECONDS = 300;
export const MCP_SERVER_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
export const MCP_CONNECTION_STATES = ['connected','configured','connecting','not_connected','needs_auth','error','disabled','unknown'] as const;
export type MCPConnectionState = typeof MCP_CONNECTION_STATES[number];
export type MCPConnection = {server_name:string;status:MCPConnectionState};
export type ConnectionReport = {schema_version:'1';report_id:string;base_revision:number;companion_version:string;collection_status:'ok'|'unavailable';connections:MCPConnection[]};
export type Integration = {
  integration_id:string;display_name:string;service:string;required:boolean;enabled:boolean;
  source:{kind:'mcp';server_name:string}|{kind:'github';template_id:string};
  guide:{summary:string;steps:string[];url:string|null};
};
export const CONNECTION_DIRECTORY_MANIFEST = {
  version:'1.0.0', max_integrations:CONNECTION_DIRECTORY_LIMIT, max_integration_bytes:INTEGRATION_MAX_BYTES, max_reported_connections:CONNECTION_DIRECTORY_LIMIT,
  fresh_seconds:CONNECTION_REPORT_FRESH_SECONDS, evidence_source:'client_reported',
  report_fields:['schema_version','report_id','base_revision','companion_version','collection_status','connections.server_name','connections.status'],
  excluded:['credentials','endpoints','environment','arguments','tool_names','tool_results','raw_errors'],
  readers:['installation_owner','company_administrator'], retention:'latest_snapshot_per_installation',
} as const;
function revision(value:unknown) {if(!Number.isSafeInteger(value)||Number(value)<0||Number(value)>=Number.MAX_SAFE_INTEGER)throw new ContractError('invalid_revision');}
function serverName(value:unknown) {if(typeof value!=='string'||!MCP_SERVER_NAME.test(value))throw new ContractError('invalid_server_name');}
function text(value:unknown,max:number) {const result=boundedString(value,max);if(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(result))throw new ContractError('invalid_guide_text');return result;}
export function parseIntegration(value:unknown):Integration {
  const obj=exact(value,['integration_id','display_name','service','required','enabled','source','guide']);
  if(new TextEncoder().encode(JSON.stringify(obj)).byteLength>INTEGRATION_MAX_BYTES)throw new ContractError('integration_too_large');
  slug(obj.integration_id);text(obj.display_name,120);slug(obj.service);
  if(typeof obj.required!=='boolean'||typeof obj.enabled!=='boolean')throw new ContractError('invalid_choice');
  const source=exact(obj.source,['kind','server_name','template_id'],['kind']);
  if(source.kind==='mcp'){exact(source,['kind','server_name']);serverName(source.server_name);}
  else if(source.kind==='github'){exact(source,['kind','template_id']);slug(source.template_id);}
  else throw new ContractError('invalid_source');
  const guide=exact(obj.guide,['summary','steps','url']);text(guide.summary,2000);
  if(!Array.isArray(guide.steps)||guide.steps.length<1||guide.steps.length>12)throw new ContractError('invalid_guide_steps');
  for(const step of guide.steps)text(step,1000);
  if(guide.url!==null){
    const raw=boundedString(guide.url,2048);
    let url:URL;try{url=new URL(raw);}catch{throw new ContractError('invalid_guide_url');}
    if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||/[\s\\]/.test(raw))throw new ContractError('invalid_guide_url');
  }
  return obj as Integration;
}
export function parseIntegrationWrite(value:unknown) {
  const obj=exact(value,['schema_version','request_id','base_revision','integration']);
  parseMutation({schema_version:obj.schema_version,request_id:obj.request_id});revision(obj.base_revision);parseIntegration(obj.integration);
  return obj as {schema_version:'1';request_id:string;base_revision:number;integration:Integration};
}
export function parseConnectionReport(value:unknown):ConnectionReport {
  const obj=exact(value,['schema_version','report_id','base_revision','companion_version','collection_status','connections']);
  if(obj.schema_version!=='1'||typeof obj.report_id!=='string'||!UUID.test(obj.report_id))throw new ContractError('invalid_report');
  revision(obj.base_revision);semver(obj.companion_version);
  if(!['ok','unavailable'].includes(String(obj.collection_status))||typeof obj.collection_status!=='string')throw new ContractError('invalid_collection_status');
  if(!Array.isArray(obj.connections)||obj.connections.length>CONNECTION_DIRECTORY_LIMIT)throw new ContractError('invalid_connection_report');
  const names=new Set<string>();
  for(const item of obj.connections){const row=exact(item,['server_name','status']);serverName(row.server_name);
    if(typeof row.status!=='string'||!MCP_CONNECTION_STATES.includes(row.status as MCPConnectionState))throw new ContractError('invalid_connection_status');
    if(names.has(row.server_name as string))throw new ContractError('duplicate_connection');names.add(row.server_name as string);
  }
  if(obj.collection_status==='unavailable'&&obj.connections.length)throw new ContractError('invalid_connection_report');
  return obj as ConnectionReport;
}
export function mcpRequirementStatus(server:string,report:{received_at:string;collection_status:string;connections:MCPConnection[]}|null,now=Date.now()):string {
  if(!report)return 'unknown';
  const received=Date.parse(report.received_at);
  if(!Number.isFinite(received)||now-received>CONNECTION_REPORT_FRESH_SECONDS*1000||received>now)return 'stale';
  if(report.collection_status!=='ok')return 'unknown';
  return report.connections.find(row=>row.server_name===server)?.status??'not_connected';
}
