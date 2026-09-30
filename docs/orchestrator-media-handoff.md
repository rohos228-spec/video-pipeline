# Text + media goal: current handoff

Base: 2d1ad7bc139818f2b207e262c23466b4597bc9e3, existing feature/worktree.
All work is offline, synthetic, one runner. No real API/key/env/installation.
Text UI/integration evidence: docs/first-version-local-acceptance.md and
outputs/first-version-local-20260930. Reuse unchanged evidence; never rerun real pilot.

## Actual inventory (implementation queue, not acceptance claims)

| Type | Existing executor | State / effect | Restricted actions | Verification |
| --- | --- | --- | --- | --- |
| excel_gpt | enrich_xlsx | JSON/DB/status | Existing text Actions/Profiles | Existing payload/QC no-op/commit |
| images | generate_images → kie_http | Submit/job/poll/download → frame artifact | Scoped start, status, wait, human | Image decode/format/dimensions/hash |
| videos | generate_videos → kie_http | Submit/job/poll/download → frame artifact | Same; never blind resubmit | ffprobe container/video/duration |
| audio | generate_audio → elevenlabs_api → local ASR | Synchronous TTS + alignment → audio/words | Scoped start, wait, human | Audio stream/duration; ASR dependency explicit |
| subtitles | assemble uses subtitles + make_simple_ass | Local ASS, not a separate registry node | Inherits assembly scope | Cues/intervals |
| assemble | steps/assemble → services/assembly | Fixed FFmpeg commands → final artifact | Scoped local processing | Video/audio/duration |
| publish | steps/publish → publishers | External accounts | Observe/validate/dryrun only | Input validation, no external delivery |

First chunk: registry-backed capabilities and local verifiers with RED tests.
Contract chunk implemented: node_capabilities + artifact_verifier. Offline
contract-green: 7 tests, 6 passed, video/audio execution test skipped for missing
ffmpeg/ffprobe. RED contract-red: six imports failed before implementation.
Image decode/dimensions/hash, ASS structure/intervals, path containment and
unknown unsupported results are tested. No claim of runtime integration yet.
Environment evidence: contract-green/artifacts/media-environment.json under
outputs/media-v1-20260930. No ffmpeg, ffprobe, faster_whisper, torch, nemo or ASR cache.
Next: persistent media job/request correlation and shared policy; hook original
services/worker without replacing executor; then family/mixed acceptance and UI.
Persistent storage chunk: media_jobs plus optional strict synthetic media policy;
job/resource/input/config identity, permanent submit reservation, scope/stop gates,
and per-category request rows in the common budget transaction. Five tests pass
in jobs-green-02, including parallel limit, restart, uncertain submit, scope and
config revision. jobs-green-01 retains initial test schema ordering failure.
No real HTTP media integration or worker acceptance claimed at this checkpoint.
Kie transport now integrated into original run_generation/create_task/poll_task/
download. transport-green-03: 6/6 actual loopback HTTP tests; image decode, duplicate
result (one submit), known-job wait cancellation + observer reload (one submit),
unknown submit, corrupt file, provider failure and redirect denial. No worker
acceptance claimed yet. Before that: green-01 omitted untracked module after
an elevated git ownership refusal; green-02 correctly blocked audit on test's
non-UUID execution. Both failed runs preserved; protection unchanged.
common-regression-01: all 125 existing Decision/Actions/Profiles/planner/journal
tests pass on this source snapshot. Actual loaded module hashes in artifacts.
Other installed media backends checked: no av, cv2 or imageio_ffmpeg either.
Next: media worker identity binding, diagnosis, registry step-code mapping and
Decision contracts; then real worker fixture acceptance and UI. New
test_media_observation.py drafted but not yet run at this checkpoint.
Potential blocker: audio requires local ASR package + cached weights. Inventory
is recorded by media-contract; no model download or fake transcription.

No current test processes owned by this task at start; unrelated Supabase
containers left alone. Historical reports/archives/containers preserved.
Native-media observation chunk: 4/4 tests in observation-green-01; actual native
node/attempt identity, registered step code, typed failure/uncertain evidence.
Runtime regression: 25/25 in runtime-regression-01. Diagnosis regression: 25/25
in diagnosis-regression-01. Waiting running job is not diagnosed as hung.
Next: two HTTP RED tests for legacy high-level service bypass and unreachable
Kie image wrapper branch; no active runners at this checkpoint.
Native wrapper chunk: wrapper-red confirms unreachable Kie image branch and
high-level direct service missing project guard. wrapper-green-01: 8/8 pass.
Managed wrappers now invoke original Kie service with explicit catalog/profile,
no native rewrite/fallback ladder; unsupported reference upload is denied.
Native images avoid CDP in managed mode. Ordinary mode branch is unchanged.
Restart test now waits for durable running event before cancelling local wait;
previous random cancellation during audit correctly triggered commit uncertainty.
This change makes the tested checkpoint explicit; audit guard is not weakened.
Next: Decision type capabilities, verified artifact provenance/idempotence and
true image worker acceptance through the existing group runner. No active runner.
Decision capability chunk: decision-capability-green 5/5. read_contract for
native media returns registry contract/jobs/structural verification; generic
text retry/profile/finish excluded by capability allowlist. First two RED runs
exposed missing unit DB binding and invalid synthetic config, preserved. Third
RED confirmed unsafe retry_same offer. No model call in these unit tests.
Artifact chunk: artifact-green-01 10/10 HTTP tests. Existing image executor now
calls persist_artifact: verified job/file hash, deterministic Artifact UUID,
SQLite conflict-ignore and business-transaction event; rollback leaves no row.
Corrupt/tampered result cannot apply; duplicate delivery produces one Artifact.
No worker acceptance claimed yet. Next is existing group runner media mode.
Native worker acceptance: worker-image-02 GREEN. Real managed API start ->
project_steps -> original worker -> generate_images -> Kie loopback create/
status/download -> verifier -> 2 Artifact rows -> original NodeRun done.
Six HTTP operations (2 submit/status/download each); n_videos pending over
12-second boundary, pilot project #1 new. No text model POST.
worker-image-01 preserved: source clean seed had no frames, original worker
stopped on missing inputs; explicit synthetic initial frames added in next run.
Hidden anim_pr sidecar denied for managed projects (sidecar-red demonstrates
prior behavior); it must run as its own authorized observed node, not under
image attempt context. Next mixed text->image acceptance and UI. No active runner.
## Resume boundary: Docker unavailable during mixed run

