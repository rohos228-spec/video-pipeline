"""Prompt style variables: {{STYLE:key}} placeholders + <prompt>.vars.json sidecar.

Overrides live in project meta: node_step_params[step].style_vars[promptName][key].
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

STYLE_RE = re.compile(r"\{\{STYLE:([A-Za-z0-9_.\-]+)\}\}")


def sidecar_path(prompt_path: Path) -> Path | None:
    p = Path(prompt_path)
    for cand in (p.with_name(p.stem + ".vars.json"), p.with_name(p.name + ".vars.json")):
        if cand.is_file():
            return cand
    return None


def load_vars(prompt_path: Path | None) -> list[dict[str, Any]]:
    if not prompt_path:
        return []
    sc = sidecar_path(prompt_path)
    if sc is None:
        return []
    try:
        raw = json.loads(sc.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("style vars: bad sidecar {}: {}", sc, exc)
        return []
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("key") or "").strip():
            continue
        t = item.get("type") if item.get("type") in ("text", "textarea", "select") else "text"
        out.append(
            {
                "key": str(item["key"]).strip(),
                "label": str(item.get("label") or item["key"]),
                "default": "" if item.get("value") is None else str(item.get("value")),
                "type": t,
                "options": [str(o) for o in item.get("options") or []] if t == "select" else None,
            }
        )
    return out


def get_overrides(meta: Any, step_code: str, prompt_name: str) -> dict[str, str]:
    if not isinstance(meta, dict):
        return {}
    nsp = meta.get("node_step_params")
    step = nsp.get(step_code) if isinstance(nsp, dict) else None
    sv = step.get("style_vars") if isinstance(step, dict) else None
    per = sv.get(prompt_name) if isinstance(sv, dict) else None
    return {str(k): str(v) for k, v in per.items() if v is not None} if isinstance(per, dict) else {}


def with_overrides(meta: Any, step_code: str, prompt_name: str, values: dict[str, Any]) -> dict:
    """Return a new meta dict with overrides set (None/'__reset__' deletes a key)."""
    m = json.loads(json.dumps(meta if isinstance(meta, dict) else {}))
    nsp = m.setdefault("node_step_params", {})
    if not isinstance(nsp, dict):
        nsp = m["node_step_params"] = {}
    step = nsp.setdefault(step_code, {})
    if not isinstance(step, dict):
        step = nsp[step_code] = {}
    sv = step.setdefault("style_vars", {})
    per = dict(sv.get(prompt_name) or {})
    for k, v in values.items():
        if v is None:
            per.pop(k, None)
        else:
            per[k] = str(v)
    if per:
        sv[prompt_name] = per
    else:
        sv.pop(prompt_name, None)
    return m


def describe(prompt_path: Path | None, meta: Any, step_code: str, prompt_name: str) -> list[dict[str, Any]]:
    ov = get_overrides(meta, step_code, prompt_name)
    res = []
    for v in load_vars(prompt_path):
        o = ov.get(v["key"])
        res.append({**v, "value": o if o is not None else v["default"], "overridden": o is not None})
    return res


def apply_style_vars(text: str, prompt_path: Path | None, meta: Any, step_code: str, prompt_name: str) -> str:
    if not text or "{{STYLE:" not in text:
        return text
    vals = {v["key"]: v["value"] for v in describe(prompt_path, meta, step_code, prompt_name)}
    unknown: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        k = m.group(1)
        if k in vals:
            return vals[k]
        unknown.add(k)
        return m.group(0)

    out = STYLE_RE.sub(sub, text)
    if unknown:
        logger.warning("style vars: unknown placeholders {} in {} ({})", sorted(unknown), prompt_name, step_code)
    return out
