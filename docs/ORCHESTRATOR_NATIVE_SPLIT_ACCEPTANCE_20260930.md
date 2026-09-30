# Native split — bounded managed extension

This extends the existing orchestrator and original `split_frames` executor.
It is separate from the earlier plan/script/HITL acceptance. No real provider,
customer environment, working database, push, deployment or dependency install
is authorized by these synthetic results.

## Implemented

- `node_capabilities.py`:native split `frame_set` contract, actual voiceover
  snapshot, original extraction evidence and output verification. Native JSON,
  dash blocks and the original local voiceover fallback remain distinct from
  mandatory-JSON structured operators. Only read_events/read_contract/wait/
  human_review are permitted. No split supervisor recovery or profile override.
- `xlsx_step_runners.py`:records the actual existing voiceover file before the
  original model transport and extracted frames before original application.
  No native prompt, model selection, extraction, apply or frame creation is
  replaced. Original aliases use the same precedence as `db_v2.replace_all_frames`.
- `observation.py`:verifies actual original-executor frame rows after return.
  Scoped project, two or more frames, nonempty unique UUID, sequential unique
  numbers, nonempty voiceover and complete ordered voiceover coverage after
  whitespace normalization are checked. Unknown input never proves success.
- `decision_v1.py`:read_contract exposes the split contract and current scoped
  frame count/hash. It does not mistake frames for a media job or infer prior
  success merely from current DB rows. Compatible recovery profiles are empty.
- `actions_v1.py` and `advance_runner.py`:a durable held execution blocks a new
  native worker tick before opening a business session or creating an attempt,
  including after volatile permission cache loss. Observation stop alone and
  unrelated projects retain admission. The existing worker, FSM and scheduling
  engine remain; in-flight work is not cancelled by this entry guard.
- Verifier/policy hold records use `explicit_operator_request=false`; ordinary
  operator hold/resume retains its existing default. A verifier/policy caller
  cannot automatically resume. Existing historical audit rows are not rewritten.

The original split commits its frame replacement before final managed
verification. A rejected result may therefore be **already applied**, not rolled
back. It is retained with committed outbox and failed verification evidence;
execution is held and continuation is prohibited. No automatic undo, deletion,
retry, data correction or fabricated successful status is performed.

## Executed RED and developer interventions

| Preserved case | What it established |
|---|---|
| native-split-contract-red-01 | Missing explicit split contract/verifier; two executed import errors |
| native-split-worker-01 | Copied seed had usable 133-character voiceover.txt, while prepared DB/fixture used 503 characters; exact coverage rejected it |
| native-held-entry-red-01 | A held native job still opened its business session; HTTP gate alone did not prevent new cached worker entries |
| native-hold-audit-red-01 | Automatic hold provenance had no distinct API/metadata path |
| native-split-alias-red-01 | Observed extraction did not match original alias precedence, producing a false coverage failure |

The original helper intentionally prefers a usable existing voiceover.txt.
The fresh synthetic preparation now initializes its **copied** file consistently
with the synthetic DB before startup. Original seed/archive files remain intact.
The first failed run made one model POST and three native attempt entries; later
entries submitted no model request. They are not hidden or reclassified as
successful recovery. The narrow durable job gate addresses these repeated
entries; it does not clear native stop/backoff or change prompts to force success.

The accepted positive precursor `native-split-worker-02` has one original worker
POST, two unique frames, preserved 503-character input, done source and pending
outside node. The final current-byte regression and lost-tail case below were
executed separately; their outcomes are not inferred from that precursor.

## Evidence and acceptance boundary

Results root outside the worktree:
`outputs/orchestrator-completion-20260930/`. The artifact-only analyzer
`summarize_split.py` imports no application module and compares captured source,
loaded application and current file hashes. It creates
`native-split-acceptance-summary.json`, `native-split-logging-coverage.md`, and
per-worker significant timeline/body-reference files. Full original events,
SQLite/Data_DIR, captured fixture requests and failures remain local.

Implementation checkpoint:`1a48e121387b93c825c89e44470d652790209b06`.
Additional test-only assertion checkpoint:`400e03430d454feb83d04c44243fcf3c562f6f71`.
All final captures match the current application bytes: zero source/loaded/current
hash mismatches. Some snapshots were created before the implementation checkpoint
while its files were uncommitted; their manifest/hash proof, rather than HEAD
alone, establishes the tested version. Exact per-file hashes and manifest hashes
are retained with each capture and in the summary.

