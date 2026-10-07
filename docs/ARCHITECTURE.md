# Architecture

This document describes the current architecture as it actually exists in
`feature/orchestrator-observe-v1`, not an aspirational target. For narrative
evidence and acceptance detail, see [`ORCHESTRATOR_CURRENT_STATE.md`](ORCHESTRATOR_CURRENT_STATE.md)
and the per-component index in [`ORCHESTRATOR_STATUS_CURRENT.md`](ORCHESTRATOR_STATUS_CURRENT.md).

## Two layers, not one

The system is two things sharing one database, not one unified engine:

```
┌─────────────────────────────────────────────────────────────┐
│  1. Native pipeline (does the actual generation work)        │
│     app/orchestrator/pipeline.py, auto_advance.py,            │
│     node_registry.py, app/orchestrator/steps/*.py             │
│     — a hand-coded linear FSM driven by ProjectStatus          │
│       transitions (plan → script → split → scene_design →      │
│       hero → items → enrich_1..5 → image_prompts → images →    │
│       animation_prompts → videos → audio → music → sfx_plan →  │
│       sfx_gen → assemble → publish)                             │
└─────────────────────────────────────────────────────────────┘
                              │ watched by (SQLAlchemy before_flush / outbox)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Observe/Decision supervisory layer (watches, diagnoses,    │
│     optionally recovers — opt-in, off by default)               │
│     app/services/observation.py, diagnosis.py, decision_v1.py, │
│     actions_v1.py, actions_recovery.py, media_jobs.py,          │
│     profiles_v1.py                                               │
└─────────────────────────────────────────────────────────────┘
```

Layer 1 is what actually runs generation and has run in production. Layer 2
is a bolted-on safety/observability harness: it can watch every node
execution, classify failures, and — for a narrow, explicitly whitelisted set
of cases — take one bounded recovery action before handing off to a human.
It does not replace layer 1's FSM, and most of its transport is currently
restricted to synthetic/loopback endpoints (see Gateway, below).

## The Decision cycle

```
Observer ──records──▶ journal (observation_events, outbox)
   │
   ▼
Diagnosis ──rule-based classification──▶ Incident
   │   (invalid_json, missing_payload, contract_rejected,
   │    api_error, media_failure, runtime_wait, user_stop, ...)
   ▼
DecisionSupervisor ──bounded (≤3 steps) model dialogue──▶ proposed action
   │   context is scope-limited JSON: incident, allowed actions,
   │   budget, capability contract, first 20 facts + read tools
   ▼
Action/ActionSupervisor ──whitelist dispatch──▶
   │   retry_same | retry_profile | read_events | read_contract |
   │   wait | human_review | finish
   ▼
Verification ──independent re-read of committed output──▶ pass/fail
   │   (never trusts the model's self-report; `finish` requires
   │    `verification.get('verified')` to be true)
   ▼
Continue (back into layer 1's FSM) ── or ── Human review (execution held,
                                            requires an operator to resume)
```

Key files: `app/services/observation.py` (Observer), `diagnosis.py`
(Diagnosis), `decision_v1.py` + `decision_state.py` (DecisionSupervisor),
`actions_v1.py` + `actions_recovery.py` (Action), verification is spread
across `node_capabilities.py` (`verify_project_text`, `verify_split_frames`,
`verify_image_prompts`) and `sfx_plan.py` (`observe_sfx_result`).

Today, auto-entry into this cycle is limited to **exhausted text
`invalid_json`/`missing_payload` incidents** — not "any error on any node."
Extending auto-entry to another failure class means adding a rule to
`diagnosis.py` and a matching contract entry in `node_capabilities.py`, not
building a new engine.

## Gateway / ledger

`decision_config.py`, `media_jobs.py`, `actions_v1.charge_request`,
`scripts/diagnostics/gateway.py`. Defense-in-depth, in order:

1. **Egress restriction** — endpoint path must be exactly
   `/codex/v1/responses`; synthetic mode forces loopback-only; media URLs
   must match an explicit `download_origins` allowlist.
2. **Keyless preflight** — credential must start with `"synthetic"` before
   any media call is allowed in synthetic mode.
3. **Atomic budget reservation** — `charge_request()` does an optimistic
   `UPDATE ... WHERE requests_used < request_limit RETURNING` *before* any
   HTTP call, so a crash mid-request can't double-spend budget.
4. **Terminal-state protection** — `MediaJob`/`MediaRequest` phase
   transitions are one-way; a transition out of a terminal state raises.
5. **ACK-before-repeat** — `ack_gateway()` requires a local
   `/local/decision-ack` acceptance after any model response; failure to ACK
   is treated as fatal rather than silently retried.
6. **Drain/held** — `ActionExecutionControl.held` is the emergency stop;
   resuming requires `operator_request=True` — nothing auto-resumes on its
   own.

