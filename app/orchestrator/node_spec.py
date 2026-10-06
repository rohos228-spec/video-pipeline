"""Declarative per-node knowledge for the orchestrator.

A node type's orchestrator knowledge is DATA (CapabilitySpec) plus optional
NAMED callables (verifier and contract registries). observation.py,
decision_v1.py and node_capabilities.capability_for look these up instead of
branching on the node type. This is not a workflow engine or discovery
mechanism.

register_node() is the one-call registration that also fills the existing
node_registry.WORK_NODES maps. See docs/ADDING_NEW_AGENT.md for the manual
procedure it wraps and docs/ORCHESTRATOR_NODE_EXTENSION_CONTRACT.md for the
declarative capability/verifier/contract layer. Registering a node still
requires: a ProjectStatus running/ready pair in app/models.py, a
steps/<name>.py executor, and a dispatch branch in pipeline.py::advance().
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import ProjectStatus


@dataclass(frozen=True)
class NodeSpec:
    """Full declared contract for one node type.

    input_schema / output_schema / permissions are documentation-level
    contracts (dict shape, not enforced by a validator here) until a
    concrete need to validate them mechanically arises.
    """

    node_id: str
    version: str
    step_code: str
    running_status: ProjectStatus
    ready_status: ProjectStatus
    result_kind: str
    executor: str
    phases: tuple[str, ...]
    effects: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    managed: bool = False
    verifier: Callable[..., Any] | None = None
    linear: bool = False
    # Capability data carried 1:1 into node_capabilities.NodeCapability.
    approval: tuple[str, ...] = ("scope", "budget", "provider_profile")
    profile_compatible: bool = False
    volatile_attachment_names: bool = False
    native_json_retry: bool = True
    recovery_continuation: str = "excel_gpt_chain"
    # Fail-closed: `managed` only takes effect with a non-empty acceptance name.
    acceptance: str | None = None
    verifier_name: str | None = None
    contract_name: str | None = None
    successor: str | None = None


def register_node(spec: NodeSpec) -> None:
    """Populate node_registry.WORK_NODES and the capability registry from one
    NodeSpec. Raises if the node_id is already registered; registration is
    meant to run once at import time, not to patch an existing node type.
    """
    from app.orchestrator import node_registry
    from app.services import node_capabilities

    if spec.node_id in node_registry.WORK_NODES:
        raise ValueError(f"node type already registered: {spec.node_id!r}")

    node_registry.WORK_NODES[spec.node_id] = node_registry.WorkNodeSpec(
        spec.node_id, spec.step_code, spec.running_status, spec.ready_status,
    )
    node_registry.STEP_CODE_TO_NODE_TYPE[spec.step_code] = spec.node_id
    node_registry.NODE_TYPE_TO_STEP_CODE[spec.node_id] = spec.step_code
    node_registry.RUNNING_TO_NODE_TYPE.setdefault(spec.running_status, spec.node_id)
    node_registry.READY_TO_NODE_TYPE.setdefault(spec.ready_status, spec.node_id)
    node_registry.NODE_TYPE_TO_RUNNING[spec.node_id] = spec.running_status
    node_registry.NODE_TYPE_TO_READY[spec.node_id] = spec.ready_status
    if spec.linear:
        node_registry.LINEAR_NODE_TYPES.append(spec.node_id)
        node_registry.LINEAR_RUNNING_PIPELINE.append((spec.running_status, spec.node_id))

    node_capabilities.register_capability_spec(CapabilitySpec(
        node_type=spec.node_id, result_kind=spec.result_kind, executor=spec.executor,
        phases=spec.phases, effects=spec.effects, actions=spec.allowed_actions,
        managed=spec.managed, approval=spec.approval,
        profile_compatible=spec.profile_compatible,
        volatile_attachment_names=spec.volatile_attachment_names,
        native_json_retry=spec.native_json_retry,
        recovery_continuation=spec.recovery_continuation,
        acceptance=spec.acceptance, verifier=spec.verifier_name,
        contract=spec.contract_name, successor=spec.successor,
    ))

    if spec.verifier is not None:
        _VERIFIERS[spec.node_id] = spec.verifier
        if spec.verifier_name is None:
            register_verifier(VerifierSpec(name="node:" + spec.node_id, observe=spec.verifier),
                              replace=True)
            bind_verifier(spec.node_id, "node:" + spec.node_id)


_VERIFIERS: dict[str, Callable[..., Any]] = {}


def verifier_for(node_id: str) -> Callable[..., Any] | None:
    """The observe-result callable passed to register_node(), if any.

    The observation hooks resolve through verifier_spec_for() (named registry).
    """
    return _VERIFIERS.get(node_id)


# ---------------------------------------------------------------------------
# Capability data
# ---------------------------------------------------------------------------

_RETRY_ACTIONS = ("retry_same", "retry_profile")
_KNOWN_RECOVERY_CONTINUATIONS = ("excel_gpt_chain", "none")
# Managed continuation rules a CapabilitySpec.successor may name (data only).
KNOWN_SUCCESSORS = frozenset({"continue_managed_image"})

# Machine-readable reasons why a node has NO automatic recovery (CapabilitySpec.forbidden_code).
# A code is data, not a branch: the supervisor never switches on it. It exists so the coverage
# panel, the tests and a human reviewer can tell WHY, and so a new reason must be added here.
KNOWN_FORBIDDEN_CODES = {
    "media_recovery_not_admitted": "восстановление надзором допущено только для сбоев ответа модели; для медиа допуска нет, возобновление и повтор делает нативный слой или человек",
    "nondeterministic_paid_request": "платный запрос строится заново при каждом запуске: тот же запрос после рестарта не восстановить, возобновление по идентификатору задачи не сработает",
    "paid_no_idempotency": "платный запрос без ключа идемпотентности: после таймаута неизвестно, списаны ли деньги",
    "paid_uncertain_submit": "исход отправки может быть неизвестен, а деньги уже списаны; управляемый путь только синтетический",
    "external_irreversible": "действие во внешнем аккаунте необратимо: повтор может опубликовать дважды",
    "destructive_rewrite": "нода целиком заменяет данные проекта: повтор стирает прежний результат",
    "native_skips_filled_rows": "нативный запуск не перезапрашивает уже заполненные строки: повтор не изменит результат",
    "partial_commit": "нода коммитит результат по частям и держит чекпоинты на диске: повтор после сбоя небезопасен",
    "pre_request_commit": "до запроса к модели нода уже записала новые строки: ввод для повтора изменён",
    "legacy_node_type": "устаревший тип ноды: управляемый путь только после миграции графа",
}
COVERAGE_LEVELS = ("managed", "observed", "human_only")
# Fields of CapabilitySpec that describe coverage only; they must not enter the recovery
# input revision (editing a note must not invalidate recovery tickets).
COVERAGE_FIELDS = frozenset({"verifies", "limits", "observation_hook", "forbidden_code",
                             "forbidden_reason", "recovery_cap"})


@dataclass(frozen=True)
class CapabilitySpec:
    """Declarative form of one node_capabilities.NodeCapability.

    Fields up to `recovery_continuation` map 1:1 onto NodeCapability, so its
    asdict()/contract() payload is unchanged. `acceptance`, `verifier`,
    `contract` and `successor` live only here: persisted/sent payloads must
    not change when a node is onboarded.
      acceptance - name of the accepted-evidence artifact/scenario; `managed`
                   is forced False without it.
      verifier   - NAME in the verifier registry (None: no result hook).
      contract   - NAME in the contract registry (None: derived from result_kind).
      successor  - NAME of the managed continuation rule after a verified
                   result (data only; None: the original runtime continues alone).
    """

    node_type: str
    result_kind: str
    executor: str
    phases: tuple[str, ...]
    effects: tuple[str, ...]
    actions: tuple[str, ...]
    managed: bool = False
    approval: tuple[str, ...] = ("scope", "budget", "provider_profile")
    profile_compatible: bool = False
    volatile_attachment_names: bool = False
    native_json_retry: bool = True
    recovery_continuation: str = "excel_gpt_chain"
    acceptance: str | None = None
    verifier: str | None = None
    contract: str | None = None
    successor: str | None = None
    # Coverage data (panel "Покрытие нод"): documentation-level, never part of the
    # NodeCapability payload and excluded from the recovery input revision.
    #   verifies          - what the supervisor checks about this node's result (plain Russian).
    #   limits            - what stays forbidden even when recovery is allowed, and why.
    #   observation_hook  - 'module:function' of a result hook that lives outside the
    #                       verifier registry (resolved by the coverage test).
    #   forbidden_code    - machine-readable reason why automatic recovery is forbidden
    #                       (one of KNOWN_FORBIDDEN_CODES); None only for a recovering node.
    #   forbidden_reason  - the same reason as plain Russian text for the panel.
    #   recovery_cap      - supervisor recoveries per node and execution (enforced by the
    #                       UNIQUE ticket in actions_recovery; 0 = no automatic recovery).
    verifies: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    observation_hook: str | None = None
    forbidden_code: str | None = None
    forbidden_reason: str | None = None
    recovery_cap: int = 0

    @property
    def effective_managed(self) -> bool:
        return bool(self.managed and self.acceptance)

    @property
    def recovery_actions(self) -> tuple[str, ...]:
        return tuple(a for a in self.actions if a in _RETRY_ACTIONS) if self.effective_managed else ()

    def to_capability(self):
        from app.services.node_capabilities import NodeCapability
        return NodeCapability(
            self.node_type, self.result_kind, self.executor, tuple(self.phases),
            tuple(self.effects), tuple(self.actions), self.effective_managed,
            tuple(self.approval), self.profile_compatible,
            self.volatile_attachment_names, self.native_json_retry,
            self.recovery_continuation)


_CAPABILITY_SPECS: dict[str, CapabilitySpec] = {}


def capability_spec_for(node_type: str) -> CapabilitySpec | None:
    return _CAPABILITY_SPECS.get(node_type)


def registered_capability_specs() -> dict[str, CapabilitySpec]:
    return dict(_CAPABILITY_SPECS)


def coverage_evidence(spec: CapabilitySpec) -> list[str]:
    """Names of the result checks the supervisor really has for this node (data lookup)."""
    evidence = []
    if verifier_spec_for(spec.node_type) is not None:
        evidence.append("verifier:" + verifier_spec_for(spec.node_type).name)
    if spec.observation_hook:
        evidence.append("hook:" + spec.observation_hook)
    if descriptive_contract_for(spec.node_type) is not None:
        evidence.append("descriptive_contract")
    return evidence


def coverage_level(spec: CapabilitySpec) -> str:
    """managed: accepted executor, the supervisor starts and verifies it.
    observed: not managed, but a result check or a read-only description exists.
    human_only: nothing beyond the generic journal; every action belongs to a human."""
    if spec.effective_managed:
        return "managed"
    return "observed" if coverage_evidence(spec) else "human_only"


# ---------------------------------------------------------------------------
# Named registries
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerifierSpec:
    """A named result observation hook used by observation.observe_attempt.

    observe(session, project, node_type) is awaited. It is a callable or a
    'module:function' path resolved lazily, so an import failure is handled
    like any other hook failure.
      managed_only          - run only in managed scope.
      success_errors        - 'gap': a failure marks an audit gap and execution
                              goes on; 'raise': the failure propagates.
      success_flag          - AttemptContext attribute set True after a clean run.
      on_error              - also run when the native executor failed.
      error_done_flag       - on error, skip when this AttemptContext attribute is truthy.
      error_gap_exempt_flag - on error, a failing hook marks a gap unless this
                              AttemptContext attribute is truthy.
    """

    name: str
    observe: Callable[..., Any] | str
    managed_only: bool = False
    success_errors: str = "gap"
    success_flag: str | None = None
    on_error: bool = False
    error_done_flag: str | None = None
    error_gap_exempt_flag: str | None = None

    def resolve(self) -> Callable[..., Any]:
        if callable(self.observe):
            return self.observe
        module, _, attr = self.observe.partition(":")
        return getattr(importlib.import_module(module), attr)


_VERIFIER_SPECS: dict[str, VerifierSpec] = {}
_VERIFIER_BINDINGS: dict[str, str] = {}
_CONTRACT_BUILDERS: dict[str, Callable[..., Any]] = {}
_DESCRIPTIVE_CONTRACTS: dict[str, Callable[..., Any]] = {}

# result_kind -> contract name, used when a spec names none. Any other kind
# (image, video, audio, assembly, publication, ...) uses the media contract.
DEFAULT_CONTRACT_BY_RESULT_KIND = {
    "unsupported": "unsupported",
    "project_text": "project_text",
    "frame_set": "frame_rows",
    "frame_prompts": "frame_rows",
    "structured": "structured",
}
MEDIA_CONTRACT = "media_jobs"


def register_verifier(spec: VerifierSpec, *, replace: bool = False) -> None:
    if spec.name in _VERIFIER_SPECS and not replace:
        raise ValueError(f"verifier already registered: {spec.name!r}")
    if spec.success_errors not in {"gap", "raise"}:
        raise ValueError("success_errors must be 'gap' or 'raise'")
    _VERIFIER_SPECS[spec.name] = spec


def bind_verifier(node_type: str, verifier_name: str) -> None:
    """Bind a node type to a verifier without a CapabilitySpec (for example an
    unsupported-but-observed type such as animation_prompts)."""
    _VERIFIER_BINDINGS[node_type] = verifier_name


def unbind_verifier(node_type: str) -> None:
    _VERIFIER_BINDINGS.pop(node_type, None)


def verifier_spec_for(node_type: str) -> VerifierSpec | None:
    name = _VERIFIER_BINDINGS.get(node_type)
    if name is None:
        spec = _CAPABILITY_SPECS.get(node_type)
        name = spec.verifier if spec else None
    return _VERIFIER_SPECS.get(name) if name else None


def registered_verifiers() -> dict[str, VerifierSpec]:
    return dict(_VERIFIER_SPECS)


def register_contract_builder(name: str, builder: Callable[..., Any], *, replace: bool = False) -> None:
    """builder(ctx) -> dict, awaited by DecisionSupervisor.read_contract."""
    if name in _CONTRACT_BUILDERS and not replace:
        raise ValueError(f"contract builder already registered: {name!r}")
    _CONTRACT_BUILDERS[name] = builder


def register_descriptive_contract(node_type: str, builder: Callable[..., Any], *, replace: bool = False) -> None:
    """Read-only description for an unsupported (unaccepted) node type.
    builder(node) -> dict | None; it never grants execution admission."""
    if node_type in _DESCRIPTIVE_CONTRACTS and not replace:
        raise ValueError(f"descriptive contract already registered: {node_type!r}")
    _DESCRIPTIVE_CONTRACTS[node_type] = builder


def descriptive_contract_for(node_type: str) -> Callable[..., Any] | None:
    return _DESCRIPTIVE_CONTRACTS.get(node_type)


def registered_contract_builders() -> dict[str, Callable[..., Any]]:
    return dict(_CONTRACT_BUILDERS)


def contract_name_for(capability) -> str:
    """Contract builder name for a NodeCapability."""
    kind = capability.result_kind
    if kind == "unsupported":
        return "unsupported"
    spec = _CAPABILITY_SPECS.get(capability.node_type)
    if spec is not None and spec.contract:
        return spec.contract
    return DEFAULT_CONTRACT_BY_RESULT_KIND.get(kind, MEDIA_CONTRACT)


def contract_builder_for(capability) -> Callable[..., Any]:
    name = contract_name_for(capability)
    try:
        return _CONTRACT_BUILDERS[name]
    except KeyError:
        raise ValueError(f"unknown contract builder: {name!r}") from None


# ---------------------------------------------------------------------------
# Node package manifest (`orchestrator:` block) -> CapabilitySpec
# ---------------------------------------------------------------------------

_MANIFEST_KEYS = {
    "result_kind", "executor", "phases", "effects", "actions", "managed",
    "approval", "profile_compatible", "volatile_attachment_names",
    "native_json_retry", "recovery_continuation", "acceptance", "verifier",
    "contract", "successor", "verifies", "limits", "observation_hook",
    "forbidden_code", "forbidden_reason", "recovery_cap",
}


def capability_from_manifest(manifest: dict) -> CapabilitySpec:
    """Build a CapabilitySpec from a node package manifest.

    Takes the whole manifest (`nodeapi: 1`, `type`, and an `orchestrator:`
    mapping) and validates it. Fail-closed: `managed` is forced False without
    an acceptance name; unknown keys, wrong types and unknown action names are
    rejected with ValueError. It does not register anything: pass the result
    to node_capabilities.register_capability_spec().
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a mapping")
    if manifest.get("nodeapi") != 1:
        raise ValueError("unsupported nodeapi version")
    node_type = manifest.get("type", manifest.get("node_type"))
    if (not isinstance(node_type, str) or not node_type
            or not node_type.replace("_", "").isalnum() or node_type != node_type.lower()):
        raise ValueError("manifest type must be a lowercase identifier")
    block = manifest.get("orchestrator")
    if not isinstance(block, dict):
        raise ValueError("manifest has no orchestrator block")
    unknown = set(block) - _MANIFEST_KEYS
    if unknown:
        raise ValueError(f"unknown orchestrator keys: {sorted(unknown)}")
    for key in ("result_kind", "executor"):
        if not isinstance(block.get(key), str) or not block[key]:
            raise ValueError(f"orchestrator.{key} must be a non-empty string")

    def names(key, default=()):
        value = block.get(key, default)
        if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v for v in value):
            raise ValueError(f"orchestrator.{key} must be a list of strings")
        return tuple(value)

    def flag(key, default):
        value = block.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"orchestrator.{key} must be a boolean")
        return value

    def optional_name(key):
        value = block.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"orchestrator.{key} must be a non-empty string or null")
        return value

    from app.services.node_capabilities import READ_ACTIONS
    actions = names("actions", READ_ACTIONS)
    unknown_actions = set(actions) - (set(READ_ACTIONS) | set(_RETRY_ACTIONS) | {"finish"})
    if unknown_actions:
        raise ValueError(f"unknown actions: {sorted(unknown_actions)}")
    continuation = block.get("recovery_continuation", "none")
    if continuation not in _KNOWN_RECOVERY_CONTINUATIONS:
        raise ValueError("unknown recovery_continuation")
    acceptance = optional_name("acceptance")
    kwargs = dict(
        node_type=node_type, result_kind=block["result_kind"], executor=block["executor"],
        phases=names("phases", ("unknown",)), effects=names("effects"), actions=actions,
        managed=flag("managed", False) and bool(acceptance),
        profile_compatible=flag("profile_compatible", False),
        volatile_attachment_names=flag("volatile_attachment_names", False),
        native_json_retry=flag("native_json_retry", False),
        recovery_continuation=continuation, acceptance=acceptance,
        verifier=optional_name("verifier"), contract=optional_name("contract"),
        successor=optional_name("successor"),
        verifies=names("verifies"), limits=names("limits"),
        observation_hook=optional_name("observation_hook"),
        forbidden_code=optional_name("forbidden_code"),
        forbidden_reason=optional_name("forbidden_reason"),
    )
    if kwargs["forbidden_code"] is not None and kwargs["forbidden_code"] not in KNOWN_FORBIDDEN_CODES:
        raise ValueError("unknown orchestrator.forbidden_code")
    cap = block.get("recovery_cap", 0)
    if type(cap) is not int or not 0 <= cap <= 1:
        raise ValueError("orchestrator.recovery_cap must be 0 or 1")
    kwargs["recovery_cap"] = cap
    if "approval" in block:
        kwargs["approval"] = names("approval")
    return CapabilitySpec(**kwargs)


