import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire('/dependencies/package.json');
const ts = require('typescript');
const file = '/observe-source/web/src/lib/observation-view.ts';
const source = fs.readFileSync(file, 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { usageLabel, mergeEvents, eventLabel } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
assert.equal(usageLabel(null), 'usage неизвестен');
assert.equal(usageLabel({total_tokens: 0}), 'total_tokens: 0');
assert.equal(usageLabel({input_tokens: 12, output_tokens: 9}), 'input_tokens: 12 · output_tokens: 9');
const a = {id: 1, event_id: 'a', project_id: 2, kind: 'json_retry', payload: {text: 'raw-secret'}};
assert.equal(mergeEvents([a], [a, {...a, id:2, event_id:'b', project_id:1}], 2).length, 1);
assert.equal(eventLabel(a).includes('raw-secret'), false);
assert.equal(eventLabel({kind:'runtime_check', payload:{check:'schema'}}).includes('неизвестно'), true);
assert.deepEqual(mergeEvents([{...a,created_at:'2026-09-28 11:20:20'}],
  [{...a,id:2,event_id:'earlier',created_at:'2026-09-28 11:20:10'}],2).map(e=>e.event_id),['earlier','a']);
assert.equal(eventLabel({kind:'observer_control',payload:{action:'stopped',initiator:'observation_api'}}),
  'Наблюдение: stopped (observation_api)');
console.log('Frontend metadata contracts: 8 assertions passed');
