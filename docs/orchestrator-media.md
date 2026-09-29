# Text and media: local handoff (work in progress)

This document describes the offline extension on feature/orchestrator-observe-v1.
It is not production acceptance. No new provider calls, customer dotenv/key
reads, dependency installations, publication or remote Git changes were made.

## Verified baseline

Text UI and full script_frames_qc evidence remains in
[first-version-local-acceptance.md](first-version-local-acceptance.md).
The accepted real read-only Decision pilot is historical evidence, not an
authorization for further requests or proof of autonomous semantic diagnosis.

The last media checkpoint with real-worker evidence is
`ee93326342005ce4523df4c6b609a715dee5aea7`.
The UI/projection and additional gates after this checkpoint are pending tests.

New evidence is under `outputs/media-v1-20260930` relative to the workspace root:

| Evidence directory | Result | Actual scope |
| --- | --- | --- |
| contract-green | 6 pass, 1 skip | Registry contracts, image and ASS structure; ffprobe test skipped |
| jobs-green-02 | 5 pass | Persistent jobs, scope/config/stop, shared atomic budget |
| wrapper-green-01 | 8 pass | Real loopback HTTP through Kie service/wrapper, known-job recovery, unsafe routes |
| artifact-green-01 | 10 pass | Above plus artifact deduplication, rollback and tamper denial |
| observation-green-01 | 4 pass | Native worker identity and typed incidents |
| decision-capability-green | 5 pass | Type-aware Decision contract and actions; SQLite unit binding |
| common-regression-01 | 125 pass | Earlier compatible Decision/Actions/Profiles/planner/journal snapshot |
| runtime-regression-01 | 25 pass | Earlier native-observation snapshot |
| diagnosis-regression-01 | 25 pass | Earlier media diagnosis snapshot |
| worker-image-02 | GREEN | Actual API → project_steps → worker → image job → verify → business DB → done |
| mixed-01 | UNKNOWN | Docker Engine stopped responding during runner; do not infer success |

These rows are not a claim that all tests ran against one final commit.
Each directory contains a source manifest, run configuration and actual loaded
module hashes. RED/failed directories remain available. The first image worker
attempt stopped because the clean seed contained no frames; the next fresh
copy explicitly supplied synthetic initial frames. No successful node statuses
were set by the test runner.

The accepted image worker execution is
`9399b4db-9751-4927-ac5a-fccc13b4cd5d`: two submit POSTs, two status GETs and two
downloads to the loopback fixture; two verified PNG artifacts; n_images done;
n_videos pending after the observed boundary; pilot project #1 still new.
No text model POST occurred in that run. Full details are local:
`worker-image-02/artifacts/group-result.json`, `api-history.json`,
`loaded-modules.json` and `test-output.txt`.

## Current capability matrix

| Family / existing node | Connected behavior | Safe actions | Verified | Remaining restriction |
| --- | --- | --- | --- | --- |
| Text / excel_gpt group | Existing Observe/Diagnosis/Decision/Actions/Profiles | Existing approved text tools/recovery | Previous text acceptance; new regression snapshots above | Re-run affected regressions after final media gates |
| Image / images | Original generate_images and Kie jobs HTTP; durable identity and verified artifact apply | Scoped start; code polling; read events/contract, wait, human review | Real worker, two files; transport failures/unknown/restart/duplicates; scope/stop unit gates | Synthetic profile only; reference upload denied; no art-quality judgment; UI pending |
| Video / videos | Kie generic jobs transport and guarded high-level entry | No blind resubmit; known-job status only | Shared transport mechanism tested with image fixture | Video worker apply integration and video verification not accepted; ffprobe absent; I2V upload unapproved |
| Audio / audio | Existing ElevenLabs + local alignment identified | Observe/read/human only | No adapter acceptance | ffprobe, ASR packages and cached weights absent; managed adapter disabled |
| Subtitles | Existing assembly.make_simple_ass; not a separate registry node | Structural local validation | ASS verifier tests | Not yet accepted through assembly worker; no speech/timing accuracy claim |
| Assembly / assemble | Original FFmpeg executor identified | Observe/read/human only | No assembly acceptance | FFmpeg and ffprobe absent; managed adapter disabled |
| Publication / publish | Existing publisher identified | Observe/read/human only | No delivery or dry-run acceptance | Dry-run adapter still outstanding; external publishing remains disabled |

