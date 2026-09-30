# Completion work: local acceptance, 2026-09-30

Scope: extension of the existing housepc runtime in `feature/orchestrator-observe-v1`.
Original application checkpoint: `01583e55f87c171824c99e553cbc901830929325`.
All new runs are free synthetic tests with isolated SQLite/DATA_DIR and Docker
`--network none`. No real provider, customer environment, credentials, working DB,
push, PR or deployment. Historical evidence is preserved.

## Confirmed implementation

The existing panel has a simple managed-launch section. One request commits the
observation scope, managed permission, budget, Decision binding and audit before
using the original start/FSM. It does not create a worker. Advanced observation,
diagnosis, Actions, Profiles and Decision controls remain available in details.

`POST /api/projects/{project_id}/observations/launch` and the read-only
`GET .../launch-options` use an explicit immutable run/graph revision, selected
supported nodes, strategies, budget and idempotency key. `managed_launches` is an
additive control table; existing business tables still belong to the runtime.
Same-key replay cannot dispatch again, including after reload. Interrupted or
failed dispatch is durably held with Decision disabled. Generic resume cannot
remove that hold. An uncertain frontend submission is retained locally; the
simple panel only offers a status read, not an automatic second submission.

Initial entry avoids the legacy manual UI reset path: no stop/backoff removal,
attempt reset or second worker. Graph, all NodeRun states and attempt counts must
agree across master/project. A stale `new` master status may be refreshed from
the authoritative idle project DB when those checks pass. That projection is
recorded in the launch policy. Other status disagreements remain blocked.

## Evidence

Paths below are relative to the task root, outside the worktree:
`outputs/orchestrator-completion-20260930/`.

| Check | Actual result | Evidence |
|---|---|---|
| Baseline runtime | 25/25 | `runtime-current-01/artifacts/resume-01/` |
| Baseline diagnosis | 25/25 | `diagnose-current-01/artifacts/resume-01/` |
| Launch API, interrupted dispatch, policy, replay | 26/26; 156 loaded modules matched | `managed-launch-policy-green-01/artifacts/acceptance-summary.json` |
| Launch and separate idle project projection | 27/27 | `launch-projection-green-01/artifacts/` |
| Frontend contracts, TypeScript, Next export | exit 0; studio v651 | `launch-ui-green-01/artifacts/` and `source/source-manifest.json` |
| One launch → original six-node text worker | all six done; five fixture model POST; nonempty 8,113-byte application HTML | `launch-text-worker-04/artifacts/acceptance-summary.json` |
| Text scope boundary | four normal five-second worker cycles; n_script pending; no extra model POST | same summary and full `group-result.json` |
| One launch → original image worker | two verified PNG artifacts; 2 submits + 2 polls + 2 downloads; requests_used=6 | `launch-images-worker-01/artifacts/acceptance-summary.json` |
| Image scope boundary | n_videos pending; pilot project #1 new | same summary |
| Source provenance | 264 text / 250 image loaded modules; no current source mismatch at capture | respective `loaded-modules.json` and summary |

Frontend checkpoint: `77c09e03`. Backend launch projection checkpoint:
`dc1e42e2`. Each test source manifest records the exact checked files; a later
documentation/test-only commit does not alter those application hashes.
The baseline regression results precede the new backend changes. They are not
claimed as a final regression of the completed implementation.

The text and image probes use different existing node families and graph scope.
They demonstrate absence of a supervisor group-name requirement for initial
launch. They do not establish support for arbitrary graphs, transparent config
paths, scene-design or other executors.

## Preserved failures and corrections

- `managed-launch-red-01`: five missing API cases, 18 existing Actions cases
  passed. `managed-launch-policy-red-01`: generic resume bypass and missing
  options route reproduced. Corrected, 26/26 GREEN.
- `launch-ui-red-01`: executed contract failure for missing selection validation.
  The original launcher exit -1 is retained; the same container's separately
  collected test result is exit 1. GREEN build is a fresh source snapshot.
