from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.change_control import lints, tiers
from app.services.change_control.errors import ChangeFailed, ChangeRefused
from app.services.change_control.gateway import ProjectGateway, project_ns
from app.services.change_control.store import sha

_NODE_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")


@dataclass
class NodeCtx:
    project_id: int
    node_key: str
    node_type: str
    step: str
    state: dict

    @property
    def ns(self):
        return project_ns(self.state)

    @property
    def is_excel(self) -> bool:
        from app.services import prompt_library as pl
        return pl.is_excel_gpt_prompt_step(self.step)


def resolve_node(state: dict, project_id: int, node_key: str) -> NodeCtx:
    if not isinstance(node_key, str) or not _NODE_KEY_RE.match(node_key):
        raise ChangeRefused("bad_node_key", "node_key has unsupported characters")
    from app.orchestrator.node_registry import NODE_TYPE_TO_STEP_CODE
    from app.services.canvas_graph import canvas_graph_from_meta
    from app.services.excel_gpt_node import effective_node_type

    canvas = canvas_graph_from_meta(state.get("meta")) or {}
    node = next((n for n in canvas.get("nodes") or []
                 if isinstance(n, dict) and str(n.get("id")) == node_key), None)
    if node is None:
        raise ChangeRefused("node_not_found", f"node {node_key!r} is not on the project canvas")
    node_type = effective_node_type(node)
    if node_type.startswith("enrich_"):
        node_type = "excel_gpt"
    if node_type not in tiers.ALLOW_LIST:
        raise ChangeRefused("node_type_not_allowed",
                            f"node type {node_type!r} has no allowed changes")
    step = NODE_TYPE_TO_STEP_CODE.get(node_type)
    if not step:
        raise ChangeRefused("node_type_not_allowed", f"node type {node_type!r} has no step")
    return NodeCtx(project_id, node_key, node_type, step, state)


def with_state(nctx: NodeCtx, **changes: Any) -> NodeCtx:
    state = copy.deepcopy(nctx.state)
    state.update(changes)
    return NodeCtx(nctx.project_id, nctx.node_key, nctx.node_type, nctx.step, state)


def _text_diff(old: str, new: str) -> str:
    out = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                         "current", "proposed", lineterm=""))
    if len(out) > tiers.MAX_DIFF_CHARS:
        out = out[:tiers.MAX_DIFF_CHARS] + "\n... [diff truncated]"
    return out


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _norm_num(n: float, kind: str) -> int | float:
    if kind == "integer" or n == int(n):
        return int(round(n))
    return n



