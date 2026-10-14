# Orchestrator remaining implementation — 2026-09-30

Existing branch: `feature/orchestrator-observe-v1`, worktree `work/video-pipeline-observe-v1`; current implementation checkpoint `974595267f77f4eb80776569f6dfcc39392d0477`. Final bounded collection completed: 305 Python executions, 304 passes and one missing-FFmpeg skip; four current-code worker cases accepted. This does not accept the whole factory.

Sources: [current state](ORCHESTRATOR_CURRENT_STATE.md), [JSON](ORCHESTRATOR_CURRENT_STATE.json), [progress](ORCHESTRATOR_COMPLETION_PROGRESS.md), [native text](ORCHESTRATOR_NATIVE_TEXT_ACCEPTANCE_20260930.md), [native split](ORCHESTRATOR_NATIVE_SPLIT_ACCEPTANCE_20260930.md), [native image prompts](ORCHESTRATOR_NATIVE_IMAGE_PROMPTS_ACCEPTANCE_20260930.md), prior minimal [registry](../app/orchestrator/node_registry.py)/[capabilities](../app/services/node_capabilities.py) reads, and the parent's current evidence update. Current-state reports describe historical `01583e55`; subsequent evidence extends only the stated boundaries. Historical reports/failures remain preserved.

Current bounded update: scene effective-type safety regression **152 passed**. Native `image_prompts` positive original-worker case is accepted offline: **one POST, one attempt, one apply; two frames with voiceover preserved; source DONE and outside-scope images PENDING**. Its supervisor capability is read-only. Negative cases remain distinct:

- Missing-prompt worker `loss-red-01`: unaccepted, bounded at **150 seconds**; **one model POST**, valid JSON with partial coverage. Original checkpoint reapply reported `OperationalError: database locked` twice. At shutdown of the run's own app, source was **RUNNING**, outside node **PENDING**, and `held=false`. Native exhaustion or verifier rejection was not established; lock owner/root cause remain unknown.
- Short-existing-prompt worker `short-red-01`: independently **one POST**; verifier failed with `missing_or_short_parent_prompt`, followed by TypeError and `held=false` due to the wrong ActionSupervisor factory. Fixed in `97459526`; current `short-green-02` verifies one failed proof/error and durable hold, unchanged rejected rows, no successor/new POST. It refreshes green-01 using the committed wait-for-error runner; green-01 is preserved. Source remains nonterminal (master running/project queued), not done.

Real failed-SQLite-session acceptance `native-image-error-session-green-02` passed: 27 passes and one missing-FFmpeg skip. Original IntegrityError/session failure retained, independent committed read with zero business writes, unchanged data, delivered output/proof/error and durable hold. This unit does not establish successful recovery from the original checkpoint lock. See [native image prompt acceptance](ORCHESTRATOR_NATIVE_IMAGE_PROMPTS_ACCEPTANCE_20260930.md).

Final collected suites are GREEN: **41 adapter, 49 Decision, 63 Actions, 26 Profiles, 2 planner, 8 journal, 26 runtime, 27 diagnosis, 35 managed launch, 27 media passes + 1 skip**. These include **18 new admission and 18 legacy claim tests**, not additional totals. The **16 legacy RED → 63 Actions GREEN** sequence establishes rejection before body execution and old Excel compatibility. Planner's fixture-only correction preserves its assertions. Counts are executions, not unique business capabilities. Loaded/staged/current application hashes have zero mismatches.

Current positive worker refresh, short-prompt rejection, genuine profile recovery→image and ordinary mode are accepted. Ordinary retains six nodes/five POST/8113-byte HTML; managed checkpoint recovery retains 22 historical operations and finishes at 28 with two Decision steps, one worker POST and three image operations. Outside video/pilot remain pending/new. Existing v653 frontend source194/export43 hashes match, reusing prior140 assertions/TypeScript/build/browser evidence; no new browser scenario or build was needed.

Evidence root: `<корень стенда>\outputs\orchestrator-completion-20260930`. This document records boundaries and references, without copying response bodies or secrets.

## Remaining implementation matrix

“Accepted offline” means bounded original-worker/fixture cases passed. Registry/capability/verifier presence alone does not establish family E2E acceptance.

