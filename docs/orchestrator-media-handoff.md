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
Potential blocker: audio requires local ASR package + cached weights. Inventory
is recorded by media-contract; no model download or fake transcription.

No current test processes owned by this task at start; unrelated Supabase
containers left alone. Historical reports/archives/containers preserved.
