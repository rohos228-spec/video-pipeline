# Extending the existing bounded runtime

Local developer contract, 2026-09-30. This is an integration checklist for the
existing code, not a new node language, executor or plugin runtime.

## Current accepted boundary

`excel_gpt` structured execution and `images` are accepted in the documented
synthetic scenarios. The original six-node text group, a separate image graph,
and genuine exhausted QC → one recovery → verification → image have worker
evidence. This does not accept every arbitrary Excel contract or media family.
Other registry entries do not acquire managed permission by existing in a list.

`videos`, `audio`, `assemble`, and `publish` remain `managed=False` in
`app/services/node_capabilities.py`. Legacy plan/script/split/scene-design,
characters/items and sidecar paths require their own executor acceptance.
Publication stays preparation/dry-run until separate authorization. Production
configuration and real media transport remain disabled.

## Developer integration points

| Responsibility | Existing integration | Required evidence before managed=True |
|---|---|---|
| Runtime identity and FSM | `app/orchestrator/node_registry.py`, original pipeline/worker | Correct step/status mapping; original worker starts it once |
| Graph and prerequisites | `app/orchestrator/graph/planner.py`, `managed_launch.validate_scope` | Immutable graph revision, valid predecessors/gates, scope boundary |
| Capability and strategies | `app/services/node_capabilities.py` | Accepted input/result/effects, supported read/actions, no arbitrary code |
| Structured write contract | `app/services/node_write_contract.py`, `db_apply.py` | Allowed fields and UUID coverage; original application applies once |
| Provider lifecycle | `media_jobs.py`, `media_transport.py`, original provider client | Durable request/job identity, submit/poll/download accounting, uncertain stop |
| Result verification | `artifact_verifier.py`, `artifact_recovery.py`, `actions_recovery.py` | Actual file/probe or data proof; partial/unknown cannot become done |
| Observability/diagnosis | `observation.py`, `diagnosis.py`, `decision_v1.py` | Input/body hashes, attempts, transactions, typed facts and unresolved unknowns |
| UI and ownership | Existing observations router and panel | One scoped launch; understandable waiting/review/progress; other projects unchanged |

For a new family the developer must define actual input fields and sources,
output schema/artifacts, allowed database effects, domain checks, idempotent
application, compatible profiles, and continuation prerequisites. Implement
these in the existing family code and contracts. Do not add generic shell/SQL or
turn a guessed verifier into a successful status.

## Configuration variants and agent roles

Profiles are exact catalog entries, not free model instructions. Current fields:
`model_id`, `provider`, `kind`, `channel`, `reasoning_effort`,
`max_output_tokens`, `prompt_variant`, `synthetic_only`.
`decision_config.validate_profile` validates compatibility and token/reasoning
bounds; `profiles_v1.active_catalog` respects an explicit owner catalog.

Registered prompt variants live in `prompt_library.APPROVED_PROFILE_PROMPTS`:
original contract, formatting-only JSON repair, and required apply operations.
They preserve source roles/facts/identifiers and original schema/validators.
No variant can change write permissions, graph, budget or network. New role or
behavior mechanics require developer code/review and a specific test contract;
they cannot be injected by the Decision model. Artistic/semantic quality remains
unknown unless separately verified with an authorized, accounted evaluator.

The unified launch persists the selected profile IDs, exact candidate snapshot
and catalog hash before initial runtime dispatch. A catalog/config/input/graph
change blocks automatic reuse. Legacy executions expose only manually approved
immutable overlays, never an inferred catalog permission.

Decision selection creates the existing one-attempt `ProfileOverlay` only after
a claimed acting decision and validated persisted proposal. Overlay history
records the decision and proposal hash. The original recovery executor checks
input, graph, source body, attempt and budget again. `retry_same` and
`retry_profile` share one additional recovery, not separate allowances. A
restart or new incident cannot supply a second recovery.

## Owner policy, checkpoints and unknown outcomes

Default request ceiling remains 100. A local owner may explicitly configure
`budget_policy={policy_id,max_requests}`; an execution request cannot set this
policy or exceed its ceiling. `ManagedRunAllowance` binds one owner execution to
the project/workflow run; closing it does not reset consumption. Worker,
Decision and media operations charge the existing atomic common counter.

The synthetic scale test reserves 1000 operations, reloads after 500, and blocks
1001. It proves counter durability/atomicity, not a 1000-node factory run,
provider token limits, monetary savings or a hard dollar cap. Unknown prices and
usage are never zero. Changing policy does not refund requests already sent.

Persist action/attempt identity and immutable revision before execution. Verify
using matching runtime events and application data. An unknown submit outcome
means hold/review; a known media job is observed without another submit. Native
active retry is not duplicated. A complete model terminal or known HTTP
rejection is required to settle a request; a mere terminal/error row is not proof.

Configuration cancellation restores configuration permission only. It does not
roll back database data, files or paid work. Partial apply or audit/commit
uncertainty requires human review; any data rollback needs a specific reviewed
application transaction and checkpoint. The model cannot issue SQL to repair it.

## Minimum acceptance for each family

1. Existing API/FSM → original worker → parse/validate → apply → verify → next node.
2. Full bodies/files are local; shared IDs and hashes link logs to the attempt.
3. Failed payload/result remains failed/unknown; native retry and supervisor
   recovery are distinguished; no successful business status manufactured.
4. Active request, user stop, audit gap, stale revision, foreign scope and spent
   budget prevent a new request. Lost/repeated delivery and restart do not submit twice.
5. Business inputs are retained, IDs covered, and apply occurs once. Disk-file
   existence alone does not authorize managed success.
6. Ordinary mode and another project remain unchanged; legacy/sidecar entrances
   cannot bypass managed scope or the common counter.
7. Real files and actual tools are used for FFmpeg/ASR/subtitles/assembly. Missing
   executable/package/weights block that family; no fake output substitutes it.
8. Current loaded backend/frontend hashes, tests and UI evidence are saved.

All acceptance currently uses separate SQLite/DATA_DIR and network none, with
only external provider transport synthetic. Real provider verification and
production readiness remain separate acceptance levels.
