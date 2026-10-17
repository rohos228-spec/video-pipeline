# Orchestrator status — current, per component

> **Исторический снимок** на `400933de` (2026-10-01). Числа тестов («94 failed» и т.п.) и состав работ с тех пор изменились; действующие правила и числа: `docs/TESTING.md` (числа собирает `scripts/test_report.py`), `docs/audit/W40_FINAL_PLAN.md`, `docs/LIVE_RUNS.md`.

Snapshot at `git log --oneline -1` → `400933de` on `feature/orchestrator-observe-v1` (2026-10-01).

This is a compact index. The authoritative, evidence-linked detail lives in
[`ORCHESTRATOR_CURRENT_STATE.md`](ORCHESTRATOR_CURRENT_STATE.md) (dated 2026-09-30, still
accurate as of this snapshot except where this file notes a delta below) and
[`ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`](ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md).
`HANDOVER.md` and `HOW_TO_RUN.md` describe an earlier (pre-Studio, Telegram/CDP-only)
architecture and should **not** be treated as current. `ARCHITECTURE_AUDIT.md` and
`BACKLOG.md` predate the Observe/Diagnose/Decision/Actions/Profiles work entirely.

Legend: 🟢 done & verified · 🟡 partially done / synthetic-only · 🔴 not done · — not applicable.

## Delta since `ORCHESTRATOR_CURRENT_STATE.md` (2026-09-30 → today)

