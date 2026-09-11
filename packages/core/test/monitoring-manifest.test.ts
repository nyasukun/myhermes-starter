import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {MONITORING_MANIFEST} from '../src/monitoring.ts';
test('public download and installed Python monitoring manifests exactly match the executable contract',()=>{
  for(const path of ['../../../monitoring/manifest.v1.json','../../../src/myhermes/monitoring-manifest.json'])assert.deepEqual(JSON.parse(readFileSync(new URL(path,import.meta.url),'utf8')),MONITORING_MANIFEST);
});