Last worker-verified checkpoint: ee93326342005ce4523df4c6b609a715dee5aea7.
Active group runner session 54039 is waiting on Docker container
vp-observe-test-20260930024256-8443a6a2, mode media-mixed, results
outputs/media-v1-20260930/mixed-01. Docker start succeeded; docker wait CLI
PID 27068 remains. Do NOT start a duplicate runner until this container's state
is known and existing artifacts are collected. Its source snapshot is immutable.
The separate hung docker ps probe PID 23124/session 5043 was positively identified
and stopped; no container, engine or foreign process was stopped.
A bounded read-only docker info probe timed out after 15 seconds with no stdout
or stderr: mixed-01/daemon-probe.json. Engine outage reason is UNKNOWN, not
assumed OOM. Desktop UI state requested asynchronously from user. No repair,
restart, installation, reset, daemon/WSL/firewall change attempted.

Pending source (not GREEN): media_view/context API + media UI labels/links and
contract tests; close refuses unresolved media jobs/reservations; worker/API
reject unintegrated managed types. Audio and assembly now advertise managed=false;
video start requires ffprobe. Existing text Action unit fixture now calls actual
additive migration so the new durable job tables exist in isolated tests.
Only Python AST syntax and git diff whitespace checked after these edits.
UI version/export remains v648; no frontend build/browser tests run on drafts.
A syntax indentation error in the first draft was caught and corrected locally.
None of the older GREEN totals validate these pending changes.

Next exact sequence:
1. Recover/collect existing mixed-01, including group-result/source/loaded hashes.
   If still running, inspect bounded progress; no second concurrent runner.
2. Stage newly added source modules before runner copies git-tracked sources.
   Run media-observation (8 tests incl new UI/gate/sidecar), media-jobs (6), then
   affected common Decision/Actions regression and runtime. One runner at a time.
3. Address mixed failure narrowly, if any. No repeat native exhaustion.
4. Verify artifact route containment/hash for previews; cover file recovery after
   download-before-apply (legacy disk recovery currently lacks job provenance).
   Add final native media completion verifier; don't trust disk existence alone.
5. Finish UI typecheck/build/version/export/browser on actual image/mixed history.
6. Missing ffmpeg/ffprobe/ASR means real video/audio/assembly acceptance blocked.
   Do not install or claim those adapters complete. Video service transport is
   only partial: business apply hook/caught-error path still needs integration.
   Available subtitle generation and publication dry-run remain independent work.
7. Full final regression/evidence matrix, reproducible handoff, clean checkpoint.

See docs/orchestrator-media.md for current matrix and adding-adapter guide.
Goal remains ACTIVE and incomplete. No real API requests/key reads this work.

## Continuation: artifact safety tests staged, Engine still unreachable

The host runner session 54039 has now ended with exit 1 at
`test_observe_offline.ps1:264`: `Cannot wait for offline test container`.
This is a Docker wait-command failure, not a test result or proof that the
container stopped. Container `vp-observe-test-20260930024256-8443a6a2` must still
be inspected before any new runner/build is started. Nothing was restarted or
deleted. The last bounded inspection at 2026-09-30T00:48:14.872Z timed out after
15 seconds, with empty stdout/stderr and `ETIMEDOUT`; see
`mixed-01/daemon-inspect-user-continue.json`. Root cause remains unknown.

Two additional HTTP transport tests are prepared in `test_media_transport.py`:

- Preview must reject a changed, still-decodable image and a path outside its
  owning project (expected HTTP 409 / 403).
- Original disk recovery must apply a verified job's artifact once, preserve its
  execution/verifier proof, and reject a plausible file without a verified job.

The shared test fixture now gives every case its own temporary DATA_DIR as
well as its own SQLite DB, so recovered files cannot leak between cases.
These tests have NOT executed: no RED/GREEN claimed. Python AST/whitespace
checks alone do not validate the behavior. Production preview/recovery source
is unchanged in this continuation. The TDD gate requires an observed failing
test before those fixes. After Engine recovery, run `media-transport` (12 HTTP
cases) in the existing offline runner, preserve the failing evidence, then fix
only managed artifact handling and rerun. Do not rerun exhausted/paid pilots.

Current deliverable remains incomplete; this checkpoint only preserves tests
and the exact resume boundary. No external API, credentials or customer DBs used.
