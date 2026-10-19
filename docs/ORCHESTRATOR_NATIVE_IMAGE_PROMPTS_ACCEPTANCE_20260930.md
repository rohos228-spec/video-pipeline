# Native image prompts and recovery capability admission

## Scope and version

Existing worktree and `feature/orchestrator-observe-v1`; implementation checkpoint
`974595267f77f4eb80776569f6dfcc39392d0477`. Final bounded collection completed.
The previous native-text/split, scene type safety and UI reports remain historical.
This extends the original runtime; it does not accept the entire content factory.

Evidence root: `outputs/orchestrator-completion-20260930` outside the clone.
All containers use `--network none --cap-drop ALL --security-opt no-new-privileges`,
the existing dependency image and copied permitted sources. No customer `.env`,
real credentials, host home, Docker socket, provider requests, installation,
working DB, push or deployment were used.

## Changes

- `node_capabilities.py`: native `image_prompts`/`frame_prompts` contract and
  original VO-parent predicate. Capture all input frame identities, numbers,
  voiceovers and required-parent flags before execution. Verify scoped identity,
  count/order, unchanged voiceover, nonempty primary prompt and the native
  200-character threshold in either actual stored shot slot. Preserve child
  exemptions. Missing provenance is unknown; semantic quality is unverified.
- `generate_image_prompts.py` captures input in the original executor;
  `xlsx_step_runners.py` records original extraction/checks and binds committed
  batches to their actual request. Original models, prompts, native retries,
  scheduler, parser, checkpoint and database application are preserved.
- `observation.py` uses task-local completed-request identity for parallel batches.
  Final/error output reads the existing business engine through an independent
  session without autoflush, commit, initialization, migration or repair of the
  native session. Errors retain their original exception. Unavailable rows are
  explicit unknown/audit gaps. A rejection saves one verifier hold and cannot
  resume itself; the deliberate rejection does not produce duplicate proof.
- `decision_v1.py`: native read_contract includes the actual scoped primary and
  secondary prompt hash, without claiming a verification was performed.
  Native actions remain read_events/read_contract/wait/human_review, no profile,
  retry or finish. Effective scene markers cannot inherit Excel capabilities.
- `actions_recovery.py`: after existing graph checks, new admission resolves the
  original master snapshot's effective type and current capability. Both
  retry_same/profile need that capability plus the execution grant, before
  business/profile/reservation/FSM effects. Tickets persist action_type,
  effective_node_type and capability_actions. Claim repeats the current checks
  before CAS, including historical queued tickets. Stored proof is not authority.
  Legitimate old Excel tickets without the new proof remain eligible once;
  inconsistent strategy/profile metadata fails closed.

No new engine, worker, provider adapter, stand or arbitrary code/SQL tool was added.

## Executed cases

| Case | Actual execution | Result / boundary |
|---|---|---|
| Native prompt positive, `native-image-prompts-worker-02` | One API launch, one original worker attempt, one fixture POST, one committed apply; two frame UUIDs and full VO retained | Source done; outside image generation pending; not held. No second POST after 12 seconds. Current implementation loaded hashes match. Earlier worker-01 remains preserved. |
| Wrong existing prompt, `native-image-prompts-short-green-02` | Explicit initial 199-character first prompt; original worker generates only the missing second prompt: one POST/apply | One failed verification, one RuntimeError, persisted hold, no node_end/recovery/Decision/successor or additional POST after 12 seconds. Invalid committed data is retained. Current runner waits for node_error; preceding green-01 is preserved separately. |
| Real failed business session, `native-image-error-session-green-02` | Actual SQLite duplicate-frame IntegrityError after committed partial output; original session stays failed | Original exception identity retained; independent committed read, zero business SQL writes, unchanged rows/project, zero model charge, one output/proof/error/hold, delivered by actual outbox catch-up. Unit/integration case, not provider worker quality evidence. |
| Native missing-prompt fixture, `native-image-prompts-loss-red-01` | Valid JSON with one of two prompts; one fixture POST before checkpoint reapply | **Not accepted.** At 150-second diagnostic deadline the original checkpoint apply reported SQLite database lock twice, node running, outside pending, not held. Native exhaustion/final verification was not reached. Source data and logs preserved; lock owner/root cause are not established. |
| Genuine checkpoint recovery, `native-prompt-final-recovery-image-01` | Original exhausted checkpoint, two synthetic Decision replies: read_events and retry_profile; one new QC worker POST, then original image submit/status/download | One distinct recovery, verified before continuation; Decision done without a third model step. Text nodes and image done, outside video pending, pilot #1 new. Historical 22 operations retained, total 28. Profile selection is synthetic evidence, not real-model quality. |
| Ordinary mode, `native-prompt-final-ordinary-01` | Observation/managed disabled, one original API start, original planner and worker | Six nodes done, five fixture POST, nonempty application HTML (8113 bytes). No observation journal is expected in this mode. No change in the accepted ordinary-node/model-POST count. |