- `launch-text-worker-01`: historical seed master/canvas revisions disagreed;
  launch rejected before model transport. A consistent graph was constructed
  on a fresh synthetic copy before app startup, using original node contracts.
- `launch-text-worker-02/03`: authoritative project readiness differed from stale
  master `new`. The original GET computed `script_ready`. The strict guard was
  reproduced in a two-DB unit test (`launch-projection-red-01`: 27 tests, one
  error), then the bounded projection fix passed 27/27 and worker-04.
- No fixture input, model or prompt was changed after launch to manufacture
  success. No successful NodeRun state was assigned by the test runner.

The text probe has exactly one launch dispatch. Following nodes belong to the
original pipeline. Verification evidence is read; there is no runner Actions
verify/recovery/next-node POST in this new healthy scenario. The image fixture
deliberately returns identical image bytes for the two different frames; each
frame has one independently recorded artifact. This says nothing about content
quality or the quality of a real model.

## Docker coordination

The user authorized coordination with task `Создать LMS-платформу с нуля`.
Our containers run sequentially without host ports, pulls or image builds. Its
seven Supabase containers, networks and volumes were not modified. Start CLI
delays are preserved as launch failures, not Python failures or valid TDD RED.
After an interruption the Engine pipe and Desktop/backend processes were absent.
The already installed Desktop was launched normally under prior authorization;
no reset, reinstall, WSL, daemon setting or firewall change was made. The
never-started saved container was resumed, not recreated.

## Browser and deterministic Decision completion

`launch-browser-03/artifacts/acceptance-summary.json`: actual current v651 panel,
one UI launch POST, all six original nodes done, five loopback model fixture POST,
8113-byte HTML generated by the application, outside `n_script` pending. Reload
restored the same execution without a second launch. No page errors; 264 loaded
modules matched both snapshot and source. Screenshots: `01-before-launch.png`,
`02-launched.png`, `03-complete.png`, `04-reconnected.png` in that directory.
Entry SHA256 `c9e2dda8ce1ba684544314d369b95f3c5bcbd288d8c0bca02e8715cb5bf6ceac`;
all 47 frontend file hashes are preserved in `frontend-files.json`.

The built-in browser failed before opening its page. Existing bundled Playwright
was used without installing packages, allowing requests only to the existing
127.0.0.1 relay. Browser-01 timed out without a model call. Browser-02 exposed a
runner race: it treated expected persisted `dispatching` as a failed launch and
cancelled the application while its UI POST was still pending. The runner now
waits for dispatch completion; it still requires `submitted`, genuine worker
transitions and complete outputs. Both failures and SQLite state are retained.
All our browser processes and relay were stopped; containers are preserved.

`decision-terminal-green-01/artifacts/`: 37/37 GREEN after three actual RED
failures. A matching recovery ticket with persisted `verified=true` ends the
Decision deterministically, including on step three and after restart. Foreign,
missing or unverified tickets escalate; queued work continues waiting. This is
a unit/durability proof, not a new real model or full worker recovery run.

## Outstanding accepted work

- Regression of owner budget policy and legacy enable paths after the new
  additive run allowance; durable bounded wait and broader safe diagnosis.
- Broader typed diagnosis/strategies, approved profile catalog and application
  across node contracts, owner scale policy/common allowance, reproducible cost
  comparison, developer extension contract and final current regression.
- Video/TTS/ASR/subtitles/assembly are not accepted managed adapters. Existing
  evidence lacks FFmpeg/ffprobe and ASR packages/weights. No dependency was
  installed and no executor was marked managed merely because an interface exists.
- Production remains disabled. Online modified execution, provider quality,
  media provider routes and monetary optimization require separate evidence and
  authorization. Request count and synthetic usage are not dollar caps.

The full customer contour is still in progress. This report claims only the
specific checks above; it does not replace previous reports or archives.

