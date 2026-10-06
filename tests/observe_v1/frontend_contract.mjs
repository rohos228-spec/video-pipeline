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
assert.equal(usageLabel(null), 'расход токенов неизвестен');
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
console.log('Frontend metadata/diagnosis contracts: passed');
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
const {scopedLaunchStrategies}=await import('data:text/javascript;base64,'+Buffer.from(actionJS).toString('base64'));
assert.equal(typeof scopedLaunchStrategies,'function');
const nativeOptions={...launchOptions,nodes:[{node_key:'plan',supported:true,actions:['wait','human_review']} ]};
assert.deepEqual(scopedLaunchStrategies(nativeOptions,['plan']),['wait','human_review']);
assert.match(managedLaunchError(nativeOptions,{...selection,node_keys:['plan'],strategies:['retry_same']}),/стратег/i);
assert.equal(managedLaunchError(nativeOptions,{...selection,node_keys:['plan']}),null);
assert.deepEqual(scopedLaunchStrategies(nativeOptions,['foreign']),[]);
assert.deepEqual(scopedLaunchStrategies({...nativeOptions,nodes:[...nativeOptions.nodes,
  {node_key:'text',supported:true,actions:['wait','human_review','retry_same']}]},['plan','text']),['wait','human_review','retry_same']);
console.log('Per-family launch strategy contracts: 6 assertions passed');
const catalogOptions={...launchOptions,strategies:[...launchOptions.strategies,'retry_profile'],
  profiles:[{profile_id:'format-approved'}],profile_catalog_hash:'catalog-v1'};
assert.match(managedLaunchError(catalogOptions,{...selection,strategies:['retry_profile']}),/профил/i);
assert.equal(managedLaunchError(catalogOptions,{...selection,strategies:['retry_profile'],
  profile_ids:['format-approved'],profile_catalog_hash:'catalog-v1'}),null);
assert.match(managedLaunchError(catalogOptions,{...selection,strategies:['retry_profile'],
  profile_ids:['unknown'],profile_catalog_hash:'catalog-v1'}),/профил/i);
assert.match(managedLaunchError(catalogOptions,{...selection,strategies:['retry_profile'],
  profile_ids:['format-approved'],profile_catalog_hash:'old'}),/каталог/i);
console.log('Preapproved profile launch contracts: 4 assertions passed');
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
assert.equal(actionStatus('revoked').includes('Запуск завершён'),true);
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
assert.match(decisionStatus('waiting_worker'),/проверки/);
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