Wrong-existing-prompt final saved states differ between DB projections: master
NodeRun remains running, project NodeRun queued; both projects are
generating_image_prompts and the execution is held. This does not mean the node,
execution, or whole WorkflowRun completed. Scope guarding prevents a new tick;
business statuses and rejected rows were not manually corrected or rolled back.

## TDD and regressions

- Original recovery capability RED: 45 executions, 14 failures and 2 missing-proof
  errors; then 45/45 GREEN with successful ticket proof.
- Historical queued claim RED: 63 executions, 16 failures; both old Excel
  compatibility controls already passed. Fixed gate subsequently passed all
  63 Actions cases, including 14 direct and two actual observation-entry denials.
- Prompt read-contract RED: one failure in three; minimal output hash fix 3/3.
- Parallel request identity RED: one failure; task-local request binding GREEN.
- Real business rollback RED: one failure in 28 (plus dependency skip). First
  fixed run exposed a test fixture's missing outbox-delivery step; adding actual
  Observer.catch_up preserved every assertion. Final 27 passes + one FFmpeg skip.
- Framework first refresh: adapter41, Decision49, Actions63, Profiles26 and
  journal8 GREEN; one of two planner cases failed because the inherited fixture
  had become Excel. Explicit native plan fixture preserved assertions; 2/2 GREEN.
- Final same-code collection: 189 framework, 26 runtime, 27 diagnosis, 35 launch
  and 28 media contract executions. **305 executions: 304 passes, one absent-FFmpeg
  skip.** Framework includes adapter41 / Decision49 / Actions63 / Profiles26 /
  planner2 / journal8. Four bounded worker cases above were accepted, with the
  original missing-prompt lock case explicitly excluded.

All final framework/runtime/diagnosis/launch and positive/rejection/recovery/ordinary
runs record implementation commit `97459526`. Failed-session GREEN was run from
dirty `7453e372` before the commit; staged/loaded/current application hashes
match the committed implementation, with zero mismatches. Staged tests also
match current files. This is byte
equivalence evidence, not a claim that those earlier run-config SHAs changed.
The exact loaded SHA256 map is in `native-prompts-final-summary.json`.

The first metadata analyzer pass with strict test-source hash checking exposed
one difference in green-01's runner: it could exit its poll on held before the
node_error arrived. Only that short case was repeated as green-02 with the
already committed wait-for-error runner. Production code was unchanged and
no old events/results were overwritten. Final summary now checks every staged
test file as well as loaded application modules.

Counts are executions, including inherited/repeated scenarios, not unique
business capabilities. Failed runs and original events were not rewritten.

## Logging evidence and external material