## Further completed evidence and Docker interruption

Artifacts below are under `outputs/orchestrator-completion-20260930/`. Previous
reports, original events, failed runs and seed checkpoints were preserved.

| Check | Actual result | Evidence |
|---|---|---|
| Owner allowance, parallel reservations/restart/new-execution denial | 24/24 GREEN; 1000 reservations, 1001 blocked | `owner-budget-green-02/artifacts/` |
| Unified owner-limit launch | 29/29 GREEN | `launch-owner-green-01/artifacts/` |
| Durable bounded wait + exhausted domain/API read diagnosis/native-active guard | 41/41 GREEN | `decision-wait-green-01/artifacts/` |
| Registered profile variants and contract instead of fixed group name | 21/21 GREEN | `profiles-catalog-green-01/artifacts/` |
| Preapproved profile context/selection + one recovery ticket/replay | 25/25 GREEN | `profiles-selection-green-01/artifacts/` |
| Atomic launch catalog approval and stale/foreign/no-approval rejection | 31/31 GREEN | `launch-profiles-green-01/artifacts/` |
| Genuine archived QC exhaustion → automatic Decision → original recovery → verify → image | accepted; 2 Decision POST, 1 worker POST, 3 media operations | `decision-worker-image-02/artifacts/` |

The last worker case retained the seed execution UUID and historical 22 requests,
with a distinct run/results/database instance. Shared counter 22 → 28. One
QC `node_start`, one `data_applied`; confirmed verification preceded one image
successor and actual verified PNG. Native exhaustion was not reconstructed:
step_failure records 9 failures and source events contain 9 native attempt UUIDs;
the archived NodeRun.attempts=0 was independently preserved. No operator node
start/recovery POST, no third summary model POST, no execution of outside video
or pilot project #1. This is synthetic behavior, not real-model quality evidence.

`profile-progress-summary.json` records exact snapshot manifests and loaded
module hashes. All completed snapshots have zero loaded/snapshot mismatches.
The 31 launch tests also match all currently loaded application bytes. The 25
profile-selection tests predate the subsequent incomplete-terminal fix in
`decision_v1.py`; they are not a claim of complete current regression. The genuine
worker proof predates the new catalog integration, whose worker proof is pending.

Executed RED: `decision-incomplete-red-01`, 42 tests, one failure — complete=false
`model_end` incorrectly allowed automatic Decision POST. The implementation now
requires complete=true or explicit known HTTP rejection. Two additional tests
cover known 429 versus error without a known HTTP outcome. GREEN on this exact
version remains pending; no provider was called by the failed unit test.

Executed UI RED: `launch-profiles-ui-red-01`, missing catalog validation. Current
source v652 adds explicit profile checkboxes/catalog hash, no profile selected
by default, and persisted wait deadline/review wording. The frontend build was
started in `vp-observe-ui-build-c5b5cb8419`, but its outcome is UNKNOWN. Old v651
export remains intact and is not acceptance for these new UI changes.

At 15:32 MSK the Engine named pipe still connected, but bounded GET /_ping had
response_timeout. A separate inspect later returned HTTP 500 on
`/v1.56/containers/vp-observe-ui-build-c5b5cb8419/json`. Desktop/backend processes
still existed; no cause is asserted. Evidence: `docker-readiness-1532.json` and
`docker-interruption.json`. This is neither frontend code RED nor GREEN.
Only verified own Docker wait CLI PID26612 was stopped; the container, other
agent's processes/networks/volumes and application materials were preserved.
No Docker restart, system change, install or further test start was performed.

`decision-profile-worker-image-01/prepared-test.json` identifies the prepared,
never executed copy for actual worker profile verification:
`vp-observe-test-20260930152759-6811ffe3`. It reuses the existing scenario/runner,
not a new stand. Synthetic control approval is documented separately from a
native initial launch; the runner does not create an overlay or recovery ticket.

