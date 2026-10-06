"""Revisions that gate managed recovery: graph structure and per-node input.

Two questions are answered separately, each with its own hash:

* structure_hash(nodes, edges): WHICH nodes run in what order. Node ids, node
  types, edges (with their kind), group membership, slot indices, scene-agent
  markers and disabled flags. Cosmetic data (labels, positions, ids of edges)
  and node configuration are not part of it. The same value is stored as the
  execution graph_version.
* node_config_revision(node, meta): the configuration that influences the
  execution of ONE node (its data, per-node project settings). Recorded per
  node, so a permission or ticket follows structure + that node's config and
  not the configuration of every other node.

input_revision (computed by compute_input_revision) combines, for one node:
the data inputs it reads, its resolved prompt files (content), the node's
capability/contract data, the settings that shape the model request, its
config revision and a bounded list of code modules (executor, verifier, apply
and request modules). Nothing else: not the whole prompts tree, not the whole
settings object, not unrelated code.

Fail-closed: whatever cannot be determined is replaced by a fresh random
nonce, so two computations never agree on it. Stored values carry a scheme
number; a value produced under another scheme is recognised and rejected with
a stated reason instead of being compared as a hash.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

# Scheme of the input revision / ticket parts. 1 was the whole-tree hash
# (no scheme recorded at all); 2 is this module.
REVISION_SCHEME = 2
# Scheme of the structural graph hash. 1 was a hash of the whole snapshot.
GRAPH_SCHEME = 2

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Cosmetic canvas data: never part of any revision.
COSMETIC_NODE_KEYS = frozenset({
    "position", "positionAbsolute", "measured", "width", "height", "selected",
    "dragging", "style", "className", "zIndex", "resizing", "draggable",
    "selectable", "connectable", "deletable", "focusable"})
COSMETIC_DATA_KEYS = frozenset({
    "label", "description", "title", "note", "comment", "color", "icon", "collapsed"})
# Data keys that decide which nodes run in what order (structure, not config).
STRUCTURAL_DATA_KEYS = frozenset({
    "groupId", "slotIndex", "slotOverflow", "sd_agent", "agent", "disabled"})
COSMETIC_EDGE_KEYS = frozenset({
    "id", "sourceHandle", "targetHandle", "label", "animated", "style", "type",
    "markerEnd", "markerStart", "selected", "zIndex", "className"})
# Per-node buckets in project.meta that hold node configuration.
PER_NODE_META_KEYS = ("excel_gpt_nodes", "prompt_slot_variants")
# Bounded list of modules whose change must invalidate a node's tickets, besides
# the executor/verifier modules resolved from capability data.
APPLY_MODULES = ("app/services/db_apply.py", "app/services/node_write_contract.py")
TEXT_REQUEST_MODULES = ("app/services/gpt_client.py", "app/services/gpt_text_builder.py")
MEDIA_MODULES = ("app/services/media_jobs.py", "app/services/artifact_verifier.py")
TEXT_RESULT_KINDS = frozenset({"project_text", "frame_set", "frame_prompts", "structured"})
# Settings that influence a model request (provider, model, route, limits).
TEXT_ROUTE_SETTINGS = (
    "text_llm_provider", "gpt_model", "gpt_reasoning_effort", "gpt_api_mode", "gpt_base_url",
    "gpt_chat_path", "gpt_timeout_s", "gpt_max_retries", "gpt_stream_read_timeout_s",
    "gpt_proxy_url", "gpt_api_enabled", "vibecode_base_url", "kie_api_base_url")
MEDIA_ROUTE_SETTINGS = (
    "image_provider", "outsee_default_image_model", "outsee_api_base_url",
    "kie_api_base_url", "kie_upload_base_url", "outsee_http_api")
# Nodes whose supervised recovery is a resume of a provider task (media_admission). Their input revision
# covers the paid request inputs only (see compute_input_revision).
MEDIA_RESUME_NODES = frozenset({"videos", "items", "music"})
# Nodes without a model request or a provider job that the supervisor re-runs once through the original worker
# (app/services/local_node_recovery.py). Their input revision covers the TRUE inputs of the local work only.
LOCAL_RECOVERY_NODES = frozenset({"assemble"})
# Everything that records a request-input baseline at attempt start (observation.observe_attempt).
BASELINE_NODES = MEDIA_RESUME_NODES | LOCAL_RECOVERY_NODES
MEDIA_FRAME_COLUMNS = ("id", "number", "uuid", "animation_prompt")
# assemble: the frame columns that are INPUT. start_ts / end_ts / duration_seconds are written by the assembly
# itself (progress of the attempt) and never enter the revision.
ASSEMBLE_FRAME_COLUMNS = ("id", "number", "uuid", "voiceover_text")
# Settings that shape the assembled file (subtitles, background music, ASR for subtitle timing).
ASSEMBLE_SETTINGS = ("subtitle_max_words", "subtitle_lead_seconds", "subtitle_chars_per_second",
                     "subtitle_rewhisper_on_assemble", "bgm_default_level", "bgm_path", "whisper_model", "asr_backend")
DATA_COLUMNS = ("topic", "general_plan", "hero_description", "script_text", "hero_mode",
                "hero_descriptions", "item_descriptions")


def _h(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def _unresolved(name: str, exc: BaseException | str) -> dict:
    """Never equal to any other computation (fail-closed)."""
    return {"unresolved": name, "error": type(exc).__name__ if isinstance(exc, BaseException) else str(exc),
            "nonce": uuid.uuid4().hex}


def _safe(name: str, fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - every failure must make the revision unequal
        return _unresolved(name, exc)


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

def _data(node: dict) -> dict:
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def _edge_kind(edge: dict) -> str:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    return str(data.get("kind") or edge.get("kind") or "after").strip().lower()


def structure_projection(nodes, edges) -> dict:
    """What decides which nodes run in what order; nothing cosmetic, nothing configurable."""
    node_rows, edge_rows = [], []
    for node in nodes or []:
        if not isinstance(node, dict) or node.get("id") in (None, ""):
            node_rows.append({"malformed": _h(node)})
            continue
        data = _data(node)
        node_rows.append({
            "id": str(node["id"]), "type": str(node.get("type") or ""),
            "group": str(data.get("groupId") or ""),
            "slot": data.get("slotIndex"), "overflow": bool(data.get("slotOverflow")),
            "marker": str(data.get("sd_agent") or data.get("agent") or ""),
            "disabled": bool(data.get("disabled"))})
    for edge in edges or []:
        if not isinstance(edge, dict) or not edge.get("source") or not edge.get("target"):
            edge_rows.append({"malformed": _h(edge)})
            continue
        edge_rows.append({"source": str(edge["source"]), "target": str(edge["target"]),
                          "kind": _edge_kind(edge)})
    node_rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
    edge_rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
    return {"scheme": GRAPH_SCHEME, "kind": "graph_structure", "nodes": node_rows, "edges": edge_rows}


def structure_hash(nodes, edges) -> str:
    return _h(structure_projection(nodes, edges))


# The stored execution graph_version is the structural hash.
graph_version = structure_hash


def legacy_graph_version(nodes, edges) -> str:
    """Scheme 1 value (hash of the whole snapshot). Only for recognising old records."""
    from app.services.observation import digest
    return digest({"nodes": nodes, "edges": edges})


def graph_version_status(nodes, edges, stored) -> str:
    """'ok', 'legacy_scheme' (stored under scheme 1) or 'changed'."""
    if stored and stored == structure_hash(nodes, edges):
        return "ok"
    if stored and stored == legacy_graph_version(nodes, edges):
        return "legacy_scheme"
    return "changed"


def graph_mismatch_reason(nodes, edges, stored) -> str | None:
    status = graph_version_status(nodes, edges, stored)
    if status == "ok":
        return None
    if status == "legacy_scheme":
        return ("graph revision recorded under legacy scheme 1 (whole snapshot hash); "
                "relaunch the managed execution to record the structural revision")
    return "graph revision changed (graph structure differs)"


# ---------------------------------------------------------------------------
# Node configuration
# ---------------------------------------------------------------------------

def node_config_projection(node: dict, meta: dict | None = None) -> dict:
    """Configuration that influences the execution of this node only."""
    data = _data(node)
    key = str(node.get("id") or "")
    meta = meta if isinstance(meta, dict) else {}
    per_node = {}
    for name in PER_NODE_META_KEYS:
        bucket = meta.get(name)
        if isinstance(bucket, dict) and key in bucket:
            value = bucket[key]
            if isinstance(value, dict):
                value = {k: v for k, v in value.items() if k not in COSMETIC_DATA_KEYS}
            per_node[name] = value
    return {
        "type": str(node.get("type") or ""),
        "data": {k: v for k, v in data.items()
                 if k not in COSMETIC_DATA_KEYS and k not in STRUCTURAL_DATA_KEYS},
        "extra": {k: v for k, v in node.items()
                  if k not in {"id", "type", "data"} and k not in COSMETIC_NODE_KEYS},
        "meta": per_node}


def node_config_revision(node: dict, meta: dict | None = None) -> str:
    return _h({"scheme": REVISION_SCHEME, "kind": "node_config",
               "config": node_config_projection(node, meta)})


# ---------------------------------------------------------------------------
# Code, prompts, settings
# ---------------------------------------------------------------------------

def _file_hash(rel: str) -> str:
    path = PROJECT_ROOT / rel
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_file(module: str) -> str | None:
    for rel in (f"app/orchestrator/steps/{module}.py", f"app/services/{module}.py",
                f"app/bots/{module}.py", f"app/{module}.py", f"app/services/{module}/__init__.py"):
        if (PROJECT_ROOT / rel).is_file():
            return rel
    return None


def executor_modules(capability) -> list[str]:
    """Module files named by the capability's executor description, e.g.
    'make_plan.run -> xlsx_step_runners.run_plan_xlsx'. Raises if one is unresolvable."""
    files = []
    for part in re.split(r"→|->", capability.executor or ""):
        for chunk in part.split("/"):
            chunk = chunk.strip()
            if not chunk:
                continue
            module = chunk.split(".")[0].strip()
            rel = _module_file(module)
            if rel is None:
                raise LookupError(f"executor module {module!r} not found")
            files.append(rel)
    if not files:
        raise LookupError("capability names no executor module")
    return files


def verifier_modules(node_type: str) -> list[str]:
    from app.orchestrator.node_spec import verifier_spec_for
    spec = verifier_spec_for(node_type)
    if spec is None:
        return []
    target = spec.observe
    module = target.partition(":")[0] if isinstance(target, str) else getattr(target, "__module__", "")
    rel = module.replace(".", "/") + ".py"
    if not module or not (PROJECT_ROOT / rel).is_file():
        raise LookupError(f"verifier module {module!r} not found")
    return [rel]


def code_files(node_type: str, capability) -> list[str]:
    files = set(executor_modules(capability)) | set(verifier_modules(node_type)) | set(APPLY_MODULES)
    files |= set(TEXT_REQUEST_MODULES if capability.result_kind in TEXT_RESULT_KINDS else MEDIA_MODULES)
    return sorted(files)


def _tree_hash(root: Path, patterns=("*.md", "*.json", "*.txt")) -> str:
    if not root.is_dir():
        return "absent"
    rows = {}
    for pattern in patterns:
        for path in root.rglob(pattern):
            rows[str(path.relative_to(root)).replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
    return _h(rows)


def _file_entry(path: Path | None) -> dict:
    if path is None:
        return {"path": None, "missing": True}
    try:
        return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    except FileNotFoundError:
        return {"path": path.name, "missing": True}


def resolved_prompt_inputs(project, node_key: str, node_type: str) -> dict:
    """The prompt FILE(S) this node actually reads, by content, plus the scoped
    overrides that select or replace them. Nothing about other nodes' prompts."""
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.services import prompt_library as pl
    step = NODE_TYPE_TO_STEP_CODE.get(node_type)
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    overrides = getattr(project, "prompt_overrides", None) or {}
    text_overrides = getattr(project, "gpt_text_overrides", None) or {}
    out: dict[str, Any] = {"step": step}
    if step is None or step not in pl.STEP_FOLDERS:
        out["files"] = []
        return out
    excel = pl.is_excel_gpt_prompt_step(step)
    sources = pl.excel_gpt_source_steps() if excel else (step,)
    out["overrides"] = {k: overrides.get(k) for k in sources}
    out["text_overrides"] = {k: text_overrides.get(k) for k in sources}
    params = meta.get("node_step_params")
    params = params if isinstance(params, dict) else {}
    out["step_params"] = {k: params.get(k) for k in sorted({step, "plan"})}
    name, source = pl.resolve_project_prompt_with_source(
        overrides, step, meta=meta, node_key=node_key if excel else None,
        slot_id="main" if excel else None)
    out["variant"], out["source"] = name, source
    if excel:
        group = pl._group_id_from_canvas_node(project, node_key)
        path = pl.resolve_excel_gpt_prompt_path(name, group_id=group, project=project, node_key=node_key)
    else:
        path = pl.prompt_path(step, name)
    files = [{"role": "main", **_file_entry(path)}]
    if not excel:
        from app.services.prompt_composer import project_uses_blocks_v2
        if project_uses_blocks_v2(overrides):
            # Composed from blocks/steps/styles: the used pieces are not enumerable here,
            # so the composition inputs as a whole stand in (bounded, fail-closed).
            root = pl.PROMPTS_ROOT
            out["composition"] = {"overrides": overrides,
                "trees": {d: _tree_hash(root / d) for d in ("blocks", "steps", "styles", "step-presets")}}
    else:
        from app.services.gpt_operator import (
            check_agent_upload_path, collect_source_prompts, operator_config)
        cfg = operator_config(project, node_key)
        if cfg.get("checkMode") or cfg.get("role") in ("review", "gate", "compare"):
            if cfg.get("checkPromptSource") == "agent":
                out["agent_trees"] = {"prompts": _tree_hash(pl.PROMPTS_ROOT),
                                      "templates": _tree_hash(pl.excel_gpt_template_dir())}
                files.append({"role": "agent_upload", **_file_entry(check_agent_upload_path(project, node_key))})
            else:
                for entry in collect_source_prompts(project, node_key):
                    files.append({"role": "source:" + str(entry.get("nodeKey")),
                                  "ok": bool(entry.get("ok")), "error": entry.get("error"),
                                  "text": hashlib.sha256((entry.get("text") or "").encode()).hexdigest(),
                                  "accompanying": hashlib.sha256((entry.get("accompanying") or "").encode()).hexdigest()})
    out["files"] = files
    vars_file = pl.PROMPTS_ROOT / "_vars.md"
    out["vars"] = _file_entry(vars_file) if vars_file.exists() else None
    return out


