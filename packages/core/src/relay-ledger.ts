/** Every persistent relay column is specified here, in the public module. */
import {boundedString,canonicalJSON,ContractError,exact} from './index.ts';
import {relayDay,relayGenerationId,relayInteger,relayRequestId,relayRequestBirth,relayRequestAdmissible,RELAY_REQUEST_MAX_AGE_MS,RELAY_KNOWN_RETENTION_MS,type RelayObservation} from './relay.ts';
import {RELAY_RECONCILE_MIN_AGE_MS,type RelayReconciliation} from './relay-reconcile.ts';
export type RelaySql = {exec(query:string,...bindings:(string|number|null)[]):Iterable<Record<string,unknown>>};
export type RelayReceipt = {
  cursor:number; person_id:string; installation_id:string; request_id:string; server_request_id:string;
  alias:string; model:string; generation_model:string; provider:string; generation_id:string|null;
  returned_model:string|null; returned_provider:string|null; day:string; created_at:string; updated_at:string;
  reservation_nano:number; cost_nano:number|null; prompt_tokens:number|null; completion_tokens:number|null; total_tokens:number|null;
  state:'registered'|'completed'|'failed'|'cancelled'|'timeout';usage_state:'unknown'|'known';failure_code:string|null;
  usage_source:'response'|'generation'|null;reconciled_at:string|null;
};
export type RelayRegistration = Pick<RelayReceipt,'person_id'|'installation_id'|'request_id'|'alias'|'model'|'provider'|'reservation_nano'>&{request_hash:string;generation_model?:string};
// request_hash is an internal keyed, owner/request-scoped fingerprint. The
// explicit projection excludes it from all owner/admin/duplicate receipts.
const projection='cursor,person_id,installation_id,request_id,server_request_id,alias,model,generation_model,provider,generation_id,returned_model,returned_provider,day,created_at,updated_at,reservation_nano,cost_nano,prompt_tokens,completion_tokens,total_tokens,state,usage_state,failure_code,usage_source,reconciled_at';
function validateRegistration(value:RelayRegistration){
  exact(value,['person_id','installation_id','request_id','request_hash','alias','model','provider','reservation_nano','generation_model'],['person_id','installation_id','request_id','request_hash','alias','model','provider','reservation_nano']);
  boundedString(value.person_id,128);boundedString(value.installation_id,128);relayRequestId(value.request_id);
  if(typeof value.request_hash!=='string'||!/^[a-f0-9]{64}$/.test(value.request_hash)||typeof value.alias!=='string'||!/^[a-zA-Z0-9_-]{1,64}$/.test(value.alias))throw new ContractError('relay_invalid_registration');
  boundedString(value.model,128);boundedString(value.provider,64);relayInteger(value.reservation_nano,1,Number.MAX_SAFE_INTEGER);
  if(value.generation_model!==undefined)boundedString(value.generation_model,128);
}
function validateObservation(value:RelayObservation){
  exact(value,['state','failure_code','generation_id','returned_model','returned_provider','usage']);
  if(!['completed','failed','cancelled','timeout'].includes(value.state))throw new ContractError('relay_invalid_observation');
  if(value.generation_id!==null&&!relayGenerationId(value.generation_id))throw new ContractError('relay_invalid_observation');
  for(const label of [value.returned_model,value.returned_provider])if(label!==null)boundedString(label,128);
  const codes=['upstream_rejected','upstream_unavailable','upstream_invalid','upstream_policy_mismatch','stream_interrupted','client_cancelled','relay_timeout'];
  if((value.state==='completed'&&value.failure_code!==null)||(value.state!=='completed'&&(typeof value.failure_code!=='string'||!codes.includes(value.failure_code))))throw new ContractError('relay_invalid_observation');
  if(value.usage!==null){
    if(value.state!=='completed')throw new ContractError('relay_invalid_observation');
    const u=exact(value.usage,['prompt_tokens','completion_tokens','total_tokens','cost_nano']);
    const prompt=relayInteger(u.prompt_tokens,0,100_000_000),completion=relayInteger(u.completion_tokens,0,100_000_000);
    if(relayInteger(u.total_tokens,0,200_000_000)!==prompt+completion)throw new ContractError('relay_invalid_observation');
    relayInteger(u.cost_nano,0,1_000_000_000_000_000);
  }
}
export class PublicRelayLedger {
  private sql:RelaySql;private transaction:<T>(fn:()=>T)=>T;
  constructor(sql:RelaySql,transaction:<T>(fn:()=>T)=>T){this.sql=sql;this.transaction=transaction;
    sql.exec(`CREATE TABLE IF NOT EXISTS relay_requests(cursor INTEGER PRIMARY KEY AUTOINCREMENT,person_id TEXT NOT NULL,installation_id TEXT NOT NULL,request_id TEXT NOT NULL,server_request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,alias TEXT NOT NULL,model TEXT NOT NULL,provider TEXT NOT NULL,generation_id TEXT,returned_model TEXT,returned_provider TEXT,day TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reservation_nano INTEGER NOT NULL,cost_nano INTEGER,prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,state TEXT NOT NULL,usage_state TEXT NOT NULL,failure_code TEXT,finish_value TEXT,UNIQUE(person_id,request_id))`);
    sql.exec('CREATE INDEX IF NOT EXISTS relay_day ON relay_requests(day)');
    const columns=new Set(Array.from(sql.exec('PRAGMA table_info(relay_requests)')).map(row=>row.name));
    for(const name of ['usage_source','reconciled_at','reconcile_value','generation_model'])if(!columns.has(name))sql.exec(`ALTER TABLE relay_requests ADD COLUMN ${name} TEXT`);
    sql.exec('UPDATE relay_requests SET generation_model=model WHERE generation_model IS NULL');
    sql.exec("UPDATE relay_requests SET usage_source='response' WHERE usage_state='known' AND usage_source IS NULL");
    sql.exec('CREATE TABLE IF NOT EXISTS relay_settings(id INTEGER PRIMARY KEY CHECK(id=1),frozen INTEGER NOT NULL)');
    sql.exec('INSERT OR IGNORE INTO relay_settings(id,frozen) VALUES(1,0)');
    if(!columns.has('retire_after_ms'))sql.exec('ALTER TABLE relay_requests ADD COLUMN retire_after_ms INTEGER');
    sql.exec("CREATE INDEX IF NOT EXISTS relay_retention ON relay_requests(retire_after_ms) WHERE usage_state='known'");
    const settings=new Set(Array.from(sql.exec('PRAGMA table_info(relay_settings)')).map(row=>row.name));
    if(!settings.has('clock_floor_ms'))sql.exec('ALTER TABLE relay_settings ADD COLUMN clock_floor_ms INTEGER NOT NULL DEFAULT 0');
    if(!settings.has('history_complete_since'))sql.exec('ALTER TABLE relay_settings ADD COLUMN history_complete_since TEXT');
    this.transaction(()=>{
      // Older standalone adapters could store mixed case although the Private
      // route already normalized it. Never turn one UUID into an absent ID.
      // Conflicting historical case variants fail closed with both receipts intact.
      try{sql.exec('UPDATE relay_requests SET request_id=lower(request_id) WHERE request_id<>lower(request_id)');}
      catch{throw new ContractError('relay_retention_corrupt');}
      const times=this.rows('SELECT MAX(created_at) AS created,MAX(updated_at) AS updated FROM relay_requests')[0];
      const latest=Math.max(0,...[times.created,times.updated].filter(value=>value!==null).map(value=>this.timestamp(value)));
      this.clock(latest);
      // Migration is metadata-only and bounded by the pre-existing 100,000-row cap.
      for(const row of this.rows("SELECT cursor,request_id,updated_at FROM relay_requests WHERE usage_state='known' AND retire_after_ms IS NULL")){
        sql.exec('UPDATE relay_requests SET retire_after_ms=? WHERE cursor=?',this.deadline(String(row.request_id),this.timestamp(row.updated_at)),Number(row.cursor));
      }
    });
  }
  private rows(query:string,...bindings:(string|number|null)[]){return Array.from(this.sql.exec(query,...bindings));}
  receipt(person:string,id:string):RelayReceipt|null{id=relayRequestId(id);return (this.rows(`SELECT ${projection} FROM relay_requests WHERE person_id=? AND request_id=?`,person,id)[0] as RelayReceipt|undefined)??null;}
  serverReceipt(id:string):RelayReceipt|null{id=relayRequestId(id);return (this.rows(`SELECT ${projection} FROM relay_requests WHERE server_request_id=?`,id)[0] as RelayReceipt|undefined)??null;}
  checkpoint(id:string,generation:string,now=Date.now()):boolean{
    id=relayRequestId(id);if(!relayGenerationId(generation))throw new ContractError('relay_invalid_observation');
    return this.transaction(()=>{
      now=this.clock(now);
      const row=this.rows('SELECT generation_id,state FROM relay_requests WHERE server_request_id=?',id)[0];
      if(!row)return false;if(row.generation_id!==null)return row.generation_id===generation;
      if(row.state!=='registered')return false;
      this.sql.exec('UPDATE relay_requests SET generation_id=?,updated_at=? WHERE server_request_id=?',generation,new Date(now).toISOString(),id);return true;
    });
  }
  summary(day:string,budget:number){
    const row=this.rows('SELECT COUNT(*) AS requests,COALESCE(SUM(CASE WHEN usage_state=\'known\' THEN cost_nano ELSE 0 END),0) AS known_cost_nano,SUM(CASE WHEN usage_state=\'unknown\' THEN 1 ELSE 0 END) AS unknown_requests FROM relay_requests WHERE day=?',day)[0];
    const reserved=Number(this.rows('SELECT COALESCE(SUM(reservation_nano),0) AS value FROM relay_requests WHERE usage_state=\'unknown\'')[0].value);
    const known=Number(row.known_cost_nano),frozen=Boolean(this.rows('SELECT frozen FROM relay_settings WHERE id=1')[0].frozen);
    const retention=this.rows('SELECT clock_floor_ms,history_complete_since FROM relay_settings WHERE id=1')[0];
    return {day,admission_day:relayDay(Number(retention.clock_floor_ms)),history_complete_since:retention.history_complete_since as string|null,timezone:'Asia/Tokyo',daily_budget_nano:budget,requests:Number(row.requests),known_cost_nano:known,unknown_requests:Number(row.unknown_requests??0),outstanding_reservation_nano:reserved,available_nano:Math.max(0,budget-known-reserved),frozen};
  }
  register(input:RelayRegistration,budget:number,now=Date.now()):{kind:'accepted'|'duplicate'|'reused'|'budget'|'frozen'|'capacity'|'expired';receipt?:RelayReceipt}{
    validateRegistration(input);input={...input,request_id:relayRequestId(input.request_id)};relayInteger(budget,1,1_000_000_000_000);
    return this.transaction(()=>{
      now=this.clock(now);
      const existing=this.rows('SELECT request_hash FROM relay_requests WHERE person_id=? AND request_id=?',input.person_id,input.request_id)[0];
      if(existing)return {kind:existing.request_hash===input.request_hash?'duplicate':'reused',receipt:this.receipt(input.person_id,input.request_id)!};
      if(!relayRequestAdmissible(input.request_id,now))return {kind:'expired'};
      this.purgeKnown(now,100);
      const day=relayDay(now),totals=this.summary(day,budget);
      if(totals.frozen)return {kind:'frozen'};
      if(input.reservation_nano>totals.available_nano)return {kind:'budget'};
      if(Number(this.rows('SELECT COUNT(*) AS n FROM relay_requests')[0].n)>=100000)return {kind:'capacity'};
      const timestamp=new Date(now).toISOString(),id=crypto.randomUUID();
      this.sql.exec(`INSERT INTO relay_requests(person_id,installation_id,request_id,server_request_id,request_hash,alias,model,generation_model,provider,day,created_at,updated_at,reservation_nano,state,usage_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'registered','unknown')`,input.person_id,input.installation_id,input.request_id,id,input.request_hash,input.alias,input.model,input.generation_model??input.model,input.provider,day,timestamp,timestamp,input.reservation_nano);
      return {kind:'accepted',receipt:this.receipt(input.person_id,input.request_id)!};
    });
  }
  finish(id:string,observation:RelayObservation,now=Date.now()):boolean{
    id=relayRequestId(id);validateObservation(observation);
    return this.transaction(()=>{
      now=this.clock(now);
      const row=this.rows('SELECT * FROM relay_requests WHERE server_request_id=?',id)[0];if(!row)return false;
      const finish=canonicalJSON(observation);if(row.finish_value!==null)return row.finish_value===finish;
      if(row.generation_id!==null&&row.generation_id!==observation.generation_id)return false;
      const u=observation.usage;
      if(row.usage_source==='generation'){
        if((u&&(['cost_nano','prompt_tokens','completion_tokens','total_tokens'] as const).some(key=>row[key]!==u[key]))
          ||(observation.returned_model!==null&&![row.returned_model,row.model,row.generation_model].includes(observation.returned_model))
          ||(observation.returned_provider!==null&&String(row.returned_provider).toLowerCase()!==observation.returned_provider.toLowerCase()&&String(row.provider).toLowerCase()!==observation.returned_provider.toLowerCase()))return false;
        this.sql.exec('UPDATE relay_requests SET updated_at=?,state=?,failure_code=?,finish_value=? WHERE server_request_id=?',new Date(now).toISOString(),observation.state,observation.failure_code,finish,id);
      }else this.sql.exec('UPDATE relay_requests SET updated_at=?,generation_id=?,returned_model=?,returned_provider=?,cost_nano=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,state=?,usage_state=?,usage_source=?,failure_code=?,finish_value=? WHERE server_request_id=?',new Date(now).toISOString(),observation.generation_id,observation.returned_model,observation.returned_provider,u?.cost_nano??null,u?.prompt_tokens??null,u?.completion_tokens??null,u?.total_tokens??null,observation.state,u?'known':'unknown',u?'response':null,observation.failure_code,finish,id);
      this.sql.exec('UPDATE relay_requests SET retire_after_ms=? WHERE server_request_id=?',u||row.usage_source==='generation'?this.deadline(String(row.request_id),now):null,id);
      if(u&&u.cost_nano>Number(row.reservation_nano))this.sql.exec('UPDATE relay_settings SET frozen=1 WHERE id=1');
      return true;
    });
  }
  reconcile(id:string,value:RelayReconciliation,now=Date.now()):{kind:'settled'|'duplicate'|'conflict'|'unavailable';receipt?:RelayReceipt}{
    id=relayRequestId(id);exact(value,['generation_id','returned_model','returned_provider','usage']);
    if(!value.usage||!value.generation_id||!value.returned_model||!value.returned_provider)throw new ContractError('relay_invalid_observation');
    validateObservation({state:'completed',failure_code:null,...value});
    return this.transaction(()=>{
      now=this.clock(now);
      const row=this.serverReceipt(id);
      if(!row||row.generation_id!==value.generation_id||now-Date.parse(row.created_at)<RELAY_RECONCILE_MIN_AGE_MS)return {kind:'unavailable'};
      if(![row.model,row.returned_model,row.generation_model].includes(value.returned_model)||(value.returned_provider!==row.returned_provider&&value.returned_provider.toLowerCase()!==row.provider.toLowerCase()))return {kind:'conflict'};
      const u=value.usage;
      if(u.cost_nano>row.reservation_nano)this.sql.exec('UPDATE relay_settings SET frozen=1 WHERE id=1');
      if(row.usage_state==='known')return {kind:(['cost_nano','prompt_tokens','completion_tokens','total_tokens'] as const).every(key=>row[key]===u[key])?'duplicate':'conflict',receipt:row};
      const timestamp=new Date(now).toISOString();
      this.sql.exec("UPDATE relay_requests SET updated_at=?,cost_nano=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,returned_model=?,returned_provider=?,usage_state='known',usage_source='generation',reconciled_at=?,reconcile_value=? WHERE server_request_id=?",timestamp,u.cost_nano,u.prompt_tokens,u.completion_tokens,u.total_tokens,value.returned_model,value.returned_provider,timestamp,canonicalJSON(value),id);
      this.sql.exec('UPDATE relay_requests SET retire_after_ms=? WHERE server_request_id=?',this.deadline(row.request_id,now),id);
      return {kind:'settled',receipt:this.serverReceipt(id)!};
    });
  }
  private timestamp(value:unknown):number {
    const parsed=typeof value==='string'?Date.parse(value):NaN;
    if(!Number.isSafeInteger(parsed)||parsed<0)throw new ContractError('relay_retention_corrupt');return parsed;
  }
  private clock(now:number):number {
    relayInteger(now,0,8640000000000000);
    const floor=Number(this.rows('SELECT clock_floor_ms FROM relay_settings WHERE id=1')[0].clock_floor_ms);
    relayInteger(floor,0,8640000000000000);const effective=Math.max(now,floor);
    if(effective!==floor)this.sql.exec('UPDATE relay_settings SET clock_floor_ms=? WHERE id=1',effective);return effective;
  }
  private deadline(id:string,updated:number):number {
    const birth=relayRequestBirth(id);return Math.max(updated+RELAY_KNOWN_RETENTION_MS,birth===null?0:birth+RELAY_REQUEST_MAX_AGE_MS+1);
  }
  private nextDeadline():number|null {
    const value=this.rows("SELECT MIN(retire_after_ms) AS value FROM relay_requests WHERE usage_state='known'")[0].value;
    return value===null?null:Number(value);
  }
  retentionNext(now=Date.now()):number|null{return this.transaction(()=>{this.clock(now);return this.nextDeadline();});}
  private purgeKnown(now:number,limit:number):{deleted:number;more:boolean;next_due:number|null} {
    const rows=this.rows("SELECT cursor,created_at FROM relay_requests WHERE usage_state='known' AND retire_after_ms<=? ORDER BY retire_after_ms,cursor LIMIT ?",now,limit);
    if(rows.length){
      const prior=this.rows('SELECT history_complete_since FROM relay_settings WHERE id=1')[0].history_complete_since;
      const complete=Math.max(prior===null?0:this.timestamp(prior),...rows.map(row=>this.timestamp(row.created_at)+1));
      // Same transaction as deletion: a rollback cannot forget either the floor or coverage boundary.
      this.sql.exec('UPDATE relay_settings SET history_complete_since=? WHERE id=1',new Date(complete).toISOString());
      for(const row of rows)this.sql.exec("DELETE FROM relay_requests WHERE cursor=? AND usage_state='known'",Number(row.cursor));
    }
    const next=this.nextDeadline();return {deleted:rows.length,more:next!==null&&next<=now,next_due:next};
  }
  purge(now=Date.now(),limit=100):{deleted:number;more:boolean;next_due:number|null}{
    relayInteger(limit,1,1000);return this.transaction(()=>this.purgeKnown(this.clock(now),limit));
  }
  list(before=Number.MAX_SAFE_INTEGER){const rows=this.rows(`SELECT ${projection} FROM relay_requests WHERE cursor<? ORDER BY cursor DESC LIMIT 101`,before) as RelayReceipt[];return {requests:rows.slice(0,100),next_before:rows.length>100?rows[99].cursor:null};}
}