Each worker's `artifacts/api-history.json` holds original saved events and body
links/hashes; `actual-worker-requests.json` holds sanitized local fixture requests.
`significant-timeline.json` is an additional selection, not a replacement journal.
`body-references.json`/`final-business-state.json` are metadata-only analysis.
Full bodies are separate under `artifacts/free-run-data/observations/bodies`.
Loaded-module hashes and `source/source-manifest.json` identify actual bytes,
including tests launched while the source commit had a diagnostic dirty diff.

Outside-clone `outputs/orchestrator-completion-20260930/stage_summary.py` is
stdlib-only offline artifact analysis: it compares source/loaded/current hashes,
checks body hashes/lengths and reads saved SQLite in mode=ro. It changes no
application behavior or historical events. No external runtime wrapper was used.

Frontend source/build was not changed. Prior v653/140 contract assertions,
TypeScript/build/native-plan browser evidence can be reused only by unchanged
file hashes; they are not new image-prompt UI screenshots. All 194 source and
43 export files match. Export entry SHA256:
`4ee9c3fe6197f86a53055ce30502471238e003c3b8d13ac1bfe5c5d356eacfdd`.

### Actual recorded coverage

Files below are relative to the evidence root. Each named worker has
`artifacts/logging-coverage.json`, `body-references.json`,
`significant-timeline.json` and `final-business-state.json`.
The summary checks 8 body references for positive, 8 for short-prompt rejection
and 100 for checkpoint recovery. Ordinary has no observation journal.

| Data | Actual file / event counts | Limit |
|---|---|---|
| Native start/input/output/status | `native-image-prompts-worker-02/artifacts/api-history.json`: start/input/output/end each1; node_change4 | Short-prompt run has output1/error1 and no end; held is not done. |
| Request/response/stream | Same file: model_start1 / model_stream_event1 / model_end1; full bodies by referenced SHA256 | Synthetic streaming fixture, not a new process-crash or provider-SSE test. |
| Parsing and checks | Same file: parse_result1 / runtime_check6 / node_result_verification1 | Structural/integrity checks, semantic quality remains false. |
| Application and committed result | Same file: apply_input1 / data_applied1; saved business DB and output body agree | No db_commit_begin/end or db_rollback event type is recorded by this native observer. Commit is established by saved rows/application evidence, not fabricated low-level transaction events. |
| Failed business transaction | `native-image-error-session-green-02/artifacts/test-output.txt` and current test `ImagePromptFailedDatabase` | Actual IntegrityError, committed independent read and SQL zero-write assertions; temporary unit DB is not a retained worker DB. |
| Recovery and successor | `native-prompt-final-recovery-image-01/artifacts/api-history.json`: recovery_verification1 / managed_successor1 / recovery_continuation_submitted1; persisted ticket/Decision in test master DB | Original seed events remain: total starts15, model events23, JSON retry9, errors9 are historical + new; they are not 23 new POST in this run. |
| Operator/control | Worker api-history observer_control1; persisted action/control records in saved master DB | Native verifier hold is automatic with explicit_operator_request=false, not consent to resume. No new forced-stop/tail guarantee is claimed. |

Metadata-only final analysis:
`outputs/orchestrator-completion-20260930/native-prompts-final-summary.json`.

## Interventions and remaining limits

Agents had disjoint owned paths: recovery tests/fix; native contract tests and
read-only review; remaining-scope documentation. Parent alone ran Docker,
integrated and committed. Test fixtures changed explicitly: structured recovery
uses Excel, native planner uses plan; short-prompt failure deliberately seeds
one invalid source prompt. No successful business status was manufactured.

The lock scenario was stopped at its bounded diagnostic deadline. It remains
an original-runtime obstacle, not a successful recovery or a secretly fixed
business defect. Do not repeat long native exhaustion just to obtain GREEN.
Native retry/profile/recovery, complete cached-output provenance, projection
agreement, scene fanout/assembly and semantic quality remain unaccepted.
FFmpeg/ffprobe/ASR dependencies, full media/factory E2E, real modifying Decision
and cost/quality optimization remain outstanding. No provider money estimate,
full-contour completion percentage or production readiness is implied.