| Completed suite / result folder | Executed | Passed | Skipped |
|---|---:|---:|---:|
| native-split-final-runtime-01 | 26 | 26 | 0 |
| native-split-final-diagnosis-01 | 27 | 27 | 0 |
| native-split-final-decision-01 | 149 | 149 | 0 |
| native-split-final-contract-01 | 15 | 14 | 1 |
| native-split-final-managed-launch-01 | 35 | 35 | 0 |
| Total test executions | 252 | 251 | 1 |

The dependency skip is the existing absent-FFmpeg check. Counts include inherited
tests shared across suites; they are not 252 unique scenarios. Decision coverage
comprises runtime adapter 41, Decision 46, Actions 27, Profiles 25, planner 2,
journal 8. All completed containers exited 0 and were preserved.

| Actual original-worker case / result folder | Recorded result |
|---|---|
| native-split-final-worker-01 | One API launch, one attempt/model fixture POST, two unique frames numbered 1/2, full 503-character input preserved, one committed replacement, verify passed, source done, outside pending |
| native-split-loss-worker-01 | Valid JSON; one committed replacement but lost voiceover tail, verify failed, held execution, source queued rather than done, outside pending; still one attempt/POST after 12 seconds; no recovery or Decision action |
| native-split-final-ordinary-01 | Managed off: original six-node group 6/6, five model fixture POST and original nonempty 8113-byte HTML; test runner bounds the ordinary case, not a managed gate |
| native-split-profile-regression-01 | Existing copied genuine-exhausted seed: preapproved profile selection, one QC recovery/apply/verify, image successor; 22 historical reservations retained, 6 new, final 28; Decision completed after 2 requests without a third summary POST |

The last case uses the existing `media-decision-recovery` runner with
`synthetic-format-v1`, not a new pilot. Its six new operations are two Decision
POST, one QC worker POST, one image submit, one status and one download. Native
exhaustion was not repeated. Pilot project #1 remained new and outside video
pending. The history's node_start/event totals include inherited activity and
must not be mistaken for newly performed actions.

For each worker, `significant-timeline.json` indexes significant unchanged events
and `body-references.json` identifies local full bodies/hash references.
`native-split-logging-coverage.md` links actual input, model/parse/check,
committed apply and verification events. Loss-case rows remain applied; a held
execution is not rollback. Current process admission also has unit evidence for
cache loss/restart, foreign project isolation and observation-stop behavior;
the four worker runs do not independently prove a process restart.

Frontend source/export is unchanged from v653; existing 140 assertions,
TypeScript/build and native browser evidence are retained by matching file
hashes, not rerun as unchanged scenarios. Entry SHA256:
`4ee9c3fe6197f86a53055ce30502471238e003c3b8d13ac1bfe5c5d356eacfdd`.
All captured frontend source and exported-file hashes were compared, not just
the entry file. Retained screenshot:
`native-final-plan-browser-02/artifacts/04-reconnected.png`. It shows the earlier
native plan browser acceptance and the documented runtime projection warning;
it is not presented as a new split-specific browser run.

## Remaining limits

| Requirement | Current bounded support | Still not established |
|---|---|---|
| One user launch | Existing managed API/UI, fresh native split and previous text/image graphs | Whole content factory across all native families |
| Understand result | Actual input/extraction/committed apply/frame integrity evidence | Artistic quality, meaning or good camera/scene design |
| Safe response to bad result | Deterministic held execution; no new attempt/POST after hold | Automatic rollback or native split recovery |
| Different graphs | Existing text/image plus native plan/script/split contracts | Large heterogeneous graphs and native agent fanout |
| Cost | One request positive and one request bad split fixture; shared durable limits | Dollar tariff/cap and proven monetary savings |
| Reuse | Ordinary original native cache path preserved | Managed cached/preexisting frames without captured attempt/input provenance |
| Other families | Earlier structured text/images acceptance retained | Scene/hero/items, video/TTS/ASR/assembly/music/SFX managed acceptance |

Cached native split without a captured input remains unknown and held. It is not
declared an accepted reuse/optimization path. The plan/script runtime projection
disagreement in the preceding report remains unresolved and visible.

FFmpeg/ffprobe and ASR package/weights are absent. Dependency permission is
pending; Docker restart did not authorize installation or downloads. Video/audio/
assembly remain disabled for managed production use. Publication stays dry-run/
disabled. Unknown usage/credits/prices are not zero; synthetic requests do not
measure real model or semantic diagnostic quality.
