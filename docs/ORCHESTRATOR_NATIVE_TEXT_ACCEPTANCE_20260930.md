# Native plan/script extension — bounded offline acceptance

This extends the existing orchestrator/runtime. It is not completion of the full
content factory and does not authorize production or provider calls.

## Implemented boundary

- Native `plan` and `script` have individual `project_text` contracts in the
  existing capabilities registry and use the original executors. A fresh scoped
  launch uses the existing API, FSM, worker, project database and HITL.
- The native node catalog now explicitly recognizes the existing `gpt-kie`
  choice and binds its API model `gpt-5-6-sol`. Existing Vibecode defaults remain.
  This registration does not prove provider availability or tariff.
- Native inputs, extracted reply, strict JSON syntax, native field validation,
  apply, committed outbox, output hashes and original pending HITL are observed.
  Script accepts the original JSON/VOICEOVER/plain-text fallback; a valid
  VOICEOVER block is not incorrectly required to be JSON.
- `read_contract` reads the authoritative project database and exposes native
  validation, required human confirmation and absence of compatible recovery
  profiles. It does not change content or permissions.
- Pending HITL remains visible after the original runtime marks the node done.
  The current decision is read by exact committed creation/attempt identity,
  even when the ordinary timeline page has more than 500 later events.
  Reading/reloading does not approve it. This is an informational human wait,
  not a pipeline failure or permission to generate again.
- The initial launch rejects strategies absent from all selected node contracts
  before permission persistence. The existing simple panel filters strategies
  for its selection. Mixed text/image scope may permit text recovery; it never
  gives that action to the image/native text executor.

Native plan/script currently allow only `read_events`, `read_contract`, `wait`
and `human_review`. Supervisor recovery, profile changes, automatic HITL approval,
LLM code/SQL edits and native retry exhaustion recovery are **not accepted** for
these two families. Existing structured text recovery remains separately valid.

## Data authority and factual findings

The successful native plan applied 503 synthetic characters once to project.db,
and the original API exposed the same text/hash. The native script did likewise
and wrote its original local `voiceover.txt`. Both original steps created a
pending human request; the successor outside scope stayed pending.

`project_db._apply_runtime_fields` deliberately copies status, auto_mode,
runtime meta and NodeRuns, not general_plan/script_text. A first harness
assertion incorrectly demanded a content mirror in master. It was replaced
with stronger checks of project content, API visibility, verification hash,
transactional apply and master NodeRun projection. No content replication
business logic was changed and no database defect is inferred from this design.

`node_result_verification` alone is not a commit receipt. `data_applied` and
`human_approval_pending` are delivered from the original business transaction's
outbox. Original runtime/journal rollback tests remain the separate evidence
for rollback semantics. Full raw events have not been removed.

## Evidence and versions

Evidence root: `outputs/orchestrator-completion-20260930/` relative to the task
root, outside this worktree. `summarize_native.py` is an artifact-only analyzer;
it imports no application code and checks loaded/source/current hashes.

Final acceptance evidence is enumerated in `native-acceptance-summary.json`:
runtime, diagnosis, Decision/Actions/Profiles/planner/journal, launch policy,
native contracts, current script worker and native plan UI worker. Actual event
counts and files are in `native-logging-coverage.md`; per-worker files include
`api-history.json`, `body-references.json`, `significant-timeline.json`, separate
SQLite/Data_DIR, captured fixture requests and loaded module manifests.

Frontend v653 was built with the existing dependency image, `--network none`,
140 frontend assertions, TypeScript/build exit0. Delivered entry SHA256:
`4ee9c3fe6197f86a53055ce30502471238e003c3b8d13ac1bfe5c5d356eacfdd`.
Old v651/v652 exports and all failed runs remain separately preserved.
Final collection completed. `native-acceptance-summary.json` contains these
actual results, not a plan for future checks:

| Existing runner target | Result | Evidence directory |
|---|---:|---|
| runtime | 26/26 | native-final-runtime-03/artifacts |
| diagnose | 27/27 | native-final-diagnosis-01/artifacts |
| decision-final | 145/145 | native-final-decision-01/artifacts |
| managed-launch | 32/32 | native-final-managed-launch-01/artifacts |
| media-contract | 11 passed, 1 skipped | native-final-contract-01/artifacts |
| Native script original worker | accepted, one fixture POST | native-final-script-02/artifacts |
| Native plan UI/original worker | accepted, one launch, one fixture POST | native-final-plan-browser-02/artifacts |

