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

## Outstanding accepted work

- Live browser acceptance of the current simple panel, screenshots and precise
  frontend entry/chunk hashes.
- Terminal Decision correction currently under test: complete from verified
  matching worker proof without a fourth POST; reject foreign/unverified ticket.
  `decision-terminal-red-01`: 37 tests, three actual failures; queued-wait case
  already passed. No GREEN claim until the current rerun completes.
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
