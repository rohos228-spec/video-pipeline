"""Контракт vp.check.v1 — парсер проверки GPT."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.check_analysis import (
    RESPONSE_FOOTER,
    SCHEMA_ID,
    append_response_footer,
    expected_artifacts_for_node_type,
    list_check_operator_steps,
    load_check_operator_prompt,
    looks_like_check_payload,
    parse_check_analysis,
    parse_gate_status,
    write_analysis_json,
)


def test_parse_pass_json() -> None:
    raw = json.dumps(
        {
            "schema": SCHEMA_ID,
            "verdict": "pass",
            "summary": "всё ок",
            "checks": [{"id": "prompt_ok", "ok": True, "note": ""}],
            "forward": {"mode": "inherit", "paths": []},
            "fix": {"target": "none", "instructions": ""},
        },
        ensure_ascii=False,
    )
    a = parse_check_analysis(raw)
    assert a.verdict == "pass"
    assert a.ok
    assert a.summary == "всё ок"
    assert a.checks[0].id == "prompt_ok"
    assert parse_gate_status(raw) == "pass"


def test_parse_fail_and_broken() -> None:
    fail = parse_check_analysis(
        '{"schema":"vp.check.v1","verdict":"fail","summary":"брак","checks":[]}'
    )
    assert fail.verdict == "fail"
    assert not fail.ok

    broken = parse_check_analysis("просто текст без json")
    assert broken.verdict == "fail"
    assert broken.raw_error
    assert "TXT" in (broken.raw_error or "") or "JSON" in (broken.raw_error or "")
    assert parse_gate_status("") == "fail"


def test_parse_fenced_and_aliases() -> None:
    fenced = """
