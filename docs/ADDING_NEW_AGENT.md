# Adding a new node / executor

This walks through the exact files touched to add a new node type today. It
is not a new plugin API — see [`ARCHITECTURE.md`](ARCHITECTURE.md#registry-current-state--see-adding_new_agentmd-for-the-how-to)
for why, and for the planned thin `NodeSpec` wrapper (not built yet) that
will reduce this to one `register_node()` call.

There is no dynamic discovery. A node type is compile-time wiring across
four places. Miss one and the node either won't run, won't be observable, or
will silently fall back to an inert `"unsupported"` capability.

## The four places, in the order you'll actually hit them

### 1. `app/models.py` — add the `ProjectStatus` states

Every node needs a `running` status and a `ready` status:

```python
# in the ProjectStatus enum, running-states section
my_node_running = "my_node_running"  # one-line comment: what happens in this state

# ...and in the ready-states section
my_node_ready = "my_node_ready"  # what "done" means for this node
```

Naming convention observed in the codebase: `<verb>ing_<noun>` for running
(`generating_sfx`, `sfx_planning`), `<noun>_ready` for ready (`sfx_ready`,
`sfx_plan_ready`).

### 2. `app/orchestrator/node_registry.py` — register the `WorkNodeSpec`

```python
WORK_NODES: dict[str, WorkNodeSpec] = {
    ...
    "my_node": WorkNodeSpec(
        "my_node", "my_step_code",
        ProjectStatus.my_node_running, ProjectStatus.my_node_ready,
    ),
}
```

- `step_code` is the short code used elsewhere (queue locks, step failure
  policy keys) — pick something distinct from existing `step_code`s.
- If the node is part of the default linear chain, also add its type string
  to `LINEAR_NODE_TYPES` (`node_registry.py:161-182`) in the position where
  it should run. **Not every registered node is linear** — `sfx_plan`/
  `sfx_gen` are real, wired `WORK_NODES` entries that are deliberately
  *not* in `LINEAR_NODE_TYPES` (they're an optional side-chain triggered by
  project config, not part of the default 20-step sequence). Decide which
  your node is.
- `is_work_node_type()` (`node_registry.py:189-190`) automatically picks up
  anything in `WORK_NODES` — no separate edit needed there.

### 3. `app/orchestrator/steps/<my_node>.py` — the executor

```python
from __future__ import annotations
from aiogram import Bot  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Project, ProjectStatus


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.my_node_running:
        return
    # ... do the actual work, read/write project.meta, commit via session ...
    project.status = ProjectStatus.my_node_ready
    await session.commit()
```

Follow `app/orchestrator/steps/generate_sfx.py` as the closest real template
— it shows the guard-on-status pattern, a feature-flag pass-through (only do
that if genuinely optional, and document it — don't silently fake success),
and how to hook an observation call (`_record_generation_input`) without
touching the Observe module's internals.

### 4. `app/orchestrator/pipeline.py::advance()` — wire the dispatch

This is the part that's easy to miss: **`auto_advance.py` is not the
dispatcher** — `pipeline.py`'s single `if/elif` chain on `project.status` is
(`pipeline.py:132-186`). Add a branch:

```python
elif status is ProjectStatus.my_node_running:
    from app.orchestrator.steps import my_node
    await my_node.run(session, project, bot)
```

Without this, the node's `run()` is dead code — nothing ever calls it, even
though `WORK_NODES` and the enum look complete.

## Making it observable and capability-honest (the part that's easy to skip)

A node that only does steps 1-4 above *runs*, but is invisible to
Observe/Diagnose/Decision and falls back to the generic `"unsupported"`
capability — which is safe (fails closed, no auto-recovery) but dishonest if
the node actually has a real, verifiable contract. This is exactly the gap
found and fixed for `sfx_plan`/`sfx_gen` on 2026-09-30 (commit `cea901af`):
both were real `WORK_NODES` entries but silently `"unsupported"` in
`node_capabilities.py`, worked around by ad hoc `capability.node_type in
{'sfx_plan','sfx_gen'}` checks in `observation.py` instead of a real entry.

### 5. `app/services/node_capabilities.py` — register a real `NodeCapability`

```python
_CAPABILITIES = {
    ...
    "my_node": NodeCapability("my_node", "<result_kind>", "my_node.run → <transport>",
        (<phases tuple>), (<effects tuple>), READ_ACTIONS, managed=False),
}
```

- `result_kind` — a short label for what the node produces (`"structured"`,
  `"audio"`, `"image"`, `"assembly"`, ...).
- `phases` — the lifecycle states this node's execution passes through
  (see the `_JOB_PHASES` tuple for provider-job-style nodes, or the
  `("running","parse","validate","apply","commit")` tuple for text/plan-like
  nodes).
- `effects` — what changes as a side effect (`"project_database"`,
  `"local_file"`, `"artifact_row"`, ...).
- `managed=False` until you have a real, independently-verified worker
  acceptance run for this node — **do not set `managed=True` speculatively**;
  it changes what the Decision/Actions layer is allowed to do automatically.
- `READ_ACTIONS` (`read_events`, `read_contract`, `wait`, `human_review`) is
  the safe default action set. Only add `retry_same`/`retry_profile`/
  `finish` once the node has a real Actions/verify story (see step 6).

Without this entry, `capability_for("my_node")` returns the generic
`"unsupported"` fallback (`node_capabilities.py:59-64`) — harmless, but any
Decision `read_contract` call on this node gets no real contract, and any
special-casing elsewhere (like `observation.py` did for SFX before today)
becomes technical debt.

### 6. (Optional, once you want independent verification) an `observe_*_result` verifier

Follow `app/services/sfx_plan.py::observe_sfx_result` — the pattern is:

1. Record the *input* the node actually received, independent of business
   logic (`record_sfx_plan_input` / `_record_generation_input`).
2. After the node finishes (success or exception), open a **separate**
   read-only DB session and independently re-read what was actually
   committed — never trust the node's own return value or a variable held in
   memory.
3. Cross-check the committed result against the captured input (counts
   match, paths are contained under the project's own directory, real
   decoded properties — not just "a file exists").
4. Record `node_output` and `node_result_verification` facts via
   `record_fact`/`_sfx_fact`-style helpers in `observation.py`.
5. Wire the call into `observation.py::observe_attempt` (the `try`/`except`
   block around line 684-721) — this is the one place that currently needs a
   per-family `if capability.node_type in {...}` branch, because there is no
   generic verifier dispatch table yet. (That generic dispatch table is
   exactly what the planned `NodeSpec.verifier` field in `ARCHITECTURE.md`
   would remove — it's on the standing plan's M3, not built yet.)

## Checklist

- [ ] `ProjectStatus` running + ready states added (`app/models.py`)
- [ ] `WorkNodeSpec` registered in `WORK_NODES` (`node_registry.py`)
- [ ] Added to `LINEAR_NODE_TYPES` **only if** it's part of the default chain
- [ ] `app/orchestrator/steps/<name>.py` executor written, guarded on status
- [ ] Dispatch branch added in `pipeline.py::advance()` — **not**
      `auto_advance.py`
- [ ] Real `NodeCapability` entry in `node_capabilities.py`, `managed=False`
      until proven otherwise
- [ ] (Optional) `observe_*_result` verifier + wiring in
      `observation.py::observe_attempt`
- [ ] Tests: at minimum a unit test for the executor's happy path and one
      failure path; if it has a verifier, a test that a tampered/incomplete
      committed result is rejected
- [ ] Ran the existing test suite for the touched area to confirm no
      regression (`pytest tests/ -k <area> --ignore=tests/observe_v1`, plus
      the relevant `tests/observe_v1/` module directly if one exists)

No step here requires touching `DecisionSupervisor`, `ActionSupervisor`, or
the FSM's core advance loop — only registry-and-capability wiring plus one
new step module, consistent with the "no new engine" constraint.
