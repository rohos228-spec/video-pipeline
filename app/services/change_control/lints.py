from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.change_control import tiers

_PLACEHOLDER_RE = re.compile(r"\{\{[A-Za-z0-9_:]+\}\}")
_LEGACY_TOPIC = ("[ВСТАВЬ ТЕМУ]", "(тема ролика)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKERS_FILE = Path(__file__).with_name("contract_markers.json")


def load_markers() -> dict[str, list[str]]:
    try:
        raw = json.loads(_MARKERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("contract_markers.json is unreadable") from None
    return {k: [str(x) for x in v] for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, list)}


def placeholders(text: str) -> set[str]:
    found = set(_PLACEHOLDER_RE.findall(text))
    found.update(m for m in _LEGACY_TOPIC if m in text)
    return found


def required_markers(step: str, name: str, base_text: str) -> set[str]:
    data = load_markers()
    required = {m for m in data.get("*", []) if m in base_text}
    required.update(data.get(f"{step}/{name}", []))
    return required


def new_secret_hits(candidate: str, base: str) -> list[str]:
    from app.services.observation import _CREDENTIAL_TEXT
    cand = {m.group(0) for m in _CREDENTIAL_TEXT.finditer(candidate)}
    old = {m.group(0) for m in _CREDENTIAL_TEXT.finditer(base)}
    return sorted(cand - old)


def lint_prompt_text(candidate: str, base: str, *, step: str, base_name: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(candidate, str):
        return ["text must be a string"]
    stripped = candidate.strip()
    if len(stripped) < tiers.PROMPT_MIN_CHARS:
        errors.append(f"text shorter than {tiers.PROMPT_MIN_CHARS} chars")
    if len(candidate) > tiers.PROMPT_MAX_CHARS:
        errors.append(f"text longer than {tiers.PROMPT_MAX_CHARS} chars")
    if _CONTROL_RE.search(candidate):
        errors.append("text contains control characters")
    if candidate == base:
        errors.append("text equals the current prompt")
    if base.strip():
        ratio = len(candidate) / max(1, len(base))
        if ratio < tiers.PROMPT_MIN_RATIO:
            errors.append(f"text is {ratio:.2f} of the current size (min {tiers.PROMPT_MIN_RATIO})")
        if ratio > tiers.PROMPT_MAX_RATIO:
            errors.append(f"text is {ratio:.2f} of the current size (max {tiers.PROMPT_MAX_RATIO})")
    have, base_ph = placeholders(candidate), placeholders(base)
    lost = sorted(base_ph - have)
    if lost:
        errors.append(f"required placeholders lost: {lost}")
    unknown = sorted(have - base_ph)
    if unknown:
        errors.append(f"unknown placeholders added: {unknown}")
    missing = sorted(m for m in required_markers(step, base_name, base) if m not in candidate)
    if missing:
        errors.append(f"contract markers lost: {missing}")
    hits = new_secret_hits(candidate, base)
    if hits:
        errors.append("text contains credential-like content")
    return errors
