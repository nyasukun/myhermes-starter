import {readFile,writeFile} from 'node:fs/promises';
import {RELAY_MANIFEST} from '../packages/core/src/relay-reconcile.ts';
const url=new URL('../monitoring/relay-manifest.v1.json',import.meta.url),text=JSON.stringify(RELAY_MANIFEST,null,2)+'\n';
if(process.argv.includes('--check')){if(await readFile(url,'utf8')!==text)throw new Error('Public relay manifest artifact is stale');}
else await writeFile(url,text);