class PromptHandler:
    group = "prompt"

    def target(self, nctx: NodeCtx) -> str:
        return f"project:{nctx.project_id}:{nctx.node_key}:prompt"

    def _resolve(self, nctx: NodeCtx) -> tuple[str, str, Path]:
        from app.services import prompt_library as pl
        ns = nctx.ns
        excel = nctx.is_excel
        name, source = pl.resolve_project_prompt_with_source(
            ns.prompt_overrides or {}, nctx.step, meta=ns.meta,
            node_key=nctx.node_key if excel else None, slot_id="main" if excel else None)
        if excel:
            path = pl.resolve_excel_gpt_prompt_path(
                name, group_id=pl._group_id_from_canvas_node(ns, nctx.node_key),
                project=ns, node_key=nctx.node_key)
        else:
            path = pl.prompt_path(nctx.step, name)
        return name, source, path

    def _effective(self, nctx: NodeCtx) -> tuple[str, Path, str]:
        name, _source, path = self._resolve(nctx)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        return name, path, text

    _SOURCE_NAMES = {"slot": "slot", "preferred": "slot", "override": "project_override",
                     "global": "global_active", "default": "default"}

    def describe(self, nctx: NodeCtx) -> dict:
        name, source, path = self._resolve(nctx)
        out = {"variant": name, "source": self._SOURCE_NAMES.get(source, source),
               "text": "", "text_chars": 0, "truncated": False, "base_text_sha256": ""}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            reason = ("файл варианта не найден" if isinstance(exc, FileNotFoundError)
                      else "файл варианта не читается")
            out.update(source="missing", missing_reason=f"{reason}: вариант «{name}»")
            return out
        out["base_text_sha256"] = sha(text)
        out["text_chars"] = len(text)
        if len(text) > tiers.PROMPT_MAX_CHARS:
            out.update(text=text[:tiers.PROMPT_MAX_CHARS], truncated=True)
        else:
            out["text"] = text
        return out

    def live(self, nctx: NodeCtx) -> dict:
        name, _path, text = self._effective(nctx)
        return {"variant": name, "text_sha256": sha(text)}

    def _shadowed_by_override(self, nctx: NodeCtx) -> str | None:
        from app.services import prompt_library as pl
        if nctx.is_excel:
            return None
        chosen = (nctx.ns.prompt_overrides or {}).get(nctx.step)
        clean = pl._clean_variant_name(str(chosen)) if chosen else ""
        if clean and pl.prompt_path(nctx.step, clean).exists():
            return clean
        return None

    def base_text(self, nctx: NodeCtx) -> str:
        return self._effective(nctx)[2]

    def _variant_text(self, nctx: NodeCtx, variant: str) -> str | None:
        from app.services import prompt_library as pl
        if nctx.is_excel:
            if not pl.excel_gpt_prompt_exists(variant):
                return None
            path = pl.resolve_excel_gpt_prompt_path(
                variant, group_id=pl._group_id_from_canvas_node(nctx.ns, nctx.node_key),
                project=nctx.ns, node_key=nctx.node_key)
        else:
            path = pl.prompt_path(nctx.step, variant)
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except OSError:
            return None

    @staticmethod
    def variant_name_for(node_key: str, text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", node_key.lower()).strip("-")[:40] or "node"
        return f"cc-{slug}-{sha(text)[:8]}"

    def prepare(self, nctx: NodeCtx, kind: str, raw: dict) -> dict:
        if kind == tiers.K_PROMPT_VARIANT:
            if set(raw) != {"variant"} or not isinstance(raw.get("variant"), str):
                raise ChangeRefused("bad_candidate", "candidate must be {variant: string}")
            variant = raw["variant"].strip()
            text = self._variant_text(nctx, variant) if variant else None
            if text is None:
                raise ChangeRefused("variant_not_found",
                                    f"prompt variant {variant!r} does not exist for this step")
            cand = {"variant": variant, "variant_sha256": sha(text)}
        else:
            if set(raw) != {"text"} or not isinstance(raw.get("text"), str):
                raise ChangeRefused("bad_candidate", "candidate must be {text: string}")
            text = raw["text"]
            cand = {"text": text, "variant": self.variant_name_for(nctx.node_key, text),
                    "base_text_sha256": sha(self.base_text(nctx))}
        errors = self.validate(nctx, cand, self.live(nctx))
        if errors:
            raise ChangeRefused("lint_failed", "; ".join(errors), errors=errors)
        return cand

    def validate(self, nctx: NodeCtx, cand: dict, live: dict) -> list[str]:
        from app.services import prompt_library as pl
        from app.services.prompt_composer import project_uses_blocks_v2
        errors: list[str] = []
        variant = cand.get("variant")
        if (not isinstance(variant, str) or not pl.is_valid_prompt_name(variant)
                or variant.startswith(".") or pl._clean_variant_name(variant) != variant):
            return ["variant name is not valid"]
        if not nctx.is_excel and project_uses_blocks_v2(nctx.ns.prompt_overrides or {}):
            errors.append("project composes prompts from blocks: a variant/text change "
                          "would not change the request")
        shadow = self._shadowed_by_override(nctx)
        if shadow:
            errors.append(f"project override prompt_overrides[{nctx.step!r}]={shadow!r} has "
                          "precedence over the node slot; the change would not take effect")
        if "text" in cand:
            base = self.base_text(nctx)
            if sha(base) != cand.get("base_text_sha256"):
                errors.append("current prompt text differs from the one the proposal was based on")
            errors.extend(lints.lint_prompt_text(
                cand["text"], base, step=nctx.step, base_name=live.get("variant") or ""))
            if self.variant_name_for(nctx.node_key, cand["text"]) != variant:
                errors.append("variant name does not match the text")
            existing = self._variant_text(nctx, variant)
            if existing is not None and existing != cand["text"]:
                errors.append("a different prompt file already has this variant name")
        else:
            text = self._variant_text(nctx, variant)
            if text is None:
                errors.append("variant file does not exist")
            elif sha(text) != cand.get("variant_sha256"):
                errors.append("variant file changed after the proposal was made")
            if variant == live.get("variant"):
                errors.append("variant is already selected")
        return errors

    def after(self, nctx: NodeCtx, cand: dict) -> dict:
        variant = cand["variant"]
        text = cand["text"] if "text" in cand else (self._variant_text(nctx, variant) or "")
        out = {"variant": variant, "text_sha256": sha(text)}
        if "text" in cand:
            out["text"] = cand["text"]
        return out

    def diff(self, nctx: NodeCtx, cand: dict, live: dict) -> str:
        if "text" in cand:
            return _text_diff(self.base_text(nctx), cand["text"])
        return f"variant: {live.get('variant')} -> {cand['variant']}"

    def comparable(self, content: dict) -> dict:
        return {"variant": content.get("variant"), "text_sha256": content.get("text_sha256")}

    async def write(self, gateway: ProjectGateway, nctx: NodeCtx, content: dict) -> None:
        from app.services import prompt_library as pl
        variant = content["variant"]
        text = content.get("text")
        if text is not None:
            existing = self._variant_text(nctx, variant)
            if existing is None:
                pl.write_prompt(nctx.step, variant, text)
            elif existing != text:
                raise ChangeFailed("variant_file_conflict",
                                   "a prompt file with this name has different content")
        live = self.live(nctx)
        if live["variant"] == variant and live["text_sha256"] == content.get("text_sha256"):
            return
        await gateway.patch(nctx.project_id, {
            "meta": {"prompt_slot_variants": {nctx.node_key: {"main": variant}}}})



class OptionsHandler:
    group = "options"

    def target(self, nctx: NodeCtx) -> str:
        return f"project:{nctx.project_id}:{nctx.node_key}:options"

    def live(self, nctx: NodeCtx) -> dict:
        from app.services.gpt_operator import operator_config
        cfg = operator_config(nctx.ns, nctx.node_key)
        return {"values": {f: copy.deepcopy(cfg.get(f)) for f in tiers.NODE_OPTION_FIELDS}}

    def prepare(self, nctx: NodeCtx, kind: str, raw: dict) -> dict:
        values = raw.get("values") if set(raw) == {"values"} else None
        if not isinstance(values, dict) or not values:
            raise ChangeRefused("bad_candidate", "candidate must be {values: {...}}")
        allowed = set(tiers.allowed_kinds(nctx.node_type).get(tiers.K_NODE_OPTIONS, ()))
        bad = sorted(set(values) - allowed)
        if bad:
            raise ChangeRefused("field_not_allowed",
                                f"fields outside the allow-list: {bad}; allowed: {sorted(allowed)}")
        from app.services.gpt_operator import VALID_EMIT_KINDS, VALID_OUTPUTS
        errors: list[str] = []
        for f, v in values.items():
            if f == "outputMode":
                if v not in VALID_OUTPUTS:
                    errors.append(f"outputMode must be one of {sorted(VALID_OUTPUTS)}")
            elif f == "emitKinds":
                if (not isinstance(v, list) or not v or len(set(map(str, v))) != len(v)
                        or any(x not in VALID_EMIT_KINDS for x in v)):
                    errors.append("emitKinds must be a unique non-empty subset of "
                                  f"{sorted(VALID_EMIT_KINDS)}")
            elif not isinstance(v, bool):
                errors.append(f"{f} must be a boolean")
        if errors:
            raise ChangeRefused("lint_failed", "; ".join(errors), errors=errors)
        cand = {"values": copy.deepcopy(values)}
        errors = self.validate(nctx, cand, self.live(nctx))
        if errors:
            raise ChangeRefused("lint_failed", "; ".join(errors), errors=errors)
        return cand

    def validate(self, nctx: NodeCtx, cand: dict, live: dict) -> list[str]:
        from app.services.gpt_operator import operator_config
        values = cand.get("values") or {}
        if all(live["values"].get(f) == v for f, v in values.items()):
            return ["values are already set"]
        meta = copy.deepcopy(nctx.state.get("meta") or {})
        nodes = dict(meta.get("excel_gpt_nodes") or {})
        cur = dict(nodes.get(nctx.node_key) or {})
        cur.update(copy.deepcopy(values))
        nodes[nctx.node_key] = cur
        meta["excel_gpt_nodes"] = nodes
        eff = operator_config(with_state(nctx, meta=meta).ns, nctx.node_key)
        return [f"{f} would be coerced to {eff.get(f)!r} by the node config"
                for f, v in values.items() if eff.get(f) != v]

    def after(self, nctx: NodeCtx, cand: dict) -> dict:
        values = self.live(nctx)["values"]
        values.update(copy.deepcopy(cand["values"]))
        return {"values": values}

    def diff(self, nctx: NodeCtx, cand: dict, live: dict) -> str:
        return "\n".join(f"{f}: {live['values'].get(f)!r} -> {v!r}"
                         for f, v in cand["values"].items())

    def comparable(self, content: dict) -> dict:
        return {"values": content.get("values")}

    async def write(self, gateway: ProjectGateway, nctx: NodeCtx, content: dict) -> None:
        live = self.live(nctx)["values"]
        changed = {f: v for f, v in content["values"].items() if live.get(f) != v}
        if changed:
            await gateway.patch(nctx.project_id,
                                {"meta": {"excel_gpt_nodes": {nctx.node_key: changed}}})



class StepParamsHandler:
    group = "step_params"

    def target(self, nctx: NodeCtx) -> str:
        return f"project:{nctx.project_id}:{nctx.node_key}:step_params"

    def live(self, nctx: NodeCtx) -> dict:
        limits = tiers.STEP_PARAM_LIMITS.get(nctx.step, {})
        raw = (nctx.state.get("meta") or {}).get("node_step_params")
        bucket = raw.get(nctx.step) if isinstance(raw, dict) else None
        bucket = bucket if isinstance(bucket, dict) else {}
        values = {}
        for f, spec in limits.items():
            n = _num(bucket.get(f))
            values[f] = _norm_num(n, spec["type"]) if n is not None else None
        return {"step": nctx.step, "values": values}

    def prepare(self, nctx: NodeCtx, kind: str, raw: dict) -> dict:
        if set(raw) != {"step", "values"} or not isinstance(raw.get("values"), dict) \
                or not raw["values"]:
            raise ChangeRefused("bad_candidate", "candidate must be {step, values: {...}}")
        if raw["step"] != nctx.step:
            raise ChangeRefused("field_not_allowed", f"step must be {nctx.step!r} for this node")
        allowed = set(tiers.allowed_kinds(nctx.node_type).get(tiers.K_STEP_PARAMS, ()))
        bad = sorted(set(raw["values"]) - allowed)
        if bad:
            raise ChangeRefused("field_not_allowed",
                                f"fields outside the allow-list: {bad}; allowed: {sorted(allowed)}")
        limits = tiers.STEP_PARAM_LIMITS[nctx.step]
        norm: dict[str, Any] = {}
        errors: list[str] = []
        for f, v in raw["values"].items():
            spec = limits[f]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors.append(f"{f} must be a number")
            elif spec["type"] == "integer" and v != int(v):
                errors.append(f"{f} must be an integer")
            else:
                norm[f] = _norm_num(float(v), spec["type"])
        if errors:
            raise ChangeRefused("lint_failed", "; ".join(errors), errors=errors)
        cand = {"step": nctx.step, "values": norm}
        errors = self.validate(nctx, cand, self.live(nctx))
        if errors:
            raise ChangeRefused("lint_failed", "; ".join(errors), errors=errors)
        return cand

    def validate(self, nctx: NodeCtx, cand: dict, live: dict) -> list[str]:
        errors: list[str] = []
        limits = tiers.STEP_PARAM_LIMITS.get(nctx.step, {})
        if cand.get("step") != nctx.step:
            return ["step does not match the node"]
        for f, v in (cand.get("values") or {}).items():
            spec = limits.get(f)
            if spec is None:
                errors.append(f"{f} is not an allowed parameter")
                continue
            if not spec["min"] <= v <= spec["max"]:
                errors.append(f"{f}={v} is outside [{spec['min']}, {spec['max']}]")
            cur = live["values"].get(f)
            if cur is None:
                errors.append(f"{f} has no current value: nothing to roll back to")
            elif cur == v:
                errors.append(f"{f} already equals {v}")
            elif not cur / tiers.MAX_STEP_PARAM_FACTOR <= v <= cur * tiers.MAX_STEP_PARAM_FACTOR:
                errors.append(f"{f}: change from {cur} to {v} exceeds "
                              f"x{tiers.MAX_STEP_PARAM_FACTOR}")
        merged = {**live["values"], **(cand.get("values") or {})}
        for lo, hi in (("cell_min_chars", "cell_max_chars"), ("cell_avg_min", "cell_avg_max"),
                       ("cell_min_chars", "cell_avg_min"), ("cell_avg_max", "cell_max_chars")):
            if merged.get(lo) is not None and merged.get(hi) is not None and merged[lo] > merged[hi]:
                errors.append(f"{lo} must not exceed {hi}")
        return errors

    def after(self, nctx: NodeCtx, cand: dict) -> dict:
        live = self.live(nctx)
        live["values"].update(cand["values"])
        return live

    def diff(self, nctx: NodeCtx, cand: dict, live: dict) -> str:
        return "\n".join(f"{nctx.step}.{f}: {live['values'].get(f)} -> {v}"
                         for f, v in cand["values"].items())

    def comparable(self, content: dict) -> dict:
        return {"step": content.get("step"), "values": content.get("values")}

    async def write(self, gateway: ProjectGateway, nctx: NodeCtx, content: dict) -> None:
        live = self.live(nctx)["values"]
        changed = {f: v for f, v in content["values"].items()
                   if v is not None and live.get(f) != v}
        if changed:
            await gateway.patch(nctx.project_id,
                                {"meta": {"node_step_params": {nctx.step: changed}}})


_PROMPT, _OPTIONS, _STEP = PromptHandler(), OptionsHandler(), StepParamsHandler()
_BY_KIND = {tiers.K_PROMPT_VARIANT: _PROMPT, tiers.K_PROMPT_TEXT: _PROMPT,
            tiers.K_NODE_OPTIONS: _OPTIONS, tiers.K_STEP_PARAMS: _STEP}
_BY_SUFFIX = {"prompt": _PROMPT, "options": _OPTIONS, "step_params": _STEP}


def handler_for_kind(kind: str):
    return _BY_KIND[kind]


def handler_for_target(target: str):
    return _BY_SUFFIX[target.rsplit(":", 1)[1]]


def describe_prompt_base(nctx: NodeCtx) -> dict:
    return _PROMPT.describe(nctx)


_KIND_TITLES = {"prompt": "промпт", "options": "настройки", "step_params": "параметры шага"}
_TARGET_RE = re.compile(r"^project:(\d+):([A-Za-z0-9_.\-]+):(prompt|options|step_params)$")


def node_title(node_key: str) -> str:
    from app.services import prompt_library as pl
    key = str(node_key or "")
    for step in ("plan", "script", "split"):
        if pl._node_key_belongs_to_step(key, step):
            return re.sub(r"^\d+\.\s*", "", pl.STEP_HUMAN_NAMES[step])
    if "excel_gpt" in key.lower():
        return f"Работа с GPT ({key})"
    return key


def target_title(target: str) -> str:
    m = _TARGET_RE.match(target or "")
    if not m:
        return "настройка проекта"
    pid, key, kind = m.groups()
    return f"{_KIND_TITLES[kind]} ноды «{node_title(key)}», проект {pid}"