// Planner clarification: option buttons re-send request + answer; a requested file is attached with the picker and re-sent.
const clarSource=fs.readFileSync('/observe-source/web/src/lib/clarification-view.ts','utf8');
const clarJS=ts.transpileModule(clarSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {clarificationQuestion,clarificationOptions,clarifiedRequest,attachmentHint,ATTACH_OPTION,ATTACH_BUTTON,RESEND_WITH_FILES}=await import('data:text/javascript;base64,'+Buffer.from(clarJS).toString('base64'));
assert.equal(clarificationQuestion({needs_clarification:' Какой шот? ',explanation_ru:'x'}),'Какой шот?');
assert.equal(clarificationQuestion({needs_clarification:true,explanation_ru:'Нужен шот'}),'Нужен шот');
assert.equal(clarificationQuestion({}),'');
assert.deepEqual(clarificationOptions({clarification_options:['шот 1',' шот 2 ','шот 1','','a','b','c']}),['шот 1','шот 2','a','b']);
assert.deepEqual(clarificationOptions({}),[]);
assert.deepEqual(clarificationOptions({clarification_options:[ATTACH_OPTION,'другое'],attachment_request:{action_id:'montage.upload_image'}}),['другое']);
assert.deepEqual(clarificationOptions({clarification_options:[ATTACH_OPTION]}),[ATTACH_OPTION]);
assert.equal(clarifiedRequest('удали картинку кадра 5.','шот 2'),'удали картинку кадра 5. Уточнение: шот 2');
assert.equal(clarifiedRequest('  удали картинку кадра 5  ','оба шота'),'удали картинку кадра 5. Уточнение: оба шота');
const info=attachmentHint({action_id:'montage.upload_image',formats:'PNG'},'Загрузить картинку');
assert.doesNotMatch(info,/нельзя приложить/);assert.doesNotMatch(info,/раздел/); // the command line can attach files now
assert.match(info,/Загрузить картинку/);assert.match(info,/PNG/);assert.match(info,new RegExp(ATTACH_BUTTON));assert.match(info,new RegExp(RESEND_WITH_FILES));
const infoWith=attachmentHint({action_id:'montage.upload_image'},undefined,true);
assert.match(infoWith,/Прикреплённые файлы можно отправить снова/);assert.match(infoWith,/уберите/);assert.match(infoWith,/montage\.upload_image/);
const planCard=fs.readFileSync('/observe-source/web/src/components/action-layer/plan-card.tsx','utf8');
assert.match(planCard,/clarificationOptions\(plan\)/);assert.match(planCard,/onClarify\(o\)/);assert.match(planCard,/attachmentHint/);
assert.doesNotMatch(planCard,/attachmentInfo/);assert.match(planCard,/onResend/);assert.match(planCard,/resend-with-files/);
const alSection=fs.readFileSync('/observe-source/web/src/components/action-layer/action-layer-section.tsx','utf8');
assert.match(alSection,/clarifiedRequest\(requestText, option\)/);assert.match(alSection,/planFromText\(text/);
// attachments: the same ids go with the first request and with every re-send; used files are dropped after the plan
assert.match(alSection,/readyIds\(files\)/);assert.match(alSection,/withoutUsed\(f, r\.attachments_used\)/);
const cmdBar=fs.readFileSync('/observe-source/web/src/components/action-layer/command-bar.tsx','utf8');
assert.match(cmdBar,/planFromText\(text\.trim\(\), c, readyIds\(attachments\)\)/);assert.match(cmdBar,/<AttachmentPicker/);
assert.match(cmdBar,/isUploading\(attachments\)/); // no request while a file is still uploading
const picker=fs.readFileSync('/observe-source/web/src/components/action-layer/attachment-picker.tsx','utf8');
assert.match(picker,/type="file"/);assert.match(picker,/accept=\{acceptAttr\(rules\)\}/);assert.match(picker,/role="progressbar"/);
assert.match(picker,/Убрать/);assert.match(picker,/Прикрепить файл/);assert.match(picker,/checkFile\(file, rules\)/);
const attSource=fs.readFileSync('/observe-source/web/src/lib/attachment-view.ts','utf8');
const attJS=ts.transpileModule(attSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const A=await import('data:text/javascript;base64,'+Buffer.from(attJS).toString('base64'));
assert.equal(A.formatSize(500),'500 Б');assert.equal(A.formatSize(2048),'2 КБ');assert.equal(A.formatSize(5*1024*1024+512*1024),'5,5 МБ');assert.equal(A.formatSize(-1),'');
assert.equal(A.fileExtension('C:\\a\\b\\Voice.MP3'),'.mp3');assert.equal(A.fileExtension('noext'),'');assert.equal(A.fileExtension('.hidden'),'');
assert.equal(A.kindOfFile('a.png'),'image');assert.equal(A.kindOfFile('a.MOV'),'video');assert.equal(A.kindOfFile('a.xls'),'excel');
assert.equal(A.kindOfFile('a.md'),'text');assert.equal(A.kindOfFile('a.exe'),null);assert.equal(A.kindOfFile('a'),null);
assert.match(A.acceptAttr(),/\.png/);assert.match(A.acceptAttr(),/\.json/);assert.doesNotMatch(A.acceptAttr(),/\.exe/);
assert.equal(A.checkFile({name:'a.png',size:1000}),null);
assert.match(A.checkFile({name:'a.exe',size:10}),/не разрешён/);assert.match(A.checkFile({name:'a.png',size:0}),/пустой/);
assert.match(A.checkFile({name:'a.png',size:40*1024*1024}),/больше лимита/);assert.equal(A.checkFile({name:'a.mp4',size:40*1024*1024}),null);
const items=[{key:'1',name:'a',size:1,status:'ready',progress:100,attachmentId:'id1'},{key:'2',name:'b',size:1,status:'uploading',progress:30},
  {key:'3',name:'c',size:1,status:'error',progress:0,error:'x'},{key:'4',name:'d',size:1,status:'ready',progress:100,attachmentId:'id4'}];
assert.deepEqual(A.readyIds(items),['id1','id4']);assert.equal(A.isUploading(items),true);assert.equal(A.isUploading([items[0]]),false);
assert.deepEqual(A.withoutUsed(items,['id1']).map(i=>i.key),['2','3','4']);assert.equal(A.withoutUsed(items,undefined),items);assert.equal(A.withoutUsed(items,[]),items);
assert.equal(A.itemStatusText(items[1]),'загрузка 30%');assert.equal(A.itemStatusText(items[0]),'готов');assert.equal(A.itemStatusText(items[2]),'x');
const alLib=fs.readFileSync('/observe-source/web/src/lib/action-layer.ts','utf8');
assert.match(alLib,/attachments: attachmentIds/);assert.match(alLib,/attachments_used\?: string\[\]/);assert.match(alLib,/XMLHttpRequest/);
console.log('Planner clarification UI contracts: passed');

// «Покрытие нод» panel
const covSource=fs.readFileSync('/observe-source/web/src/lib/node-coverage-view.ts','utf8');
const covJS=ts.transpileModule(covSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {coverageLevelLabel,coverageSummaryLine,forbiddenText,verifiesText,COVERAGE_LEVEL_CLASS}=await import('data:text/javascript;base64,'+Buffer.from(covJS).toString('base64'));
assert.equal(coverageSummaryLine({managed:8,observed:11,human_only:5,total:24}),'управляется 8 из 24, наблюдается 11, только человек 5');
assert.match(coverageSummaryLine(null),/не прочитано/);
assert.equal(coverageLevelLabel('managed'),'Под управлением');assert.equal(coverageLevelLabel('observed'),'Под наблюдением');
assert.equal(coverageLevelLabel('human_only'),'Только человек');assert.equal(coverageLevelLabel('bogus'),'Неизвестно');
assert.equal(new Set(Object.values(COVERAGE_LEVEL_CLASS)).size,3);
assert.match(forbiddenText({recovery:{actions:['retry_same'],cap_per_node:1},forbidden:null}),/Автоповтор разрешён.*retry_same.*1/);
assert.equal(forbiddenText({recovery:{actions:[],cap_per_node:0},forbidden:{code:'x',meaning:'смысл кода',reason:'причина запрета'}}),'причина запрета');
assert.equal(forbiddenText({recovery:{actions:[],cap_per_node:0},forbidden:{code:'x',meaning:'смысл кода',reason:null}}),'смысл кода');
assert.match(forbiddenText({recovery:{actions:[],cap_per_node:0},forbidden:null}),/не указана/);
assert.match(verifiesText({verifies:[]}),/общий журнал/);assert.equal(verifiesText({verifies:['a','b']}),'a; b');
const covPanel=fs.readFileSync('/observe-source/web/src/components/orchestrator/node-coverage-section.tsx','utf8');
assert.match(covPanel,/nodeCoverage\(projectId\)/);assert.match(covPanel,/Загрузка/);assert.match(covPanel,/role="alert"/);
assert.doesNotMatch(covPanel,/method:|POST|onClick|<button|edit_files|shell|\.env/i); // read-only
assert.match(fs.readFileSync('/observe-source/web/src/components/orchestrator/observation-section.tsx','utf8'),/<NodeCoverageSection projectId=\{projectId\}\/>/);
assert.match(fs.readFileSync('/observe-source/web/src/lib/observations.ts','utf8'),/path\(project\)\+"\/node-coverage"/);
console.log('Node coverage UI contracts: passed');

// The bug button must not promise code fixes while CHAT_CODE_ACTIONS_ENABLED defaults to false.
const orchPanel=fs.readFileSync('/observe-source/web/src/components/orchestrator/orchestrator-panel.tsx','utf8');
assert.match(orchPanel,/BUG_MODE_LABEL = "Диагностика багов"/);assert.match(orchPanel,/CHAT_CODE_ACTIONS_ENABLED=false/);
assert.match(orchPanel,/\{BUG_MODE_LABEL\}\s*<\/Button>/);
assert.doesNotMatch(orchPanel,/Режим фикса|нажмите «Фикс багов»|чинить \(шаги\/код\)/);
console.log('Bug-mode honesty UI contracts: passed');

// «Восстановление медиа» panel: read-only view + plans through the action layer (never the write endpoints)
const mrSource=fs.readFileSync('/observe-source/web/src/lib/media-recovery-view.ts','utf8');
const mrJS=ts.transpileModule(mrSource,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const M=await import('data:text/javascript;base64,'+Buffer.from(mrJS).toString('base64'));
for(const mode of ['resume_poll','resume_local','retry_same','denied','nothing_to_do']){
  assert.ok(M.modeLabel(mode).length>3);assert.ok(M.modeExplain(mode).length>20);assert.match(M.modeClass(mode),/border-/);
}
assert.equal(new Set(['resume_poll','resume_local','retry_same','denied','nothing_to_do'].map(M.modeLabel)).size,5);
assert.match(M.modeExplain('retry_same','local_only_no_provider_spend'),/Локальная сборка/);assert.match(M.modeExplain('retry_same','provably_failed'),/точно отклонил/);
assert.equal(M.modeLabel('bogus'),'Неизвестно');assert.match(M.modeExplain('bogus'),/не распознано/);
assert.match(M.codeText('unknown_outcome_outsee'),/Outsee/);assert.equal(M.codeText('weird_code'),'weird_code');assert.equal(M.codeText(null),'');
assert.match(M.ledgerText('disabled'),/выключен/);assert.match(M.ledgerText(undefined),/нет данных/);
const keys=[{key:'clip_001',mode:'resume_poll',code:'open_task',task_id:'t1'},{key:'clip_002',mode:'resume_poll',code:'open_task',task_id:'t1'},
  {key:'clip_003',mode:'denied',code:'unknown_outcome_kie'}];
assert.equal(M.ledgerCounts(keys),'открытых 2, ждут человека 1');assert.equal(M.ledgerCounts([]),'задач в журнале нет');
const asm=(node,op,extra={})=>({project_id:1,node_type:node,verdict:{mode:op?'resume_poll':'denied',code:'x'},keys,
  supervisor_admission:{admitted_node:true,operation:op,denied:op?null:'verdict_denied',...extra}});
assert.equal(M.hasTaskId(asm('videos','resume_poll')),true);assert.deepEqual(M.taskIds(asm('videos','resume_poll')),['t1']);
assert.equal(M.hasTaskId({keys:[{key:'a',mode:'denied',code:'x'}]}),false);
assert.equal(M.canResume(asm('videos','resume_poll')),true);assert.equal(M.resumeBlockedReason(asm('videos','resume_poll')),null);
assert.equal(M.canResume(asm('videos',null)),false);assert.match(M.resumeBlockedReason(asm('videos',null)),/неизвестен|решает человек/);
assert.equal(M.canResume(asm('hero','resume_poll')),false); // resume is only for videos/items/music, whatever the server says
assert.match(M.resumeBlockedReason(asm('hero','resume_poll')),/нет задачи провайдера/);
assert.equal(M.canResume(null),false);assert.match(M.resumeBlockedReason(null),/не прочитано/);
assert.match(M.resumeBlockedReason(asm('music',null,{denied:null,admitted_node:false})),/не допускается/);
assert.equal(M.formatAge(30),'30 с');assert.equal(M.formatAge(40*60),'40 мин');assert.equal(M.formatAge(2*3600+300),'2 ч 5 мин');assert.equal(M.formatAge(null),'неизвестно');
assert.equal(M.formatBlockedUntil('2026-10-06T14:35:09'),'до 06.10 14:35 (UTC)');assert.equal(M.formatBlockedUntil(''),'неизвестно');
assert.equal(M.reasonOk('коротко'),false);assert.equal(M.reasonOk('         123456789      '),false);assert.equal(M.reasonOk('Сверил кабинет Kie: списания нет'),true);
assert.equal(M.reasonOk('x'.repeat(501)),false);assert.equal(M.cleanReason('  a   b \n c '),'a b c');
assert.deepEqual(M.resumeParams(7,'videos'),{project_id:7,node_type:'videos'});
assert.deepEqual(M.releaseParams(7,{key:'clip_005',provider:'kie'},'  Сверил   кабинет, списания нет '),
  {project_id:7,key:'clip_005',provider:'kie',reason:'Сверил кабинет, списания нет'});
assert.equal(M.RESUME_ACTION,'media.resume_poll');assert.equal(M.RELEASE_ACTION,'media.release_unknown');
assert.match(M.SECOND_CHARGE_WARNING,/ВТОРОЕ СПИСАНИЕ/);assert.match(M.SECOND_CHARGE_WARNING,/кабинет/);
assert.deepEqual(M.MEDIA_NODES.map(n=>n.node_type).sort(),['assemble','audio','hero','images','items','music','sfx_gen','videos']);
const mrPanel=fs.readFileSync('/observe-source/web/src/components/orchestrator/media-recovery-section.tsx','utf8');
assert.match(mrPanel,/previewPlan\(\[\{action_id: RESUME_ACTION/);assert.match(mrPanel,/previewPlan\(\[\{action_id: RELEASE_ACTION/);
assert.match(mrPanel,/<PlanCard/);assert.match(mrPanel,/<RunProgress/);assert.match(mrPanel,/Докачать/);assert.match(mrPanel,/Снять блокировку…/);
assert.match(mrPanel,/Загрузка/);assert.match(mrPanel,/role="alert"/);assert.match(mrPanel,/Блокировок неизвестного исхода нет/);
assert.match(mrPanel,/observations\.nodeCoverage\(project\)/); // the same API as the «Покрытие нод» panel: counts are never hardcoded
assert.match(mrPanel,/coverageSummaryLine\(coverage\.summary\)/);assert.match(mrPanel,/forbiddenText\(cov\)/);
assert.match(mrPanel,/disabled=\{!reasonOk\(reason\)/);assert.match(mrPanel,/SECOND_CHARGE_WARNING/);
assert.doesNotMatch(mrPanel,/resume-poll|release-unknown|method:\s*"POST"|approvePlan|runPlan|\.env/); // no direct write call, no approval from here
const mrApi=fs.readFileSync('/observe-source/web/src/lib/media-recovery.ts','utf8');
assert.match(mrApi,/media-recovery/);assert.doesNotMatch(mrApi,/POST|method:|resume-poll|release-unknown/);
assert.doesNotMatch(mrSource,/\b(managed|observed|human_only)\s*[:=]\s*\d/); // coverage counts come from the API
assert.match(fs.readFileSync('/observe-source/web/src/components/orchestrator/observation-section.tsx','utf8'),/<MediaRecoverySection projectId=\{projectId\}\/>/);
assert.match(planCard,/ВТОРОЕ СПИСАНИЕ|destructive_effects/); // the destructive card text comes from the server preview
console.log('Media recovery UI contracts: passed');

// planner retry hint: `retryable: true` from the planner -> «Повторить» in the command line card
assert.match(alLib,/retryable\?: boolean/);assert.match(alLib,/export function isRetryable/);
assert.match(cmdBar,/isRetryable\(e\)/);assert.match(cmdBar,/planner-retry/);assert.match(cmdBar,/Повторить/);
assert.match(planCard,/plan\.retryable && onRetry/);assert.match(alSection,/onRetry=/);
console.log('Planner retry UI contracts: passed');
