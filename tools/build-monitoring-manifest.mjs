import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';
import {MONITORING_MANIFEST} from '../packages/core/src/monitoring.ts';
import {SYNC_STATUS_MANIFEST} from '../packages/core/src/sync-status.ts';
const root=fileURLToPath(new URL('../',import.meta.url));
const text=JSON.stringify(MONITORING_MANIFEST,null,2)+'\n';
for(const relative of ['monitoring/manifest.v1.json','src/myhermes/monitoring-manifest.json']){
  const path=root+relative;
  if(process.argv.includes('--check')){
    if(await readFile(path,'utf8')!==text)throw new Error('Public monitoring manifest artifact is stale: '+relative);
  }else{await mkdir(dirname(path),{recursive:true});await writeFile(path,text);}
}
const syncPath=root+'monitoring/sync-status-manifest.v1.json';
const syncText=JSON.stringify(SYNC_STATUS_MANIFEST,null,2)+'\n';
if(process.argv.includes('--check')){
  if(await readFile(syncPath,'utf8')!==syncText)throw new Error('Public sync-status manifest artifact is stale');
}else await writeFile(syncPath,syncText);