def model_route(project, node_key: str, node_type: str, capability) -> dict:
    """Settings and per-node choices that shape the model request. Not the whole settings."""
    from app.settings import settings
    names = TEXT_ROUTE_SETTINGS if capability.result_kind in TEXT_RESULT_KINDS else MEDIA_ROUTE_SETTINGS
    route = {name: getattr(settings, name) for name in names}
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    from app.services.vibecode_catalog import resolve_node_choice
    route["node_choice"] = resolve_node_choice(meta, node_key=node_key, node_type=node_type)
    if capability.result_kind in TEXT_RESULT_KINDS:
        from app.services.text_llm_catalog import resolve_active_model_id, resolve_active_provider
        route["active_model"] = resolve_active_model_id(settings)
        route["active_provider"] = resolve_active_provider(settings)
    return route


def capability_data(node_type: str, capability) -> dict:
    from app.orchestrator.node_spec import COVERAGE_FIELDS, capability_spec_for
    spec = capability_spec_for(node_type)
    # Coverage notes (panel text, forbidden-reason codes) are documentation: editing one must
    # not invalidate recovery tickets, so they stay out of the revision.
    return {"capability": capability.contract(),
            "spec": ({k: v for k, v in dataclasses.asdict(spec).items() if k not in COVERAGE_FIELDS}
                     if spec is not None else None)}


