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