**Important limitation**: this gateway currently only governs the
Observe/Decision layer's own synthetic/loopback transport. It does **not**
gate the real provider calls made directly by `app/orchestrator/steps/
generate_images.py`, `generate_videos.py`, or `generate_sfx.py` — those go
through `app/services/gpt_api.py`, `kie_catalog.py`, and Outsee's HTTP client
outside this managed envelope. Extending the gateway to cover real provider
traffic is a deliberate architectural fork-in-the-road (see the standing
plan's stop points) — it should be a conscious decision, not an incidental
side effect of adding a feature.

## Registry (current state — see `ADDING_NEW_AGENT.md` for the how-to)

There is no plugin/dynamic-discovery registry. A node type is defined by
three to four places that must stay in sync. The orchestrator's per-node
knowledge (capability, result verifier, contract) is declarative data, see
the last bullets of this list and "Declarative node knowledge" below:

- `app/orchestrator/node_registry.py` — `WORK_NODES: dict[str, WorkNodeSpec]`
  (step code, running status, ready status).
- `app/services/node_capabilities.py` — one `CapabilitySpec` data entry per
  node type (result kind, executor description, phases, effects, allowed
  actions, `managed`, recovery flags, plus `acceptance`, `verifier`,
  `contract`, `successor` names), registered into the registry in
  `app/orchestrator/node_spec.py`; `_CAPABILITIES` is the derived
  `NodeCapability` view. `capability_for()` fails safe: an unregistered node type gets an inert
  `"unsupported"` capability rather than erroring.
- `app/models.py` — the `ProjectStatus` enum needs the node's running/ready
  states.
- `app/orchestrator/steps/<name>.py` — the actual executor (`async def
  run(session, project, bot=None)`), gated on `project.status`.
- `app/orchestrator/pipeline.py::advance()` — the single `if/elif` dispatch
  chain on `project.status` that imports and calls each step module. This,
  not `auto_advance.py`, is where a new node's `run()` gets wired in.

This is intentionally **not** being replaced with a new engine (out of scope
per the brief). `NodeSpec` / `register_node()` (built, `app/orchestrator/
node_spec.py`) wrap the same dicts behind one call; it fills `WORK_NODES`
and the capability registry together.

### Declarative node knowledge

`app/orchestrator/node_spec.py` holds the data model and three registries:

- `CapabilitySpec` — the capability fields of a node type as data. The 12
  built-in entries live in `node_capabilities._register_builtin_capabilities`.
  `NodeCapability` (and its `asdict()`/`contract()` payload) is unchanged;
  `tests/fixtures/node_capabilities_snapshot.json` pins it byte for byte.
- Verifier registry (`VerifierSpec`, `verifier_spec_for`) — the result
  observation hooks that `observation.observe_attempt` runs after the native
  executor (`run_verifier_after_success` / `run_verifier_after_error`). The
  per-type `if capability.node_type ...` chain is gone.
- Contract registry (`register_contract_builder`, `contract_builder_for`) —
  what `DecisionSupervisor.read_contract` returns per node, picked by the
  spec's `contract` name or, when absent, by `result_kind`; unaccepted node
  types may add a read-only description via `register_descriptive_contract`.
- `capability_from_manifest(dict)` validates a node package's `orchestrator:`
  block into a `CapabilitySpec` (see `PROPOSAL_NODE_PACKAGES.md`).

`managed` takes effect only with a non-empty `acceptance` name. Still
hard-wired, not yet data: the `pipeline.py::advance()` dispatch, the
`ProjectStatus` enum, `input_revision`/`graph_version` composition and the
`continue_managed_image` successor check (`successor` is recorded as data
only). `tests/observe_v1/test_node_conformance.py` checks every registered
spec and onboards a synthetic node by data.

## Model / Agent / Node Strategy / Prompt Version

These are four already-distinct concepts, not one conflated "Agent" class.
They live in separate modules; a change to one does not imply a change to
the others:

| Concept | What it means here | Where it lives |
|---|---|---|
| **Model** | Which LLM/provider answers a request (e.g. `gpt-5-6-sol`, a Kie-hosted Seedance video model) | `app/services/text_llm_catalog.py` (active text model selection), `vibecode_catalog.py` (richer per-provider/per-node catalog, pricing/markup), `app/services/llm_override.py` (`NodeLlmOverride` — a per-request override bound via `ContextVar`) |
| **Agent** | A named executor role *within* scene design — `characters`/`world`/`style`/`camera`/`action` | `app/services/scene_design/agents.py`, `agent_chunks.py` — these are prompt/step variants of the `sd_agent` node type, not a separate class hierarchy |
| **Node strategy** | Per-node-instance behavior/config attached to a canvas node (duration, split limits, output/check/work mode) | `app/services/node_step_params.py`, `app/services/gpt_operator.py::operator_config()`, `app/services/excel_gpt_node.py::node_config()` — plain JSON on the node, not a class |
| **Prompt version** | Which named prompt text is active for a given step code | `app/services/prompt_library.py` (file-based storage per step_code), `app/services/prompt_active_global.py` (which prompt name is globally active) |

A fifth, unrelated concept — **"profile"** in `profiles_v1.py` — bundles
`(model_id, reasoning_effort, max_output_tokens, one fixed prompt_variant)`
for the Decision layer's recovery-retry action only. It is not a general
model/agent/strategy/prompt abstraction and should not be confused with the
four above. `profiles_v1.PRODUCTION_CATALOG` is the built-in production
catalog of text-recovery profiles (Kie: same model with another
`reasoning_effort`/`max_output_tokens`; Vibecode: formatting-only JSON repair,
no effort/token knobs). It is active only when the owner's production config
lists `retry_profile`, has no explicit `profiles`/`incident_scope` and does not
set `builtin_profiles:false`; synthetic profiles stay test-only.

**Practical implication**: changing which LLM answers a node's requests
(model) never implies changing the node's executor code (agent/strategy), and
vice versa. A developer touching "the SFX agent" should know that means the
`sfx_plan`/`sfx_gen` node strategy and executor, not a model change.

## Media pipeline shape (current, not target)

Every media family is intended to eventually follow the same shape —
**input → worker → events → result → verifier → decision** — but they are at
different points along that path today:

| Family | Input → Worker | Events | Verifier | Decision-managed |
|---|---|---|---|---|
| Text (`excel_gpt`) | `text_job` → GPT | Full Observe journal | `verify_project_text` | Yes |
| Images | `generate_images.run` → `kie_http`/Outsee | Full job-phase journal | Structural (decode/hash/dimensions) | Synthetic-accepted |
| Video | `generate_videos.run` → `kie_http` (Seedance/Kling ladder) | Partial | `video_error_policy` classification | No (`managed=false`) |
| SFX | `generate_sfx.run` → `sfx_gen`/ElevenLabs/local-synth | Full (added today) | `observe_sfx_result` (WAV duration/RMS/path/hash) | No (`managed=false`, honest capability entry) |
| Assembly | `assemble.run` → real `ffmpeg` subprocess | Partial | Structural ASS verifier only | No |
| Publish | `publish.run` | Read-only preview | Input/dry-run only | No (real publish blocked by design) |

Bringing a family fully into this shape means: (1) a real `NodeCapability`
entry (not "unsupported"), (2) an `observe_*_result` verifier that
independently re-reads committed output, (3) a decision on whether it needs
`managed=True` in the Decision/Actions envelope. None of this requires a new
workflow engine — the shape already exists; it's a per-family completion
task (tracked in `ORCHESTRATOR_STATUS_CURRENT.md`).

## Self-healing fallback (same-node profile, not cross-agent routing)

When the Decision layer's single bounded recovery attempt (see "The Decision
cycle" above) is granted for an exhausted text `invalid_json`/`missing_payload`
incident, the model can choose `retry_profile` instead of a bare `retry_same`
— i.e. retry the *same* node with a different model/reasoning-effort/prompt
candidate from `profiles_v1.py`'s catalog, rather than literally repeating
the identical request. This is deliberately **same-node, same-executor**:
there is no mechanism (and none is planned without a separate design pass)
for substituting a different node/agent implementation for a failing one —
`node_registry.WORK_NODES` maps one node type to exactly one
`app/orchestrator/steps/<name>.py` executor, and there is no "equivalent
capability" registry of alternate implementations per node type. Swapping
which *node* runs would be a structural graph change with real implications
for the budget ledger, audit trail and idempotency guarantees — out of scope
here.

Production `profiles` are intentionally **not** shipped pre-populated:
`config/orchestrator-runtime.example.json` (the file `ORCHESTRATOR_RUNTIME_CONFIG`
is meant to point at) ships fully empty/disabled by design — a committed test
(`tests/observe_v1/test_decision_runtime.py::test_default_example_is_disabled_and_valid`)
enforces `profiles == {}` and `allowed_actions == []` for that exact file, so
that a copy-paste deployment starts from zero, not from an implicit decision
already made for the operator. The *validated, ready-to-copy recipe* for the
one supported fallback case (lower `reasoning_effort` + the already-approved
`repair-json-only-v1` prompt variant, for `invalid_json`/`missing_payload`)
lives in a separate, clearly-labeled file:
`config/orchestrator-runtime.self-healing-example.json` — it does not load
as-is (its `_comment` key makes `validate_config()` reject it on purpose;
confirmed by test above), it's meant to be copied into an operator's own
`config/orchestrator-runtime.json`. `online_enabled` stays `false` even in
that recipe — turning it on additionally requires a real (non-`synthetic*`)
`GPT_API_KEY`, which is a deployment decision, not something this repo
should default to.

## Cost control

The two mechanisms that actually bound real spend today:

- `app/services/step_failure_policy.py` — 3 failures → 30 min sleep
  (2 min for known-transient errors), 3 cycles (9 total failures) → project
  paused, requires manual `reset_step()`. Survives restarts (`project.meta`).
- `app/services/video_error_policy.py` — bounds total attempts per clip
  across primary + fallback provider before giving up on that frame; a
  separate 5-consecutive-failure counter skips the frame regardless.

The Decision layer's own request-budget ledger (`ActionExecutionControl.
request_limit`/`requests_used`) is real and atomic but only applies inside
the synthetic/opt-in managed envelope — it does not currently bound the
native pipeline's real provider calls, which rely on the two mechanisms
above instead. There is no dollar-cost model anywhere in the system;
`cost_status: 'unknown_tariff'` is stamped deliberately everywhere rather
than inventing a false precision.