# ---------------------------------------------------------------------------
# Input revision
# ---------------------------------------------------------------------------

def _stat_row(path: Path) -> dict:
    try:
        st = path.stat()
        return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return {"missing": True}


def _files_under(root: Path, suffixes: tuple[str, ...], *, prefix: str | None = None) -> dict:
    """Relative path -> size and mtime of the input files of one folder. Absent folder = empty listing."""
    out: dict[str, dict] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in suffixes and (prefix is None or path.name.startswith(prefix)):
            out[path.relative_to(root).as_posix()] = _stat_row(path)
    return out


def assemble_inputs(project) -> dict:
    """True inputs of the local assembly that are not database rows: media files and assembly parameters.

    Fail-closed through _safe at the call site. Stat only (size, mtime): no file is read."""
    from app.settings import settings
    root = Path(project.data_dir)
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    steps = meta.get("node_step_params")
    board = meta.get("montage_board")
    board = board if isinstance(board, dict) else {}
    return {
        "videos": _files_under(root / "videos", (".mp4", ".mov", ".webm", ".mkv")),
        "scenes": _files_under(root / "scenes", (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov")),
        # the voice FILES (voice*.mp3/wav): words_*.json and sidecars are written by the attempt itself
        "voice": _files_under(root / "audio", (".mp3", ".wav", ".m4a"), prefix="voice"),
        "music": _files_under(root / "music", (".mp3", ".wav", ".m4a")),
        "sfx": _files_under(root / "sfx", (".wav", ".mp3")),
        "params": {
            "assemble": (steps.get("assemble") if isinstance(steps, dict) else None),
            "video_trims": board.get("video_trims"), "stale_videos": board.get("stale_videos"),
            "bgm": {k: v for k, v in sorted(meta.items()) if str(k).startswith(("bgm", "mass_bgm"))},
            "settings": {name: str(getattr(settings, name, None)) for name in ASSEMBLE_SETTINGS},
        },
    }


async def _find_node(session, project, node_key: str) -> dict | None:
    from sqlalchemy import select
    from app.models import WorkflowRun
    from app.services.canvas_graph import canvas_graph_from_meta
    canvas = canvas_graph_from_meta(project.meta if isinstance(project.meta, dict) else {})
    for node in (canvas or {}).get("nodes") or []:
        if isinstance(node, dict) and str(node.get("id")) == node_key:
            return node
    run = await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project.id))
    for node in (run.nodes_snapshot if run else None) or []:
        if isinstance(node, dict) and str(node.get("id")) == node_key:
            return node
    return None