| Family / capability | Reported status and accepted boundary | Remaining implementation / acceptance |
|---|---|---|
| Observe / Diagnose / Actions / Decision / Profiles | Accepted offline tracing, incidents, gates, hold and structured-text recovery/profiles, current-capability admission/legacy claim denial and old Excel compatibility; final same-code collection complete. Historical real read-only Decision is separate. | Broader causes/strategies, crash recovery, semantics and authorized real modifying cycles. |
| Structured text `excel_gpt/script_frames_qc` | Accepted offline six-node group/recovery/profiles; QC no-op distinguished from apply. | Quality/cause corpus, real profiles, factory composition; native `enrich_1..5` remains separate. |
| `images` | Accepted offline submit/status/download, file/hash/provenance, preview and mixed/recovery graphs. | Real jobs/reference inputs/model changes, semantic fit and crash/uncertain-job recovery; no generic text retry. |
| Native `plan` / `script` | Accepted offline fresh extraction/apply/commit/structural checks and pending HITL; plan browser reload. | Projection, cached provenance, failed/exhausted recovery. Supervisor actions remain `read_events`/`read_contract`/`wait`/`human_review`. |
| Native `split`, fresh input | Accepted offline voiceover coverage, frame identity/order, committed replacement and rejection hold. | Already committed rejected rows, recovery and split-specific browser proof; no accepted automatic rollback/profile/retry. |
| Cached/preexisting split | Missing input provenance stays unknown and held; ordinary cache path preserved. | Captured authoritative input/attempt provenance and matching verification before accepting reuse/optimization. |
| `scene_design`, `sd_agent`, `sd_assemble` | Effective-type safety regression 152 passed; scene-family managed E2E remains unverified. | Fanout/assembly contracts, domain gates and actual worker acceptance beyond type safety. |
| Native `image_prompts` | Current positive original worker plus short-existing-prompt failed verify/hold; separate real-SQLite error snapshot/rollback invariants. Native read-only capability, no supervisor recovery. | `loss-red-01` original checkpoint lock/reapply remains unaccepted; projection/nonterminal rows, cache provenance and safe native recovery remain open. |
| `hero`, `items`, `enrich_1..5`, `animation_prompts` | Registry/legacy interface-only; unsupported managed fallback in read snapshot. | Individual I/O/effect/HITL contracts, verifiers and worker cases. |
| `videos`, `audio`/TTS/ASR | Partial/legacy interfaces; managed disabled; worker E2E unaccepted. | FFmpeg/ffprobe and ASR packages/weights absent; approval pending. Then lifecycle, timing/alignment and artifact acceptance. |
| Subtitles / `assemble` | Subtitle structural verifier only; assembly managed disabled. | Composition, command/result/error evidence and final artifact acceptance after prerequisites; tools alone are insufficient. |
| `music`, `sfx_plan`, `sfx_gen` | Registered legacy families; managed lifecycle unaccepted. | Contracts, timing/media verification and worker acceptance. |
| `publish` | Read-only preview/dry-run; external publication disabled. | Delivery contract, separately authorized external/customer acceptance; production deployment unaccepted. |

Native plan evidence has master Project=`plan_ready` / WorkflowRun=`waiting_hitl`, versus project.db Project=`planning` / WorkflowRun=`new`; NodeRuns agree. The runtime/projection warning remains unresolved; lost content is not established. Mandatory HITL prevents unattended-chain acceptance. Bad split rows can already be committed before hold; hold does not undo them.

The **1000-operation counter exercise is synthetic reservation/accounting evidence**, not factory E2E or 1000 completed outputs. Operation limits are not a verified dollar cap. Repeated/inherited suite counts imply no completion percentage or schedule. No proven dollar saving or semantic/artistic/model-diagnostic quality is established; unknown usage/credits/prices remain unknown.

## Customer goal mapping

| Customer goal | Current bounded support | Closure needed |
|---|---|---|
| One launch to finished content | Scoped launch; original HITL preserved. | Projection/cache, family composition, human gates and final media: T2–T4, T6, T8. |
| Understand failures/judge results | Typed structural/transaction/media evidence; explicit unknown. | Cause corpus and semantic acceptance: T7. |
| Repair/select model or prompt | Structured-text recovery/preapproved synthetic profiles. | Native policies, crash proof, authorized real modification: T3–T5, T8. |
| Different graphs/agents/extensibility | Existing registry/contracts/worker boundaries. | Family/fanout acceptance: T4, T6. Arbitrary code edits remain prohibited; broader agent catalogs unimplemented. |
| Throughput and spending reduction | Durable operation accounting/fixture baselines. | Heterogeneous factory E2E, usage/tariffs and comparable quality: T8. |
| Customer delivery | Local evidence/preview surfaces. | Media, recovery/retention, scoped installation/publication: T5, T6, T8. |

## Specific next tasks

These are separate remaining work packages within the user's development scope;
this report itself grants no new permissions for installation or external access.
Parent exclusively owns Docker and integration; delegated edits need disjoint path ownership.

1. **T1 — Final bounded collection: complete.** Current-code framework/runtime/diagnosis/launch, positive worker, ordinary mode and genuine recovery refresh are bound by loaded hashes/body metadata in `ORCHESTRATOR_NATIVE_IMAGE_PROMPTS_ACCEPTANCE_20260930.md` and `native-prompts-final-summary.json`. `loss-red-01` remains an original checkpoint lock obstacle, separately from fixed verifier TypeError and accepted failed-session/short-prompt hold. Native exhaustion was not manufactured and business logic was not fixed for GREEN.
2. **T2 — Native projection.** Reproduce master/project Project, WorkflowRun and NodeRun states across legitimate HITL; trace executor/apply/session paths, establish status authority and add a regression case.
3. **T3 — Cached split provenance.** Specify input/attempt/hash requirements; accept matching reuse and missing-input/mismatch holds. Handle committed rejected rows explicitly; repair needs separate design.
4. **T4 — Native family contracts.** Assign scene fanout/assembly, hero/items/enrichment, animation prompts and music/SFX separately. Require original I/O/commit evidence, domain/HITL rules, verifier and positive/negative worker cases.
5. **T5 — Process crash/restart.** Parent interrupts reservation/apply/download cases; verify durable holds, uncertainty, no duplicate dispatch and actual successor starts. Document orphan/retention/recovery handling; Observer reconstruction is insufficient.
6. **T6 — Media.** Resolve FFmpeg/ffprobe and ASR packages/weights approval, then accept individual video/TTS/alignment/subtitle/assembly lifecycles and a composed final-media case.
7. **T7 — Cause/quality corpus.** Define expected structural/semantic judgments for text/image/native failures, human waits and unknowns; measure diagnosis/verification against them.
8. **T8 — Scale/money/delivery.** Define heterogeneous factory E2E and operation/usage/price/outcome baselines. Compose accepted families offline; real recovery/providers, spending comparisons and customer/publication acceptance need separate explicit authorization. Preserve unknown charges.

The full content factory remains incomplete. This synthesis creates only this document in the shared worktree; it does not change runtime acceptance or historical reports.