| Change | Detail |
|---|---|
| Video routing bug **fixed** | `outsee_retry.py`: `primary_is_kling` was set from `primary_backend == "kie"`, which is also true for KIE-hosted *primary* models (Seedance) — any Seedance selection was immediately misrouted onto the Kling fallback's request contract. Now uses `_video_fallback_active()` (has the primary ladder actually been exhausted) plus an explicit `kie` flag through `_dispatch`. 9 new tests in `tests/observe_v1/test_native_video_routing.py`, all passing against real `ffmpeg`. Commit `153e6ac0`. |
| SFX plan validation **hardened** | `sfx_plan.py`/`generate_sfx.py`/`observation.py`: SFX plan generation input (frames/timeline/prompt) and the selected plan source (model/checkpoint/file) are now journaled; `observe_sfx_result` cross-checks committed WAV files against the captured plan (count matches plan, path contained under the project's `sfx/` dir, real duration/RMS decoded via `wave`). Commit `050bc45c`. |
| `sfx_plan`/`sfx_gen` capability gap **fixed** | These were real, wired `WORK_NODES` entries but absent from `node_capabilities._CAPABILITIES`, silently falling back to `"unsupported"` and relying on ad hoc `capability.node_type in {'sfx_plan','sfx_gen'}` special-casing in `observation.py`. Added honest `NodeCapability` entries (`managed=False`, matching the "registered legacy family, managed lifecycle unaccepted" status already documented for this family). |

## Per-component status

| Component | State | Done | Verified | Not done | Next step |
|---|---|---|---|---|---|
| **Observe** (`app/services/observation.py`) | 🟢 | Durable append-only journal of node/model lifecycle, survives restart, redacts credentials | Restart/resume tests, duplicate-event protection | Not total SQL/lock tracing of every legacy branch | — |
| **Diagnose** (`diagnosis.py`) | 🟢 | Deterministic rule-based incident classification (`invalid_json`, `missing_payload`, `contract_rejected`, `api_error`, `media_failure`, …) | Unit/integration, `RULE_VERSION=diagnose-v1.5-native-hitl` | Root cause often stays `hypothesis=null` — no real root-cause reasoning claimed | See M4 in the standing plan: extend classification per failure class where gaps are found |
| **Actions** (`actions_v1.py`, `actions_recovery.py`) | 🟡 | One-shot idempotent recovery for exhausted text `invalid_json`/`missing_payload`; budget/hold/verify/close | Worker E2E (`recovery-worker-image-01`) | Only text-family recovery; no media-family recovery | Extend to `images`/`sfx_*` once those families get real managed lifecycles |
| **Decision** (`decision_v1.py`) | 🟡 | Bounded (3-step) model-in-the-loop dialogue, fail-closed, `finish` requires real verification | One real read-only cycle (`read_contract`→`human_review`) + synthetic suite | No real model-driven *modifying* recovery has ever run | Needs an explicit, user-approved real trial before claiming autonomy beyond text |
| **Profiles** (`profiles_v1.py`) | 🟡 | Overlay can change `model_id`/`reasoning_effort`/`max_output_tokens`/one fixed `prompt_variant` for one node | Unit + one synthetic worker run | `PRODUCTION_CATALOG={}` — no real profile exists yet | Populate a real production profile only when a concrete model swap is requested |
| **Gateway/ledger** (`decision_config.py`, `media_jobs.py`, `scripts/diagnostics/gateway.py`) | 🟡 | Egress pinned to one IP, atomic budget reservation, ACK-before-repeat, transactional terminal states | Fault-injection/replay acceptance | Hard-restricted to loopback/synthetic — does not gate the real provider calls made by `generate_images.py`/`generate_videos.py`/`generate_sfx.py` | Architectural decision needed (flag to user, see plan's stop points): extend gateway to real provider calls, or keep it synthetic-only |
| **Registry / plugin story** | 🔴 → in progress | Two hand-kept dicts (`node_registry.WORK_NODES`, `node_capabilities._CAPABILITIES`) + one `steps/<name>.py` file per node | Existing node types work | No single lookup; a new node type requires editing 2-4 places by hand | M3 of the standing plan: thin `NodeSpec` wrapper, no new engine |
| **Model/Agent/Strategy/Prompt separation** | 🟢 (conceptually) / 🟡 (documented) | Already 4 distinct concepts across `text_llm_catalog.py`/`vibecode_catalog.py` (model), `scene_design/agents.py` (agent), `node_step_params.py`/`gpt_operator.py` (node strategy), `prompt_library.py` (prompt version) | Code reads consistently with this split | Not documented anywhere as an explicit map | See `ARCHITECTURE.md` |
| **Text** (`excel_gpt`/`script_frames_qc`) | 🟢 | Full Observe/Diagnose/Actions/Decision/Profiles wiring | Worker-accepted (synthetic) | Real autonomous recovery not run | — |
| **Images** | 🟡 | Real adapter (submit/job/poll/download via `kie`/`outsee`) | Worker-accepted (synthetic) + recovery→image | No real provider calls made in acceptance | — |
| **Videos** | 🟡 | Real Seedance/Kling routing via `kie`, well-engineered attempt ladder (`video_error_policy.py`) — **routing bug fixed today** | 9 unit tests + broader `test_outsee_video_fallback.py` suite | `managed=false` — not yet wired into the Decision/Observer managed lifecycle | Wire `videos` into `_CAPABILITIES` as `managed=True` once a worker E2E acceptance exists |
| **Audio/TTS/ASR** | 🔴 | ElevenLabs + local-synth fallback for SFX only; TTS/ASR executor exists but unmanaged | — | FFmpeg/ffprobe/ASR weights absent outside the sandboxed diagnostic image; no accepted TTS/align/apply/verify | Blocked on user-approved FFmpeg/ASR install (already partially approved for the diagnostic image only) |
| **SFX** (`sfx_plan`/`sfx_gen`) | 🟡 | Real ElevenLabs/local-synth generation, plan validated against committed WAVs — **capability gap fixed today** | 18 unit tests + `native-sfx-worker-02`/`native-sfx-cache-green-01` acceptance | Two operator API starts, no automatic chain, no managed claim, no Artifact rows | Decide whether to pursue managed lifecycle for this family next (small, already has the most journaling work done) |
| **Subtitles/Assembly** | 🔴 | Real FFmpeg subprocess calls with real exit-code propagation; structural ASS verifier exists | 28 FFmpeg-contract tests (diagnostic image only) | No managed run; no subtitle-worker/assembly E2E | Blocked on FFmpeg outside the diagnostic image (user decision) |
| **Publish** | 🔴 | Read-only preview/dry-run only | Synthetic preview/policy checks | Real external publish blocked by design | Out of scope until a real publish target is chosen |
| **Cost control** | 🟢 (native pipeline) / 🟡 (Decision layer) | `step_failure_policy.py` (3 fails→30min sleep, 3 cycles→pause, manual reset) + `video_error_policy.py` attempt ladder are real and used in production; Decision layer's own request-budget ledger works but only inside the synthetic/opt-in envelope | Tested | No dollar-cost model anywhere — `cost_status: 'unknown_tariff'` everywhere by design | Document clearly (M6) rather than build a new cost engine |
| **Tests** | 🟡 | ~1950 test functions; error→diagnosis→action→continue ✅, error→HITL ✅, restart/resume ✅, duplicate-event protection ✅, new-node mechanics ✅ | Green (minus pre-existing, unrelated environment failures — see below) | No single test drives the full pipeline start-to-finish; no "add a node" test yet; `test_native_video_routing.py` hard-fails (not skips) without ffmpeg | M5 of the standing plan |
| **OSS readiness** | 🔴 | Not assessed yet | — | — | M6 of the standing plan |

## Known pre-existing, unrelated test failures (not caused by today's changes)

Confirmed via before/after comparison (stashed today's diff, re-ran, same failures at baseline):
`test_artifact_recovery_newer_disk.py::test_recover_video_rebinds_when_disk_newer`,
`test_media_polling_retry.py::test_outsee_poll_recovers_from_transient_502_streak`,
`test_montage_regen_api.py::test_execute_video_regen_api_skips_cdp`,
`test_outsee_image_ui_parity.py::test_outsee_image_model_slugs_match_live_ui`,
`test_outsee_veo_settings.py::test_ensure_public_requires_yandex_no_public_hosts`,
`test_outsee_video_fallback.py` (3 cases),
`test_project_gen_models.py::test_patch_image_and_video_generator`,
`test_status_sequencing_gates.py::test_images_ready_auto_advance_prefers_anim_pr_not_videos`.

Root cause appears to be a missing `action_execution_controls` table in this
dev environment's fresh SQLite setup (`no such table: action_execution_controls`) —
likely a fixture/migration ordering issue specific to this freshly-created `.venv`,
not a regression from any change made today. Flagged here rather than fixed
silently; worth a dedicated look before it's mistaken for a real regression later.

**Full-suite run** (`pytest tests/ --ignore=tests/observe_v1`, ~2600 tests,
355s): 94 failed, 2540 passed — a much larger set than the 8 above, spanning
completely unrelated areas (`test_studio_launcher_encoding.py`,
`test_xlsx_studio_full_sheet.py`, `test_montage_board_frames.py`,
`test_shot_menu.py`, `test_text_llm_provider.py`, `test_db_v2_api.py`, etc.).
Spot-checked two of these in isolation
(`test_studio_launcher_encoding.py`, `test_xlsx_studio_full_sheet.py`) —
both fail on their own, independent of any change made today: they assert
exact byte/string content against `scripts/studio.ps1` and the Next.js
frontend source (an em-dash check, exact UI strings) that has simply drifted
from what the tests expect. This is frontend/script content no commit today
touched. Not individually re-verified for all 94 — the failure signatures
(frontend string drift, xlsx/montage/scene-design content assertions,
`test_db_v2_api.py`'s SQLAlchemy errors matching the same missing-table
pattern above) are consistent with pre-existing environment/content drift in
this freshly-installed `.venv`, not with the narrow diffs made in this
session (`outsee_retry.py`, `sfx_plan.py`, `generate_sfx.py`,
`observation.py`, `node_capabilities.py`, `node_spec.py`,
`test_native_video_routing.py`). Worth a dedicated triage pass before relying
on this `.venv`/checkout for CI.

## Failure-class coverage (what's automatic vs. human review)

Verified directly against `app/services/diagnosis.py::derive_incidents` (not
inferred). Every incident type is *diagnosed* automatically — the question
here is what happens after diagnosis, not whether the failure is noticed.

| Failure class (from the brief) | Incident type(s) | What happens automatically | What requires a human |
|---|---|---|---|
| **Transient error** (e.g. read timeout, connect timeout, cancelled request) | `uncertain_request` (`diagnosis.py:206-217`, `:222-227`); `runtime_wait` (`:228-232`) for native queue/lock waits | `runtime_wait` self-resolves — it's marked `severity='info'`, `action='wait'`, not an error, and the native `step_failure_policy.py` retry/backoff (outside the Decision layer) already handles real transient retries at the pipeline level | `uncertain_request` is immediately set to `status='human_review'` (`:215`) — the Decision layer never auto-retries an uncertain outcome, by design (retrying after an ambiguous result risks duplicate side effects) |
| **Bad input error** (malformed/missing required data) | `invalid_json`, `missing_payload` (raised via the `bads` branch, `:201-204`) | **The only incident classes with any automated remedy**: once native retries are exhausted, `actions_recovery.py` can issue exactly one bounded `retry_same`/`retry_profile` via `decision_v1.py`, for the **text family only** (`excel_gpt`/`script_frames_qc`) | Any other node family with these incidents, or exhaustion of the single allowed recovery attempt |
| **API error** (non-ambiguous transport/HTTP failure) | `api_error` (`:212`, the non-`uncertain` branch) | Diagnosed with a catalog code/title (`ERROR_CATALOG`) | Immediately `status='human_review'` (`:215`) — no automated remedy exists at the Decision layer for this class today |
| **Unknown status** (state can't be confirmed) | `journal_gap` (`:240`, `:261`), `db_disagreement` (`:257`) | Recorded as non-blocking warnings (`severity='warning'`, `blocking=False`) with an explicit `action='restore_journal'`/`'check_database'` recommendation | Always — these are deliberately never auto-resolved; the code comment is explicit that a later successful check does not retroactively clear a DB-disagreement warning |
| **Corrupted result** (verification fails against captured input) | `contract_rejected` (`:197`, `:219`); the various `observe_*_result`/`verify_*` functions in `node_capabilities.py`/`sfx_plan.py` | The verifier calls `ActionSupervisor.set_held(..., operator_request=False)` — the execution is held immediately | Always requires `operator_request=True` to resume (`actions_v1.py::set_held`, `:486-528`) — nothing auto-clears a failed verification |

**Net finding**: 4 of the 5 requested failure classes already diagnose
correctly but have **zero automated remedy** by design — they go straight to
human review or a non-actionable warning. Only "bad input" (JSON/payload)
has any automation, and only for the text family. This is a deliberate,
documented safety posture (see `ORCHESTRATOR_CURRENT_STATE.md` §2.3-2.4), not
a gap to silently close — widening auto-remedy to more classes or families
is exactly the kind of change `ARCHITECTURE.md`'s Gateway section and the
standing plan flag as needing an explicit user decision, since it changes
what the system is allowed to do without a human in the loop.

## One TODO found in the whole codebase

`app/orchestrator/auto_advance.py:1650` — unhandled branch for visual-review
auto-advance gating (`AUTO_REVIEW_VISUAL=1` + visual case).

## Live verification (2026-10-01)

`scripts/e2e_test_agentic_loop.py --live` was run once against the real Kie API
(`gpt-5-6-sol`, `https://api.kie.ai/codex/v1/responses`) with an owner-approved
ceiling of 5 cents and a hard cap of 4 requests. Result: **PASS**.

- Scenario 1 (invalid_json): exactly **1** real model call; the real model read the
  context, followed `recommended_profile_id` and chose `retry_profile` with the
  production `repair-json-only-v1` profile; the recovery ticket was verified and the
  decision reached `done`.
- Scenario 2 (timeout): **0** model calls — the decision loop refuses any new model
  step while a request outcome is uncertain and escalates to
  `REQUIRES_HUMAN_INTERVENTION` with the structured summary.

The real excel_gpt executor re-running with the repair profile was verified in a
separate live run, see docs/STAGE_STATUS_REPORT.md. Not covered: media nodes and exact
billing (Kie credits are not converted to USD in this project).

The credentials were supplied via process environment only; nothing was written
to the repository.
