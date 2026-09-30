import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire('/dependencies/package.json');
const ts = require('typescript');
const file = '/observe-source/web/src/lib/observation-view.ts';
const source = fs.readFileSync(file, 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { usageLabel, mergeEvents, eventLabel, groupForScope } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
// A persisted execution can belong to a group other than the first graph group.
const graphGroups=[{id:'other',keys:['outside']},{id:'script_frames_qc',keys:['script','qc']}];
assert.equal(groupForScope(graphGroups,['qc','script']),'script_frames_qc');
assert.equal(groupForScope(graphGroups,['script']),'');
assert.equal(groupForScope(graphGroups,['removed']),'');
assert.equal(groupForScope(graphGroups,[]),'');
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
const diagnosisSource=fs.readFileSync('/observe-source/web/src/lib/diagnosis-view.ts','utf8');
const diagnosisJS=ts.transpileModule(diagnosisSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {groupIncidents,diagnosisSummary}=await import('data:text/javascript;base64,'+Buffer.from(diagnosisJS).toString('base64'));
const repaired={incident_id:'i1',status:'resolved_confirmed',currently_relevant:false,resolution:{kind:'format_recovery'}};
const open={incident_id:'i2',status:'open',currently_relevant:true,type:'user_stop',blocking:false};
const groups=groupIncidents([repaired,open,{...repaired,incident_id:'i3',resolution:{kind:'condition_not_observed'}}]);
assert.equal(groups.repaired.length,1);assert.equal(groups.attention.length,1);assert.equal(groups.other.length,1);
assert.equal(diagnosisSummary(null).includes('не подтверждена'),true);
assert.equal(diagnosisSummary({available:false}).includes('недоступна'),true);
assert.equal(diagnosisSummary({available:true,lagging:true}).includes('отстаёт'),true);
assert.equal(diagnosisSummary({available:true,lagging:false,blocking_count:0,evidence_complete:false}).includes('неполны'),true);
assert.equal(diagnosisSummary({available:true,lagging:false,blocking_count:1,evidence_complete:true}).includes('1'),true);
assert.equal(groupIncidents([{...open,currently_relevant:false,status:'human_review'}]).attention.length,1);
console.log('Frontend metadata/diagnosis contracts: 17 assertions passed');
const actionSource=fs.readFileSync('/observe-source/web/src/lib/actions-view.ts','utf8');
const actionJS=ts.transpileModule(actionSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {actionStatus,actionsModeLabel,actionReviewMessage}=await import('data:text/javascript;base64,'+Buffer.from(actionJS).toString('base64'));
const {managedLaunchError}=await import('data:text/javascript;base64,'+Buffer.from(actionJS).toString('base64'));
const launchOptions={configured:true,request_limit_max:100,
  nodes:[{node_key:'a',supported:true},{node_key:'z',supported:true},{node_key:'video',supported:false}],
  strategies:['wait','human_review','retry_same']};
const selection={node_keys:['a','z'],strategies:['wait','human_review'],request_limit:12};
assert.equal(typeof managedLaunchError,'function','simple launch must validate the server capability catalog');
assert.equal(managedLaunchError(launchOptions,selection),null);
assert.match(managedLaunchError(launchOptions,{...selection,node_keys:[]}),/область/i);
assert.match(managedLaunchError(launchOptions,{...selection,node_keys:['video']}),/поддерж/i);
assert.match(managedLaunchError(launchOptions,{...selection,node_keys:['unknown']}),/поддерж/i);
assert.match(managedLaunchError(launchOptions,{...selection,request_limit:NaN}),/лимит/i);
assert.match(managedLaunchError(launchOptions,{...selection,request_limit:101}),/лимит/i);
assert.match(managedLaunchError(launchOptions,{...selection,strategies:['shell']}),/стратег/i);
assert.match(managedLaunchError({...launchOptions,configured:false},selection),/выключ|настро/i);
console.log('Unified launch capability/selection contracts: 9 assertions passed');
assert.doesNotMatch(actionStatus('human_review'),/recovery/);
assert.match(actionReviewMessage('human_review'),/Причина/);
assert.doesNotMatch(actionReviewMessage('human_review'),/Verification не прошёл/);
assert.match(actionReviewMessage('node_recovery'),/не подтверждён/);
assert.match(actionsModeLabel(null),/не подтверждено/);
assert.equal(actionsModeLabel({configured:false,enabled:false,held:false}),'Управление выключено');
assert.match(actionsModeLabel({configured:true,enabled:true,held:true}),/удержан/);
assert.match(actionsModeLabel({configured:true,enabled:false,held:true}),/завершён/);
for(const state of ['proposed','accepted','rejected','queued','running','await_verify','verified','human_review','revoked']) {
  assert.notEqual(actionStatus(state),state);
  assert.equal(actionStatus(state).includes('undefined'),false);
}
assert.equal(actionStatus('human_review').includes('Нужен человек'),true);
assert.equal(actionStatus('revoked').includes('Execution завершён'),true);
assert.equal(groupIncidents([{...repaired,resolution:{kind:'supervisor_recovery_verified'}}]).repaired.length,1);
console.log('Actions UI status contracts: 21 assertions passed');
const decisionSource=fs.readFileSync('/observe-source/web/src/lib/decision-view.ts','utf8');
const decisionJS=ts.transpileModule(decisionSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {decisionStatus,decisionRuntimeLabel,decisionActionLabel,decisionPolicyLabel}=await import('data:text/javascript;base64,'+Buffer.from(decisionJS).toString('base64'));
const {decisionWaitLabel}=await import('data:text/javascript;base64,'+Buffer.from(decisionJS).toString('base64'));
assert.match(decisionStatus('waiting_evidence'),/Ожидает новых событий/);
assert.equal(typeof decisionWaitLabel,'function');
assert.equal(decisionWaitLabel({}),null);
assert.match(decisionWaitLabel({wait:{until:'2026-09-30T12:00:00'}}),/UTC.*не отправляются/);
assert.match(decisionWaitLabel({wait:{until:'unknown'}}),/неизвестен/);
assert.match(decisionPolicyLabel({allowed:false,reason:'wait expired without new runtime evidence'}),/Ожидание.*человек/);
console.log('Durable Decision wait UI contracts: 6 assertions passed');
assert.match(decisionRuntimeLabel({mode:'production',configured:false}),/не настроена/);
assert.match(decisionRuntimeLabel({mode:'synthetic',configured:true}),/Тестовый/);
assert.match(decisionRuntimeLabel({mode:'production',configured:true}),/Рабочий/);
assert.match(decisionActionLabel('retry_same'),/Повтор/);
assert.match(decisionPolicyLabel({allowed:false,reason:'context version changed'}),/измен/);
assert.match(decisionPolicyLabel({allowed:false,reason:'unmapped-private-detail'}),/Подробности/);
assert.doesNotMatch(decisionPolicyLabel({allowed:false,reason:'unmapped-private-detail'}),/unmapped/);
for(const state of ['ready','model_pending','acting','waiting_worker','done','human_review'])assert.notEqual(decisionStatus(state),state);
assert.match(decisionStatus('human_review'),/Нужен человек/);
assert.match(decisionStatus('waiting_worker'),/verify/);
const decisionPanel=fs.readFileSync('/observe-source/web/src/components/orchestrator/decision-section.tsx','utf8');
assert.match(decisionPanel,/controlDecisions/);assert.match(decisionPanel,/rationale.trim\(\).length<3/);
assert.match(decisionPanel,/verification.verified/);assert.doesNotMatch(decisionPanel,/edit_files|shell|\.env/);
console.log('Decision UI contracts: 12 assertions passed');

const profileSource=fs.readFileSync('/observe-source/web/src/lib/profiles-view.ts','utf8');
const profileJS=ts.transpileModule(profileSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {profileStatus}=await import('data:text/javascript;base64,'+Buffer.from(profileJS).toString('base64'));
for(const state of ['approved','reserved','active','applied','failed','cancelled'])assert.notEqual(profileStatus(state),state);
assert.match(profileStatus('cancelled'),/данные и бюджет сохранены/);
assert.match(profileStatus('failed'),/нужен человек/);
const profilePanel=fs.readFileSync('/observe-source/web/src/components/orchestrator/profiles-section.tsx','utf8');
assert.match(profilePanel,/approveProfile/);assert.match(profilePanel,/cancelProfile/);
assert.match(profilePanel,/verification\?\.verified/);assert.doesNotMatch(profilePanel,/edit_files|shell|\.env/);
console.log('Profiles UI contracts: 12 assertions passed');

// Same first render on server and client, even when persisted UI differs.
const vm=await import('node:vm');
const React=require('react');const ReactServer=require('react-dom/server');
const hookSource=fs.readFileSync('/observe-source/web/src/hooks/use-persisted-state.ts','utf8');
const hookJS=ts.transpileModule(hookSource,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
let storageReads=0;const hookModule={exports:{}};
vm.runInNewContext(hookJS,{exports:hookModule.exports,require,window:{},localStorage:{
  getItem:()=>{storageReads++;return JSON.stringify('persisted-client-value');},setItem:()=>{}}});
function FirstClientRender(){const [value]=hookModule.exports.usePersistedState('synthetic-ui-key','server-default');return React.createElement('span',null,value);}
assert.equal(ReactServer.renderToString(React.createElement(FirstClientRender)),'<span>server-default</span>');
assert.equal(storageReads,0);
console.log('Persisted UI first-render contracts: 2 assertions passed');

const mediaSource=fs.readFileSync('/observe-source/web/src/lib/media-view.ts','utf8');
const mediaJS=ts.transpileModule(mediaSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {mediaPhase,mediaVerification,artifactLink}=await import('data:text/javascript;base64,'+Buffer.from(mediaJS).toString('base64'));
assert.match(mediaPhase('running'),/выполняется/);
assert.doesNotMatch(mediaPhase('running'),/завис|%/);
assert.match(mediaPhase('unknown'),/неизвестен/);
assert.doesNotMatch(mediaPhase('completed'),/проверен/);
assert.match(mediaVerification('passed'),/качество.*не оценивалось/);
assert.match(mediaVerification(),/не подтверждена/);
assert.equal(artifactLink('a'.repeat(32),2),'/api/artifacts/'+ 'a'.repeat(32)+'/file?project_id=2');
for(const unsafe of ['../../secret','http://evil.test','/tmp/output.mp4'])assert.equal(artifactLink(unsafe),null);
console.log('Media UI contracts: 10 assertions passed');

const {publicationSummary}=await import('data:text/javascript;base64,'+Buffer.from(mediaJS).toString('base64'));
assert.match(publicationSummary('passed'),/проверен/);
assert.match(publicationSummary('failed'),/не прош/);
assert.match(publicationSummary('unknown'),/не подтвержд/);
for(const status of ['passed','failed','unknown'])assert.match(publicationSummary(status),/Публикация выключена/);
console.log('Publication dry-run UI contracts: 6 assertions passed');

const {imagePreviewLink}=await import('data:text/javascript;base64,'+Buffer.from(mediaJS).toString('base64'));
const verifiedImage={phase:'verified',verification:{status:'passed'},artifact_uuid:'b'.repeat(32)};
assert.equal(imagePreviewLink('image',verifiedImage,2),'/api/artifacts/'+ 'b'.repeat(32)+'/file?project_id=2');
for(const phase of ['reserved','running','completed','failed','unknown'])
  assert.equal(imagePreviewLink('image',{...verifiedImage,phase},2),null);
for(const status of ['failed','unknown',undefined])
  assert.equal(imagePreviewLink('image',{...verifiedImage,verification:{status}},2),null);
for(const artifact_uuid of [null,'../../secret','http://evil.test','/tmp/frame.png'])
  assert.equal(imagePreviewLink('image',{...verifiedImage,artifact_uuid},2),null);
for(const kind of ['video','audio','publication','unknown'])
  assert.equal(imagePreviewLink(kind,verifiedImage,2),null);
for(const projectId of [undefined,null,0,-1,1.5,NaN,Infinity,'2','2&project_id=1']) {
  assert.equal(artifactLink('b'.repeat(32),projectId),null);
  assert.equal(imagePreviewLink('image',verifiedImage,projectId),null);
}
console.log('Verified scoped image preview contracts: 35 assertions passed');