Installed image inventory is in `contract-green/artifacts/media-environment.json`.
The container has Pillow; it lacks ffmpeg, ffprobe, faster_whisper, torch, nemo
and cached ASR weights. Additional inspection found no av, cv2 or imageio_ffmpeg.
Nothing was installed or downloaded to compensate.

## Actual integration points

- `node_capabilities.py`: capabilities backed by the original node registry.
- `media_jobs.py`: persistent job/resource/input/config identity; categories in
  the existing shared request-budget transaction; transactional Artifact UUID.
- `media_transport.py`: bounded client helper called by original Kie functions.
  It is neither a proxy nor an executor. Exact routes, loopback fixture origins,
  no redirects, byte/time limits, project-root containment and atomic file rename.
- `kie_http.py`, `outsee_retry.py`, `generate_images.py`: original execution,
  guarded managed entry, no native prompt rewrite/model fallback ladder in
  managed mode, no CDP fallback. Unmanaged branches retain their original path.
- `observation.py`, `diagnosis.py`, `decision_v1.py`: native attempt identity,
  media incidents and typed read_contract; no generic JSON retry for media.
- `anim_pr_sidecar.py`: a managed image cannot start the hidden animation
  prompt task under its image attempt. A separate node needs its own permission.
- `media_view.py` and existing observation UI: pending read-only projection,
  explicit job phase, structural verifier, artifact UUID link, no guessed percent.

Image file success does not mean artistic correctness. A provider job ID or
HTTP 200 does not mean a verified artifact. Local wait cancellation does not
mean provider cancellation or a refund. A status/download GET has unknown
tariff; it is not labelled free. Production media transport is disabled.

## Adding the next real adapter

1. Locate the existing registry node and actual executor. Add its capability
   in `node_capabilities.py`; keep managed disabled until its entry/gates,
   verifier and acceptance work.
2. Bind the original worker's execution/node/attempt. Add a guard before the
   original service can access a provider, including direct legacy entry.
3. Reuse durable jobs and the common atomic budget. Reserve before every
   effect; identify submit/status/download/local processing separately.
   Unknown submit cannot be replayed. A known provider job is polled, not recreated.
4. Describe an explicit provider route/source policy. Do not extend the
   Decision gateway arbitrarily. No model-chosen URL, shell, SQL, code or path.
5. Add a structural verifier using installed tools. Missing required evidence
   returns unknown and holds execution. Apply an artifact only with provenance
   in the original business transaction, with a stable deduplication identity.
6. Add typed events and diagnosis evidence. Expose only authorized read tools
   and safe actions; no universal retry for a newly registered type.
7. Extend the existing offline runner at the HTTP fixture boundary. Test
   success/failure/corruption/duplicate/unknown/restart/stop/scope, then a real
   worker path. Save actual source hashes and a current UI/build result.
8. Only after those gates, enable the type in a synthetic local configuration.
   Real provider routes, keys, pricing and expenditure remain separate approval.

## Reproducing / resuming

Use the existing cached Docker image and `scripts/test_observe_offline.ps1`.
One runner at a time; no frontend build alongside a worker run.

After Docker is healthy, FIRST inspect/collect the existing mixed-01 runner;
do not create a duplicate while its state is unknown. Source snapshots inside
test containers are immutable; host UI drafts do not change that running copy.

Then use fresh result directories for targets `media-observation`,
`media-jobs`, affected `decision-final`/runtime/diagnosis regressions.
Group modes `media-image` and `media-mixed` use the same group runner,
original API/worker and a loopback fixture in a network-none container.
The mixed mode joins six text nodes to images and keeps video outside scope.
It supplies initial image prompts explicitly; this checks runtime continuity,
not that the model invented a high-quality image prompt from a script.

Frontend build uses the existing `scripts/test_observe_frontend.ps1 -Build`.
Bump Studio with `scripts/bump_studio_version.py --no-build`, build offline,
then refresh the tracked export only after GREEN. No dependency install.
Current media UI draft has not yet been built or browser-accepted.

No final delivery/checkpoint claim while mixed acceptance, current regression,
UI, publication dry-run and the remaining safety checks are outstanding.