# ---------------------------------------------------------------------------
# Verifier dispatch used by observation.observe_attempt
# ---------------------------------------------------------------------------

async def run_verifier_after_success(ctx, session, project, node_type: str) -> None:
    """Run the node type's registered result hook after the native executor
    returned. Does nothing for a node type without a verifier."""
    spec = verifier_spec_for(node_type)
    if spec is None or (spec.managed_only and ctx.scope.mode != "managed"):
        return
    if spec.success_errors == "raise":
        await spec.resolve()(session, project, node_type)
        if spec.success_flag:
            setattr(ctx, spec.success_flag, True)
        return
    try:
        await spec.resolve()(session, project, node_type)
        if spec.success_flag:
            setattr(ctx, spec.success_flag, True)
    except Exception as snapshot_error:
        ctx.observer.mark_gap(ctx.scope.execution_id, snapshot_error)


async def run_verifier_after_error(ctx, session, project, node_type: str, exc: BaseException) -> None:
    """Run the registered hook after a native failure so the failed result is
    still observed. A hook failure never replaces the native error."""
    spec = verifier_spec_for(node_type)
    if spec is None or not spec.on_error or not isinstance(exc, Exception):
        return
    if spec.managed_only and ctx.scope.mode != "managed":
        return
    if spec.error_done_flag and getattr(ctx, spec.error_done_flag, False):
        return
    try:
        await spec.resolve()(session, project, node_type)
    except Exception as snapshot_error:
        if spec.error_gap_exempt_flag and getattr(ctx, spec.error_gap_exempt_flag, False):
            return
        ctx.observer.mark_gap(ctx.scope.execution_id, snapshot_error)
