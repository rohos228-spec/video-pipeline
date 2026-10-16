from __future__ import annotations

LEVEL_LABELS = {
    "managed": "Под управлением: оркестратор запускает, проверяет результат и, где разрешено, повторяет",
    "observed": "Под наблюдением: журнал и проверка или описание результата, действий нет",
    "human_only": "Только человек: кроме общего журнала, оркестратор ничего не делает",
}

TITLES = {
    "plan": "Сценарий", "script": "Закадровый текст", "split": "Разбивка",
    "excel_gpt": "Работа с GPT", "scene_design": "Сцена (старый тип)", "sd_agent": "Агент сцены",
    "sd_assemble": "Сборка сцены", "hero": "Персонажи", "items": "Предметы",
    "image_prompts": "Промты картинок", "images": "Картинки",
    "animation_prompts": "Промты анимации", "videos": "Видео", "audio": "Озвучка",
    "music": "Музыка", "sfx_plan": "План звуков", "sfx_gen": "Звуки", "assemble": "Сборка",
    "publish": "Публикация",
    **{f"enrich_{n}": f"Доп. работа с Excel {n} (старый тип)" for n in range(1, 6)},
}


def _ensure_registered() -> None:
    import app.services.node_capabilities
    import app.services.decision_v1


def registry_node_types() -> list[str]:
    _ensure_registered()
    from app.orchestrator.node_registry import WORK_NODES
    return ["excel_gpt", *[t for t in WORK_NODES if t != "excel_gpt"]]


def uncovered_node_types() -> list[str]:
    _ensure_registered()
    from app.orchestrator.node_spec import registered_capability_specs
    specs = registered_capability_specs()
    return [t for t in registry_node_types() if t not in specs]


def node_coverage_row(spec) -> dict:
    from app.orchestrator.node_spec import (
        KNOWN_FORBIDDEN_CODES, coverage_evidence, coverage_level,
    )
    level = coverage_level(spec)
    recovery = list(spec.recovery_actions)
    forbidden = None
    if not recovery:
        forbidden = {"code": spec.forbidden_code,
                     "meaning": KNOWN_FORBIDDEN_CODES.get(spec.forbidden_code or ""),
                     "reason": spec.forbidden_reason}
    return {
        "node_type": spec.node_type,
        "title": TITLES.get(spec.node_type, spec.node_type),
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "managed": spec.effective_managed,
        "acceptance": spec.acceptance if spec.effective_managed else None,
        "evidence": coverage_evidence(spec),
        "verifies": list(spec.verifies),
        "recovery": {"actions": recovery, "cap_per_node": spec.recovery_cap if recovery else 0,
                     "profile_compatible": spec.profile_compatible if recovery else False,
                     "resume_only": bool(recovery and spec.recovery_only)},
        "limits": list(spec.limits),
        "forbidden": forbidden,
    }


def node_coverage() -> dict:
    _ensure_registered()
    from app.orchestrator.node_spec import registered_capability_specs
    specs = registered_capability_specs()
    rows = [node_coverage_row(specs[t]) for t in registry_node_types() if t in specs]
    summary = {level: sum(r["level"] == level for r in rows) for level in LEVEL_LABELS}
    summary["total"] = len(rows)
    summary["with_automatic_recovery"] = sum(bool(r["recovery"]["actions"]) for r in rows)
    return {"nodes": rows, "summary": summary, "uncovered": uncovered_node_types(),
            "levels": LEVEL_LABELS, "automatic_actions": False, "semantic_quality_verified": False}