Python totals: **242 test executions, 241 passed, one FFmpeg/ffprobe skip**.
Frontend results are separate:140 assertions, TypeScript/build exit0. These
counts do not establish that blocked media executors passed or that all factory
families are implemented. Final Python suites loaded application bytes from
`fbdf84b3132adfb159107df084fe1ba010804e14`; browser source was
`c3c95f446b1f63f19f99d308770002cca6363d4f`. The intervening change only supplied
required synthetic canvas coordinates. All source/loaded/current application
hash comparisons are zero-mismatch, including 242 loaded modules per worker.

Browser:zero page errors; original HITL stays pending after reload; the same
execution and one-request counter remain visible. Screenshots in the browser
directory:`01-before-launch.png`, `02-launched.png`, `03-complete.png`,
`04-reconnected.png`. Browser/relay/app processes finished; containers preserved.

The screenshot also correctly retains a **runtime snapshot disagreement**.
Saved native plan master has Project=`plan_ready`, WorkflowRun=`waiting_hitl`;
project.db has Project=`planning`, WorkflowRun=`new`, while both scoped NodeRuns
match (`done`/outside `pending`). `_copy_noderuns_between_sessions` copies NodeRuns
and graph additions, not WorkflowRun.status. The native executor and apply use
separate existing session paths. This evidence does not establish lost content
or justify automatic status repair. The warning remains visible and is a known
runtime/projection limitation requiring separate investigation before claiming
an unattended native chain. No comparison was weakened to hide it.

Historical first native worker evidence remains in `native-plan-worker-01..05`
and `native-script-worker-01`. Failures were: default Vibecode route without a
synthetic key; unrecognized catalog fallback; fixture trailing whitespace;
incorrect commit event name; incorrect master content mirroring; then correct
one-apply/HITL behavior. Later pagination fixtures lacked an import/timestamp,
fixed without weakening the 510-event requirement. None is presented as a real
provider quality test or a proven runtime defect without source evidence.

## Operator/developer interventions

Fresh synthetic graphs and inputs were prepared before app startup in copied
test data. No successful status, HITL approval, next-node start or recovery was
manufactured. Requests used only the loopback fixture; real .env/key access was
not used. No Windows/Docker/WSL/firewall setting, foreign process, container or
volume was changed or deleted.

One development mistake is recorded in `native-development-interventions.json`:
the existing version bump script was invoked without `--no-build`, unexpectedly
attempting host `npm install`. It failed exit1. Manifests stayed unchanged, but
the existing ignored host node_modules timestamp changed; its contents are not
claimed verified or used for acceptance. No active bump/npm descendants remained
when checked. The accepted v652 export was restored byte-for-byte from saved
evidence, then preserved before delivering the isolated v653 Docker build.
Future version bumps must pass `--no-build`; builds use the prepared container.

`native-final-plan-browser-01` failed before launch because the synthetic graph
omitted required canvas positions (`position.x`). Its sole local ensure-run
request was not a model request. The runner's own app was stopped with SIGTERM,
and logs/screenshot/results were preserved. Commit `c3c95f44` supplies coordinates
without changing UI, worker, prompts, fixtures' output, permissions or assertions.
The second browser case above passed.

## What this proves, and what remains

The native fresh-state launch/parse/apply/structural-verification/HITL boundary
is exercised, alongside the earlier six-node text/image/Decision acceptance.
This is not an unattended native plan-to-script chain: its existing mandatory
human confirmation remains. Cached/preexisting native output reuse, semantic
quality, native failed worker/exhaustion recovery and large heterogeneous graphs
still need their own contracts/provenance and acceptance. Generic registry
presence is not an accepted adapter.

| Customer requirement | Current evidence | Remaining limit |
|---|---|---|
| Single scoped launch | Existing API/UI plus native extension | Original domain HITL still requires a human |
| Observe and diagnose | Typed JSON/native field checks, transactions, exact pending approval | Wider native/scene/hero/items and semantic cause coverage |
| Safe automatic repair | Existing structured text synthetic recovery/profile loop | Native plan/script recovery not enabled; real modifying cycle not authorized |
| Other executors | Structured text group and images; native plan/script fresh boundary | Video/TTS/assembly/ASR and remaining legacy families not accepted |
| Cost and scale | Durable owner budget and saved call baselines | 1000 reservations are not a factory E2E; no measured monetary savings |
| Extensibility and ownership | Existing registry/policy/developer extension document | New families need actual worker, verifier, domain rules and tests |

FFmpeg/ffprobe are missing from the diagnostic image; ASR packages/weights are
also absent. Their installation/use is not inferred from the Docker restart.
Video/audio/assembly remain managed=false. Publication remains disabled/dry-run.
Unknown credits/prices/semantic quality are not zero or success. Production
configuration stays off; no online authorization is reused.
