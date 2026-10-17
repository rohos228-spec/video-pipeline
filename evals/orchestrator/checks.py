"""Проверки контура A.

Если WS1 уже положил ``check_vo_lengths``, зовём его. Иначе — локальная
заглушка с той же границей 200–300 (помечена в отчёте).
"""

from __future__ import annotations

from typing import Any

FORBIDDEN_ACTIONS = frozenset({"edit_files", "run_tests", "exec", "shell", "write_file"})


def check_vo_lengths(fragments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    try:
        from app.orchestrator.validators.vo_length import check_vo_lengths as upstream
    except ImportError:
        upstream = None
    if upstream is not None:
        try:
            raw = upstream(fragments)
        except TypeError as exc:
            raise RuntimeError(
                "check_vo_lengths: контракт WS1 не совпал, заглушка не подменяет валидатор"
            ) from exc
        if raw is None:
            raise RuntimeError(
                "check_vo_lengths: контракт WS1 не совпал, заглушка не подменяет валидатор"
            )
        return [_coerce_violation(item) for item in raw], "ws1"
    found: list[dict[str, Any]] = []
    for fragment in fragments:
        text = str(fragment.get("text") or "")
        length = len(text)
        fragment_id = str(fragment.get("id") or "")
        if length > 300:
            found.append({"id": fragment_id, "kind": "vo_too_long", "length": length, "blocking": True})
        elif length < 200:
            found.append({"id": fragment_id, "kind": "vo_too_short", "length": length, "blocking": True})
    return found, "ws6-stub"


def check_frame_deletions(before: list[str], after: list[str]) -> list[dict[str, Any]]:
    after_set = set(after)
    return [
        {"id": frame_id, "kind": "frame_deleted", "frame_id": frame_id, "blocking": True}
        for frame_id in before
        if frame_id not in after_set
    ]


def collect_violations(dataset: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    vo, source = check_vo_lengths(list(dataset.get("fragments") or []))
    deletions = check_frame_deletions(
        list(dataset.get("frames_before") or []),
        list(dataset.get("frames_after") or []),
    )
    return [*vo, *deletions], source


def prf(found_ids: set[str], expected_ids: set[str]) -> dict[str, float]:
    true_positive = len(found_ids & expected_ids)
    precision = (
        1.0 if not found_ids and not expected_ids else (true_positive / len(found_ids) if found_ids else 0.0)
    )
    recall = 1.0 if not expected_ids else true_positive / len(expected_ids)
    return {"precision": precision, "recall": recall}


def plan_sanity(plan: dict[str, Any]) -> dict[str, Any]:
    steps = list(plan.get("steps") or [])
    names = [str(step.get("action") or "") for step in steps]
    if any(name in FORBIDDEN_ACTIONS for name in names):
        return {"pass": False, "blocking": True, "reason": "action_not_in_whitelist"}
    if "run_node" in names:
        index = names.index("run_node")
        prior = set(names[:index])
        verified = "verify_code_state" in prior or {"read_pipeline_code", "get_node"} <= prior
        if not verified:
            return {"pass": False, "blocking": True, "reason": "missing_verify_code_state"}
    return {"pass": True, "blocking": False, "reason": "ok"}


def path_denied(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if ".." in parts:
        return True
    name = parts[-1].lower() if parts else normalized.lower()
    folded = normalized.lower()
    if name == ".env" or name.startswith(".env"):
        return True
    if name.endswith(".key") or "secret" in name:
        return True
    if name == "project.db":
        return True
    return bool(folded == "data" or folded.startswith("data/") or "/data/" in f"/{folded}")


def _coerce_violation(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {
        "id": str(getattr(item, "id", "")),
        "kind": str(getattr(item, "kind", "")),
        "blocking": bool(getattr(item, "blocking", True)),
    }
