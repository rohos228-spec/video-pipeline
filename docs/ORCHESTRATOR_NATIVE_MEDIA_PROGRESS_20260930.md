# Native executor expansion — factual progress, 2026-09-30

This is an intermediate record, not whole-factory acceptance. Historical reports
and every failed run are preserved. Real provider calls in this phase: **0**.
Existing worktree/feature branch, original API/planner/worker/parser/writer/FSM;
synthetic external transport, separate databases/DATA_DIR, Docker network none.

## Approved diagnostic dependency

The user approved FFmpeg and necessary dependencies only in the diagnostic
image. `vp-offline-diagnostic:20260930-ffmpeg` contains FFmpeg/ffprobe
7.1.5-0+deb13u1. Old image/tag remain. Build/tool/capability/image proofs:
`../../outputs/orchestrator-completion-20260930/ffmpeg-image-preparation-02`.
Preparation-01 failed before installation because its FROM image digest was
interpreted as a registry name; its log remains. No application or secret was
present during dependency preparation. Runtime: network none, dropped
capabilities, no-new-privileges, no home/Git/Docker socket mounted.

## Actually executed evidence

All paths below are under `../../outputs/orchestrator-completion-20260930`.
Each run retains source manifest, loaded-module hashes, configuration, stdout,
separate business databases and original events. Full bodies stay local.

| Area | Evidence | Factual result | Limit |
|---|---|---|---|
| Project runtime projection | `runtime-projection-green-01` | 5 tests plus two fresh-start subcases pass | Bounded stale status adoption; no forced aggregate repair |
| Restart admission | `managed-restart-green-01` | 6 tests pass; durable control and once-only accounting retained | Cache/DB reconstruction, not actual process-crash E2E |
| Cached SFX validation | `native-sfx-cache-green-01` | 6 tests pass; original validator runs before cached data/files/ready | Fresh retry parser coverage is separate |
| Real media verification | `media-ffmpeg-contract-green-01` | 28 tests pass with FFmpeg available | Synthetic/local files; provider quality not tested |
| Hero executor | `native-entity-worker-01` | Original GPT/Outsee path, parser, worker, PNG and Artifact accepted | One base character; managed remains disabled |
| Assembly executor | `native-assembly-worker-02` | Original FFmpeg assembly creates verified H264/AAC MP4, frame order/timing checked | ASR/subtitles excluded; approve_final pending, publication pending |
| SFX executors | `native-sfx-worker-02` | Original plan/gen both done; actual WAV duration/RMS/hash checked | Two operator API starts; no automatic-chain or managed claim; original file-only output has no Artifact rows |
| Animation journal | `animation-review-green-01` | 16 tests pass, including real failed SQL session, callback faults and inherited image context | No managed/recovery grant or semantic quality claim |
| Scene journal | `scene-observation-green-01` | 20 tests pass; actual camera parser, scoped inputs, native checks, independent committed result and missing-body evidence | No managed admission, complete fanout, Entity or scene-space acceptance |
| Animation worker | `native-animation-worker-green-01` | Original API/worker, two synthetic model replies, parser/apply/harness and committed result accepted; outside video pending | One bounded original node, no entire media graph claim |
| Malformed animation | `native-animation-corrupt-worker-01` | Actual native RuntimeError and failed result recorded; no prompts applied or successor | One explicit operator stop bounds the negative; not native exhaustion or autonomous recovery |
| Project HITL GET | `hitl-project-authority-green-02` | 12 tests pass; project authority, no initialization/write, external/dangling/corrupt storage rejection | GET only; decision POST and global pending aggregation not accepted here |
| Scene camera | `native-scene-camera-worker-01` | Actual camera request, 14 ok cells, native done/harness, frames preserved | Intentionally diagnostic/accepted=false: missing journal, chars/world/action prerequisites, assembler pending |
| Video / Seedance | `native-video-worker-02` | Original registered Seedance selection is immediately replaced by Kling fallback | Genuine routing defect; accepted=false; archive unchanged; narrow reproducer pending |
| Distinct Kling baseline | `native-video-kling-worker-01` | Original node done, videos_ready, scene_video Artifact and pending approve_videos | Final test assertion failed; not accepted; this does not fix or accept Seedance |

Test directory names must be checked against saved run configuration before
building a final aggregate; this intermediate table is not a cumulative count.

## Narrow production changes

- Release the preceding native image-prompt snapshot transaction before its
  original sibling apply; preserve partial commits and native attempts.
- Reject managed cold worker entry without matching loaded durable permission.
- Validate reused SFX payload with the original validator before ready/files.
- Reject a narrowly demonstrated stale master running projection over a newer
  completed local NodeRun; retain genuine newer starts/attempts.
- Read existing authoritative project HITL history without initialization,
  migration, sync, fallback on corrupt storage or foreign path access.
- Journal animation inputs after native backfill/unpack; actual parse results
  bind task-local response identity. Read committed result independently after
  success or error. Callback failures become explicit gaps; original native
  error/cancellation/apply remain. Closed or inherited image contexts cannot
  emit animation node facts. Checkpoints: `c6fe04f7`, `93d3eeb3`.

Scene missing hooks RED (20 executed tests, 21 expected assertion failures) is
preserved. Its implemented journal passes 20/20 at `6ace2700`; current original
camera worker acceptance is ongoing. The complete fanout/assembler is separate.
Native executor observation does not by itself open managed admission.

## Interventions and outstanding work

Fresh synthetic source data are initialized before startup. Startup-derived
script_ready is calibrated using original guards, never manually promoted to
success. Old animation/video baseline failures occurred before worker start
and are not production defects. Earlier SFX fixture incorrectly expected
business content in master; project content is authoritative and native master
projection remains runtime-only. Earlier assembly failed on HITL visibility;
the reproduced GET defect is fixed and original assembly rerun passes.

Original TTS calls ASR before clipping/persisting artifacts. Faster-whisper and
small weights are absent; separate user approval is pending. No fake ASR or
success fixture substitutes for that dependency. Existing assembly per-frame
audio inputs do not prove the fresh TTS executor.

Original regression at the captured file versions: `runtime-final-native-01`
26/26 and `diagnosis-final-native-01` 27/27 pass. Remaining:
final current framework/hash closure, scene worker proof and
worker proof, video contract/routing triage, SFX journal, accepted managed
media admission/accounting/provenance, other native families, broader semantic
verification, full graph autonomy, real crash E2E and measured cost optimization.
No real provider, production config, network policy expansion or online
authorization is enabled. Unknown prices/credits are not a monetary cap.

Artifact-only analysis outside the clone:
`../../outputs/orchestrator-completion-20260930/stage_summary.py`.
It uses stdlib to read saved evidence and writes derived summaries without
running/importing the app or changing archive events. It is explicitly outside
Git; a clean repository diff must not hide this analysis file.
