/** Public storage/projections/aggregation; private wrappers add no collection fields. */
import {boundedString,canonicalJSON,ContractError,exact} from './index.ts';
import {MONITORING_LIMITS,MONITORING_READ_LIMITS,MONITORING_VERSION,monitoringHash,parseAuditBatch,parseAuditEvent,telemetryError,telemetryHex,telemetryId,telemetryInteger,telemetryVersion,type AuditBatch,type TelemetrySpan} from './monitoring.ts';
import type {RelaySql} from './relay-ledger.ts';
export type MonitoringSubject={person_id:string;installation_id:string};
export const MONITORING_REJECTIONS=['monitoring_invalid','monitoring_age','monitoring_version','monitoring_size','monitoring_signature','monitoring_canonical','monitoring_chain','monitoring_reused','monitoring_capacity'] as const;
export type MonitoringRejection=typeof MONITORING_REJECTIONS[number];
export type MonitoringReceipt={status:'accepted'|'duplicate';accepted:number};
function subject(value:MonitoringSubject){exact(value,['person_id','installation_id']);boundedString(value.person_id,128);telemetryId(value.installation_id);}
function span(value:TelemetrySpan,now:number){
  exact(value,['trace_id','span_id','parent_span_id','start_time_unix_nano','end_time_unix_nano','status_code','client_version','event']);
  const e=parseAuditEvent(value.event,now);if(e.trace_id!==value.trace_id||e.span_id!==value.span_id)telemetryError();
  telemetryHex(value.trace_id,16);telemetryHex(value.span_id,8);if(value.parent_span_id!==null)telemetryHex(value.parent_span_id,8);telemetryVersion(value.client_version);telemetryInteger(value.status_code,2);
  for(const t of [value.start_time_unix_nano,value.end_time_unix_nano])if(typeof t!=='string'||!/^\d{1,20}$/.test(t))telemetryError();
  const start=BigInt(value.start_time_unix_nano),end=BigInt(value.end_time_unix_nano);if(start===0n||end<start||end-start>86400000000000n||start/1000000n<BigInt(now-30*86400000)||end/1000000n>BigInt(now+300000))telemetryError();
}
export class PublicMonitoringStore{
  private sql:RelaySql;private transaction:<T>(fn:()=>T)=>T;
  constructor(sql:RelaySql,transaction:<T>(fn:()=>T)=>T){this.sql=sql;this.transaction=transaction;
    sql.exec('CREATE TABLE IF NOT EXISTS monitoring_subject(id INTEGER PRIMARY KEY CHECK(id=1),person_id TEXT NOT NULL,installation_id TEXT NOT NULL)');
    sql.exec('CREATE TABLE IF NOT EXISTS monitoring_streams(stream_id TEXT PRIMARY KEY,sequence INTEGER NOT NULL,batch_sha256 TEXT NOT NULL,received_at INTEGER NOT NULL)');
    sql.exec('CREATE TABLE IF NOT EXISTS monitoring_batches(cursor INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT NOT NULL UNIQUE,stream_id TEXT NOT NULL,batch_sha256 TEXT NOT NULL,batch_json TEXT NOT NULL,signature TEXT NOT NULL,received_at INTEGER NOT NULL)');
    sql.exec('CREATE TABLE IF NOT EXISTS monitoring_events(cursor INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,batch_id TEXT NOT NULL,kind TEXT NOT NULL,outcome TEXT NOT NULL,request_id TEXT,event_json TEXT NOT NULL,received_at INTEGER NOT NULL)');
    sql.exec('CREATE TABLE IF NOT EXISTS monitoring_spans(cursor INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,trace_id TEXT NOT NULL,span_id TEXT NOT NULL,kind TEXT NOT NULL,outcome TEXT NOT NULL,request_id TEXT,span_json TEXT NOT NULL,received_at INTEGER NOT NULL,UNIQUE(trace_id,span_id))');
    sql.exec('CREATE TABLE IF NOT EXISTS monitoring_rejections(cursor INTEGER PRIMARY KEY AUTOINCREMENT,transport TEXT NOT NULL,code TEXT NOT NULL,received_at INTEGER NOT NULL)');
    for(const table of ['streams','batches','events','spans','rejections'])sql.exec(`CREATE INDEX IF NOT EXISTS monitoring_${table}_age ON monitoring_${table}(received_at)`);
  }
  private rows(q:string,...args:(string|number|null)[]){return Array.from(this.sql.exec(q,...args));}
  private bind(auth:MonitoringSubject){subject(auth);const old=this.rows('SELECT person_id,installation_id FROM monitoring_subject WHERE id=1')[0];if(old&&(old.person_id!==auth.person_id||old.installation_id!==auth.installation_id))throw new ContractError('monitoring_invalid');this.sql.exec('INSERT OR IGNORE INTO monitoring_subject VALUES(1,?,?)',auth.person_id,auth.installation_id);}
  private capacity(table:string,extra:number){if(Number(this.rows(`SELECT COUNT(*) AS n FROM monitoring_${table}`)[0].n)+extra>100000)throw new ContractError('monitoring_capacity');}
  purge(now=Date.now()){
    telemetryInteger(now);return this.transaction(()=>{let removed=0;let has_records=false;for(const table of ['streams','batches','events','spans','rejections']){const cutoff=now-(table==='rejections'?MONITORING_LIMITS.rejection_days:MONITORING_LIMITS.accepted_days)*86400000;removed+=Number(this.rows(`SELECT COUNT(*) AS n FROM monitoring_${table} WHERE received_at<?`,cutoff)[0].n);this.sql.exec(`DELETE FROM monitoring_${table} WHERE received_at<?`,cutoff);has_records ||= this.rows(`SELECT 1 FROM monitoring_${table} LIMIT 1`).length>0;}return {removed,has_records};});
  }
  async acceptAudit(auth:MonitoringSubject,input:AuditBatch,signature:string,now=Date.now()):Promise<MonitoringReceipt>{
    subject(auth);const batch=parseAuditBatch(input,now);if(typeof signature!=='string'||!/^[A-Za-z0-9_-]{86}$/.test(signature))telemetryError();const digest=await monitoringHash(batch);this.purge(now);
    return this.transaction(()=>{this.bind(auth);
      const existing=this.rows('SELECT batch_sha256 FROM monitoring_batches WHERE batch_id=?',batch.batch_id)[0];if(existing){if(existing.batch_sha256!==digest)throw new ContractError('monitoring_reused');return {status:'duplicate',accepted:0};}
      const head=this.rows('SELECT sequence,batch_sha256 FROM monitoring_streams WHERE stream_id=?',batch.stream_id)[0];
      if(head?(Number(head.sequence)+1!==batch.first_sequence||head.batch_sha256!==batch.previous_batch_sha256):(batch.first_sequence!==1||batch.previous_batch_sha256!==null))throw new ContractError('monitoring_chain');
      for(const e of batch.events)if(this.rows('SELECT 1 FROM monitoring_events WHERE event_id=?',e.event_id).length)throw new ContractError('monitoring_reused');
      this.capacity('events',batch.events.length);this.capacity('batches',1);this.capacity('streams',head?0:1);
      this.sql.exec('INSERT INTO monitoring_batches(batch_id,stream_id,batch_sha256,batch_json,signature,received_at) VALUES(?,?,?,?,?,?)',batch.batch_id,batch.stream_id,digest,canonicalJSON(batch),signature,now);
      for(const e of batch.events)this.sql.exec('INSERT INTO monitoring_events(event_id,batch_id,kind,outcome,request_id,event_json,received_at) VALUES(?,?,?,?,?,?,?)',e.event_id,batch.batch_id,e.kind,e.attributes.outcome,e.attributes.request_id??null,canonicalJSON(e),now);
      this.sql.exec('INSERT INTO monitoring_streams(stream_id,sequence,batch_sha256,received_at) VALUES(?,?,?,?) ON CONFLICT(stream_id) DO UPDATE SET sequence=excluded.sequence,batch_sha256=excluded.batch_sha256,received_at=excluded.received_at',batch.stream_id,batch.first_sequence+batch.events.length-1,digest,now);
      return {status:'accepted',accepted:batch.events.length};
    });
  }
  acceptSpans(auth:MonitoringSubject,spans:TelemetrySpan[],now=Date.now()):MonitoringReceipt{
    subject(auth);if(!Array.isArray(spans)||spans.length<1||spans.length>100)telemetryError();for(const s of spans)span(s,now);this.purge(now);
    return this.transaction(()=>{this.bind(auth);let accepted=0;
      for(const s of spans){const existing=this.rows('SELECT span_json FROM monitoring_spans WHERE event_id=? OR (trace_id=? AND span_id=?)',s.event.event_id,s.trace_id,s.span_id);const serialized=canonicalJSON(s);if(existing.length){if(existing.length!==1||existing[0].span_json!==serialized)throw new ContractError('monitoring_reused');continue;}
        this.capacity('spans',1);this.sql.exec('INSERT INTO monitoring_spans(event_id,trace_id,span_id,kind,outcome,request_id,span_json,received_at) VALUES(?,?,?,?,?,?,?,?)',s.event.event_id,s.trace_id,s.span_id,s.event.kind,s.event.attributes.outcome,s.event.attributes.request_id??null,serialized,now);accepted++;
      }return {status:accepted?'accepted':'duplicate',accepted};
    });
  }
  reject(auth:MonitoringSubject,transport:'audit'|'traces',code:MonitoringRejection,now=Date.now()){
    subject(auth);if(!['audit','traces'].includes(transport)||!MONITORING_REJECTIONS.includes(code))telemetryError();
    this.purge(now);this.transaction(()=>{this.bind(auth);this.sql.exec('INSERT INTO monitoring_rejections(transport,code,received_at) VALUES(?,?,?)',transport,code,now);this.sql.exec('DELETE FROM monitoring_rejections WHERE cursor <= (SELECT MAX(cursor)-10000 FROM monitoring_rejections)');});
  }
  list(auth:MonitoringSubject,source:'audit'|'traces'|'rejections'|'batches',before=Number.MAX_SAFE_INTEGER,now=Date.now()){
    this.bind(auth);this.purge(now);telemetryInteger(before,Number.MAX_SAFE_INTEGER,1);
    const fields={audit:['events','cursor,event_id,batch_id,event_json,received_at'],traces:['spans','cursor,event_id,span_json,received_at'],rejections:['rejections','cursor,transport,code,received_at'],batches:['batches','cursor,batch_id,stream_id,batch_sha256,batch_json,signature,received_at']} as const;
    if(!Object.hasOwn(fields,source))telemetryError();const [table,projection]=fields[source];
    const records:Record<string,unknown>[]=[],envelope={...auth,manifest_version:MONITORING_VERSION,source,trust:source==='audit'||source==='batches'?'client_reported_signed':source==='traces'?'client_reported':'server_validation'};
    const encoder=new TextEncoder();
    // Reserve the widest possible continuation number. Include JSON escaping,
    // UTF-8 subject bytes, array commas and the complete response envelope.
    let size=encoder.encode(JSON.stringify({...envelope,records,next_before:Number.MAX_SAFE_INTEGER})).byteLength;
    let more=false;
    // A SQL cursor avoids materializing 101 full batches just to return one
    // byte-bounded page. Only one omitted record is read as a lookahead.
    const rows=this.sql.exec(`SELECT ${projection} FROM monitoring_${table} WHERE cursor<? ORDER BY cursor DESC LIMIT ?`,before,MONITORING_READ_LIMITS.page_records+1);
    for(const row of rows){
      if(records.length===MONITORING_READ_LIMITS.page_records){more=true;break;}
      const item={...row};for(const key of ['event_json','span_json','batch_json'])if(typeof item[key]==='string'){item[key.slice(0,-5)]=JSON.parse(item[key] as string);delete item[key];}
      const itemBytes=encoder.encode(JSON.stringify(item)).byteLength+(records.length?1:0);
      if(size+itemBytes>MONITORING_READ_LIMITS.page_bytes){
        // Every valid accepted batch fits by itself. Corrupt oversized storage
        // must not produce an unbounded response or silently lose its cursor.
        if(records.length===0)throw new ContractError('monitoring_size');
        more=true;break;
      }
      size+=itemBytes;records.push(item);
    }
    return {...envelope,records,next_before:more?Number(records.at(-1)!.cursor):null};
  }
  summary(auth:MonitoringSubject,now=Date.now()){
    this.bind(auth);this.purge(now);const group=(table:string)=>this.rows(`SELECT kind,outcome,COUNT(*) AS count FROM monitoring_${table} GROUP BY kind,outcome ORDER BY kind,outcome`);
    return {...auth,manifest_version:MONITORING_VERSION,retention_days:90,audit:{trust:'client_reported_signed',counts:group('events')},traces:{trust:'client_reported',counts:group('spans')},rejections:this.rows('SELECT transport,code,COUNT(*) AS count FROM monitoring_rejections GROUP BY transport,code ORDER BY transport,code'),usage:'Client cost is not aggregated. Authoritative cost is available only from the separate server relay ledger.'};
  }
}