Remaining next checks: collect existing frontend container outcome after Engine
recovery, correct narrowly if needed, run current Decision/profile regression,
execute that prepared worker case, export a successful v652 build and capture
only the changed catalog/wait UI. Video/assembly still lack ffmpeg/ffprobe;
installation approval is pending. ASR packages/weights remain absent. Do not
recreate historical exhaustion or claim all families ready.

Developer contract: `ORCHESTRATOR_NODE_EXTENSION_CONTRACT.md`. Owner scale test
does not prove 1000 pipeline operations, dollar cap or cost savings. No real API,
keys/.env, production/master/customer DB, push/PR/deploy or new dependency.

## Resume after operator Docker restart: current regression and profile worker

The operator restarted Docker. Engine 29.8.1 answered; no daemon/system/foreign
container changes were made by this agent. The interrupted frontend container
was exited 255: contract checks had passed, but only a build-start marker existed.
The interruption artifacts are preserved separately, never reported as GREEN.

`decision-profile-worker-image-01` actually ran after restart and failed safely:
Decision escalated with `input/config revision changed or not recorded: settings`.
No recovery ticket or worker attempt was created. The fixture imported Profiles
(then models/settings) before configuring KIE_API_BASE_URL. Commit `843919bc`
moves fixture environment setup before application imports and preserves early
human-review reasons. It does not change the original checkpoint or revision guard.
The abandoned preparation `-02` (missing explicit DenialSeed) and unexecuted `-03`
(superseded by the subsequent Decision fix) remain preserved.

`decision-profile-current-regression-01` executed 144 tests: 143 passed, one
failed. Incomplete terminal blocked model HTTP but still created an automatic
DecisionRecord. Commit `f41de424` additionally defers automatic record creation
until native/request state is settled. Explicit diagnostic context access keeps
its existing semantics. The same assertion was retained, not weakened.

Current `decision-profile-current-regression-02/artifacts/`:

| Module | Actual result |
|---|---:|
| Decision runtime adapter | 41/41 |
| Decision durability/policy | 44/44 |
| Actions contract | 24/24 |
| Profiles | 25/25 |
| Planner boundary | 2/2 |
| Journal | 8/8 |
| Total | **144/144 GREEN** |

All 183 loaded application modules match the snapshot and current working bytes.

`decision-profile-worker-image-04/artifacts/`: actual Observer/DecisionSupervisor
selected `synthetic-format-v1`; one immutable overlay reached the original worker
body (alternate synthetic model, low, 1024 cap, registered format suffix). One QC
recovery, one apply and confirmed verification preceded the image successor.
Decision completed deterministically after two model steps; no third summary POST.
The common request counter retained 22 historical operations and reached 28:
2 Decision POST + 1 worker POST + image submit/poll/download. Recovery is verified,
Decision is done, pilot #1 remains new and outside video pending. All 267 loaded
application modules match snapshot/current bytes. No native exhaustion repeated.

This copied-checkpoint test records preapproval in diagnostic control tables;
it is separate from the genuine initial-launch API/UI evidence. The fixture
does not create the overlay/action or manufacture successful business statuses.
Source execution UUID is retained as lineage; result run_id and test DB differ.

Local artifact analysis: `resume-acceptance-summary.json` and
`measured-call-baseline.json`. Historical ordinary healthy text and managed text
both used five provider-fixture POST. Genuine same-profile and catalog-profile
recovery copies both used six additional accounted operations. This proves no
extra call in these cases, not monetary savings. Real provider tokens/credits/USD
remain unknown for synthetic calls; 1000 reservations are not 1000 pipeline runs.

At this checkpoint frontend resume, current runtime/diagnosis regression and
changed catalog browser acceptance are still pending. Full-contour completion
is not claimed. Video/assembly lack FFmpeg/ffprobe; ASR lacks package/weights;
other legacy text/scene/hero/items/music/SFX families still need individually
accepted managed contracts/executors. Production remains disabled.