async def compute_input_revision(session, project, node_key, *, parts=None) -> str:
    """Revision of everything that can change the outcome of THIS node's recovery."""
    from sqlalchemy import select
    from app.models import Frame, FrameText, Scene, SceneDesignCell
    from app.services.canvas_graph import canvas_graph_from_meta
    from app.services.excel_gpt_node import effective_node_type

    meta = project.meta if isinstance(project.meta, dict) else {}
    canvas = canvas_graph_from_meta(meta)
    node = await _find_node(session, project, str(node_key))
    node_type = None
    if node is not None:
        node_type = effective_node_type(node)
        if node_type.startswith("enrich_"):
            node_type = "excel_gpt"

    state: dict[str, Any] = {k: getattr(project, k, None) for k in DATA_COLUMNS}
    if node_type in MEDIA_RESUME_NODES:
        # A supervised media resume depends on the REQUEST inputs of the paid task (prompts), not on the
        # progress the failed attempt itself writes (frame status, fail counters, in-flight markers).
        rows = (await session.scalars(select(Frame).where(Frame.project_id == project.id)
                                      .order_by(Frame.id))).all()
        state[Frame.__tablename__] = [{c: getattr(row, c, None) for c in MEDIA_FRAME_COLUMNS} for row in rows]
    elif node_type in LOCAL_RECOVERY_NODES:
        # A supervised local re-run depends on what the local work READS: the voiceover text, the set of frames,
        # the clip / image / voice / music / sound-effect files and the assembly parameters. Everything the
        # attempt itself writes (frame timings, subtitle and ASR artifacts, the final file, counters) is progress.
        rows = (await session.scalars(select(Frame).where(Frame.project_id == project.id)
                                      .order_by(Frame.id))).all()
        state[Frame.__tablename__] = [{c: getattr(row, c, None) for c in ASSEMBLE_FRAME_COLUMNS} for row in rows]
        state["assemble_inputs"] = _safe("assemble_inputs", lambda: assemble_inputs(project))
    else:
        for model in (Frame, Scene, FrameText, SceneDesignCell):
            rows = (await session.scalars(select(model).where(model.project_id == project.id)
                                          .order_by(model.id))).all()
            state[model.__tablename__] = [
                {c.name: getattr(row, c.name) for c in model.__table__.columns
                 if c.name not in {"created_at", "updated_at"}} for row in rows]
    # Runtime canvas structure plus project-level disable switches.
    state["structure"] = _safe("structure", lambda: {
        "canvas": structure_hash((canvas or {}).get("nodes"), (canvas or {}).get("edges")) if canvas else None,
        "disabled_nodes": sorted(str(x) for x in (meta.get("disabled_nodes") or []))})
    if node is None or node_type is None:
        for name in ("node_config", "prompts", "model_route", "contract", "node_code"):
            state[name] = _unresolved(name, "node not found in canvas or workflow snapshot")
    else:
        from app.services.node_capabilities import capability_for
        capability = capability_for(node_type)
        state["node_config"] = _safe("node_config", lambda: node_config_projection(node, meta))
        state["prompts"] = _safe("prompts", lambda: resolved_prompt_inputs(project, str(node_key), node_type))
        state["model_route"] = _safe("model_route", lambda: model_route(project, str(node_key), node_type, capability))
        state["contract"] = _safe("contract", lambda: capability_data(node_type, capability))
        # An unmanaged node type has no executor description and recovery is denied for it by
        # capability alone; its code is not part of any recovery decision.
        state["node_code"] = ({"unmanaged": node_type} if not capability.managed else _safe(
            "node_code", lambda: {rel: _file_hash(rel) for rel in code_files(node_type, capability)}))
    state["revision_scheme"] = REVISION_SCHEME
    if parts is not None:
        parts.update({k: _h(v) for k, v in state.items()})
        parts["revision_scheme"] = REVISION_SCHEME
        parts["node_config_revision"] = _h({"scheme": REVISION_SCHEME, "kind": "node_config",
                                            "config": state["node_config"]})
    return _h(state)


# ---------------------------------------------------------------------------
# Stored values
# ---------------------------------------------------------------------------

def parts_scheme(parts) -> int | None:
    value = (parts or {}).get("revision_scheme") if isinstance(parts, dict) else None
    return value if isinstance(value, int) else None


def stored_revision_problem(parts) -> str | None:
    """None when the stored parts were produced under the current scheme."""
    scheme = parts_scheme(parts)
    if scheme == REVISION_SCHEME:
        return None
    if scheme is None:
        return (f"input revision recorded under legacy scheme 1 (whole prompts tree, settings and code); "
                f"current scheme is {REVISION_SCHEME}; the attempt must be re-recorded")
    return f"input revision scheme {scheme} is not supported (current {REVISION_SCHEME})"