Вот результат:
```json
{"schema":"vp.check.v1","verdict":"ok","summary":"норм","checks":[]}
```
"""
    a = parse_check_analysis(fenced)
    assert a.verdict == "pass"

    auto = parse_check_analysis('{"decision":"approved","criteria":[]}')
    assert auto.verdict == "pass"

    rej = parse_check_analysis('{"decision":"rejected","reason":"x"}')
    assert rej.verdict == "fail"


def test_looks_like_check_payload_blocks_voiceover_pollution() -> None:
    assert looks_like_check_payload(
        "# ОТЧЁТ ПРОВЕРКИ\nverdict: fail\n\n## summary\nx\n"
    )
    assert looks_like_check_payload(
        json.dumps(
            {
                "decision": "rejected",
                "criteria": [],
                "issues": ["a"],
                "red_flags": [],
            },
            ensure_ascii=False,
        )
    )
    assert looks_like_check_payload(
        json.dumps(
            {
                "schema": SCHEMA_ID,
                "verdict": "fail",
                "summary": "x",
                "checks": [],
            }
        )
    )
    assert not looks_like_check_payload(
        "В тёмном подъезде пахло сыростью. Герой шагнул вперёд."
    )


def test_save_voiceover_rejects_check_payload(tmp_path: Path, monkeypatch) -> None:
    from app.models import Project
    from app.services.chatgpt_xlsx import save_voiceover_text

    data = tmp_path / "data"
    monkeypatch.setattr("app.models.settings.data_dir", data)
    root = data / "videos" / "p1"
    root.mkdir(parents=True)
    p = Project(id=1, topic="t", slug="p1")
    monkeypatch.setattr(type(p), "data_dir", property(lambda self: root))
    dest = root / "voiceover.txt"
    dest.write_text("старый нормальный закадр", encoding="utf-8")
    bad = json.dumps(
        {"decision": "rejected", "criteria": [], "issues": ["x"], "red_flags": []},
        ensure_ascii=False,
    )
    try:
        save_voiceover_text(p, dest, bad)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "отчёт проверки" in str(e).lower() or "проверк" in str(e).lower()
    assert dest.read_text(encoding="utf-8") == "старый нормальный закадр"


def test_save_voiceover_rejects_tsv_writeback(tmp_path: Path, monkeypatch) -> None:
    from app.models import Project
    from app.services.chatgpt_xlsx import save_voiceover_text

    data = tmp_path / "data"
    monkeypatch.setattr("app.models.settings.data_dir", data)
    root = data / "videos" / "p1"
    root.mkdir(parents=True)
    p = Project(id=1, topic="t", slug="p1")
    monkeypatch.setattr(type(p), "data_dir", property(lambda self: root))
    dest = root / "voiceover.txt"
    dest.write_text("старый нормальный закадр", encoding="utf-8")
    bad = (
        "# Лист: Общий план\n"
        "@row=1\tТема\tдлинный текст\n"
        "# Лист: План\n"
        "@row=49\tзакадровый текст\tраз\tдва\n"
    )
    try:
        save_voiceover_text(p, dest, bad)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "tsv" in str(e).lower() or "лист" in str(e).lower()
    assert dest.read_text(encoding="utf-8") == "старый нормальный закадр"


def test_write_analysis_json(tmp_path: Path) -> None:
    a = parse_check_analysis(
        '{"schema":"vp.check.v1","verdict":"pass","summary":"ok","checks":[]}'
    )
    path = write_analysis_json(tmp_path / "excel_gpt_uploads" / "n1", a)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["verdict"] == "pass"
    assert data["schema"] == SCHEMA_ID


def test_footer_and_artifacts() -> None:
    assert SCHEMA_ID in RESPONSE_FOOTER
    assert "verdict" in RESPONSE_FOOTER
    out = append_response_footer("проверь картинки")
    assert "проверь картинки" in out
    assert SCHEMA_ID in out
    # повторно не дублируем
    assert append_response_footer(out) == out
    assert "scenes/" in expected_artifacts_for_node_type("images")
    assert "characters/" in expected_artifacts_for_node_type("hero")
    assert "project.xlsx" in expected_artifacts_for_node_type("plan")


def test_check_operator_prompt_library() -> None:
    steps = list_check_operator_steps()
    for need in (
        "hero",
        "images",
        "assemble",
        "plan",
        "script",
        "videos",
        "audio",
        "animation_prompts",
        "image_prompts",
    ):
        assert need in steps, need
    hero = load_check_operator_prompt("hero")
    assert hero is not None
    assert "персонаж" in hero.lower() or "hero" in hero.lower()
    assert SCHEMA_ID in hero
    # alias img_pr → image_prompts
    assert load_check_operator_prompt("img_pr") is not None


def test_new_step_prompts_present() -> None:
    steps = list_check_operator_steps()
    for need in ("items", "excel_gpt", "storage", "publish"):
        assert need in steps, need


def test_every_pipeline_node_type_resolves() -> None:
    """Каждый реальный тип ноды пайплайна должен резолвиться в промт-агент."""
    from app.orchestrator.node_registry import (
        CONFIG_NODE_TYPES,
        HITL_NODE_TYPES,
        WORK_NODES,
    )

    node_types: set[str] = set(WORK_NODES) | set(HITL_NODE_TYPES) | set(CONFIG_NODE_TYPES)
    node_types.update({"excel_gpt", "excel_feed"})
    # hitl_gate — ручной гейт без своего артефакта, промт не обязателен.
    node_types.discard("hitl_gate")
    for nt in sorted(node_types):
        prompt = load_check_operator_prompt(nt)
        assert prompt is not None, f"нет промта проверки для ноды {nt!r}"
        assert SCHEMA_ID in prompt, nt
        assert "verdict" in prompt.lower(), nt


def test_step_code_aliases_resolve() -> None:
    """step_code рабочих нод (img_pr/anim_pr/img/video) тоже резолвятся."""
    for code in ("img_pr", "anim_pr", "img", "video", "topic", "enrich_3"):
        assert load_check_operator_prompt(code) is not None, code


def test_prompts_declare_stable_check_ids() -> None:
    """Каждый агент перечисляет фиксированные check-id (строгий вывод)."""
    expected: dict[str, tuple[str, ...]] = {
        "plan": ("hook_present", "arc_clear", "topic_covered"),
        "images": ("files_present", "no_artifacts", "aspect_matches_target"),
        "videos": ("clips_present", "no_black_frames"),
        "excel_gpt": ("xlsx_valid", "task_applied", "no_data_loss"),
        "items": ("items_present",),
        "storage": ("files_present", "sorted_by_format"),
        "publish": ("final_ready",),
    }
    for step, ids in expected.items():
        body = load_check_operator_prompt(step)
        assert body is not None, step
        for cid in ids:
            assert cid in body, f"{step}: нет check-id {cid}"
