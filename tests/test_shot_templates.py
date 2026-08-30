"""Каталог шаблонов T/X для ноды shots."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.prompt_library import resolve_excel_gpt_prompt_path
from app.services.shot_templates import (
    assign_templates_for_action,
    clear_shot_templates_cache,
    coverage_template_reason,
    format_shot_templates_catalog,
    format_when_assignments,
    load_shot_templates,
    neighbor_place_hints,
    plan_templates_for_action,
    select_template_when,
    template_max_shots,
)


def test_template_max_shots_from_catalog() -> None:
    clear_shot_templates_cache()
    assert template_max_shots("T0") == 1
    assert template_max_shots("T5") == 4
    assert template_max_shots("T3") == 2
    # T8 — динамическая лестница (сколько мест, столько кадров): без лимита.
    assert template_max_shots("T8") == 0


# Пример пользователя: герой идёт в полицию и пишет заявление о краже.
POLICE_ACTION = (
    "1. улица — Михаил идёт к зданию полиции\n(Михаил пришёл к отделению полиции,)\n"
    "2. отделение полиции — заходит внутрь, снимает кепку\n(зашёл внутрь,)\n"
    "3. отделение полиции — общается с дежурным полицейским\n(рассказал дежурному о краже)\n"
    "4. отделение полиции — пишет заявление за столом\n(и написал заявление.)\n"
)


def test_police_example_chain_templates() -> None:
    """Путь → новое место → диалог → работа рук: T6 → T3 → T1 → T5."""
    clear_shot_templates_cache()
    assigned = assign_templates_for_action(POLICE_ACTION)
    assert [assigned[i] for i in sorted(assigned)] == ["T6", "T3", "T1", "T5"]


def test_police_example_same_place_compression() -> None:
    """Место сцен 2–4 одно → сжатие «место уже было», шаблон не меняется."""
    clear_shot_templates_cache()
    plan = plan_templates_for_action(POLICE_ACTION)
    by_n = {row["n"]: row for row in plan}
    assert by_n[2]["same_place"] is False
    assert by_n[3]["same_place"] is True
    assert "T1-c2" in by_n[3]["compression"]
    assert by_n[4]["same_place"] is True
    assert "K0" in by_n[4]["compression"]


def test_t8_chain_repeat_allowed() -> None:
    """Биография «жил → служил → работал»: T8>T8>T8 — норма, не брак."""
    clear_shot_templates_cache()
    action = (
        "1. двор Киселёвска — мальчик Ткач бегает между домами\n(Ткач родился в Киселёвске,)\n"
        "2. армия — Ткач в солдатской форме стоит на плацу\n(прошёл службу в армии,)\n"
        "3. после армии — снимает гимнастёрку, надевает милицейскую форму, входит в отделение\n"
        "(после чего начал карьеру в милиции.)\n"
    )
    assigned = assign_templates_for_action(action)
    assert [assigned[i] for i in sorted(assigned)] == ["T8", "T8", "T8"]


def test_same_place_keeps_template_with_compression() -> None:
    """То же место — тот же T* по дереву + сжатие, а не подмена шаблона."""
    clear_shot_templates_cache()
    action = (
        "1. кабинет следствия — раскладывают дело\n(один)\n"
        "2. кабинет следствия — листают протоколы\n(два)\n"
    )
    assigned = assign_templates_for_action(action)
    assert assigned == {1: "T5", 2: "T5"}
    plan = plan_templates_for_action(action)
    assert plan[1]["same_place"] is True
    assert "без K0" in plan[1]["compression"]


def test_select_specific_before_t3() -> None:
    """T3 = «только смена места»: содержательные сцены не должны падать в T3."""
    clear_shot_templates_cache()

    def one(head: str, vo: str = "(текст.)") -> str:
        return select_template_when(
            {"blob": f"{head} {vo}", "place": head.split(" — ")[0]}, ""
        )

    assert one("архив — вошёл между стеллажами", "(он достал папку.)") == "T2"
    assert one("двор — смотрит в толпу", "(он увидел его у ворот.)") == "T4"
    assert one("кухня — стирает следы со стола") == "T5"
    assert one("зал суда — слушает приговор") == "T7"
    assert one("кухня — сидит у окна", "(он сидел и ждал.)") == "T9"
    assert one("кабинет — закрывает папку и уходит", "(он ушёл.)") == "T10"
    assert one("отделение — фасад и дверь", "(он зашёл внутрь.)") == "T3"
    assert one("титр — Сергей Ткач", "(Сергей Ткач.)") == "T0"


def test_coverage_template_reason_lengthen() -> None:
    shots = [
        {
            "сцена": 1,
            "шаблон": "T0",
            "план": "СРЕДНИЙ",
            "место": "титр",
        },
        {
            "сцена": 1,
            "шаблон": "T0",
            "план": "ОБЩИЙ",
            "место": "титр",
        },
    ]
    reason = coverage_template_reason(shots, "aa" * 4)
    assert reason and "удлинили" in reason


def test_coverage_template_reason_two_templates_in_scene() -> None:
    shots = [
        {"сцена": 1, "шаблон": "T2", "план": "ОБЩИЙ", "место": "архив"},
        {"сцена": 1, "шаблон": "T3", "план": "СРЕДНИЙ", "место": "архив"},
    ]
    reason = coverage_template_reason(shots)
    assert reason and "два шаблона" in reason


def test_coverage_template_reason_t8_same_place_twice() -> None:
    shots = [
        {"сцена": 1, "шаблон": "T8", "план": "ОБЩИЙ", "место": "двор"},
        {"сцена": 1, "шаблон": "T8", "план": "ОБЩИЙ", "место": "двор"},
    ]
    reason = coverage_template_reason(shots)
    assert reason and "T8" in reason


def test_coverage_template_reason_repeat_t_allowed() -> None:
    """Один T* на разных местах соседних сцен — не брак (T8>T8>T8, T3>T3)."""
    shots = [
        {"сцена": 1, "шаблон": "T8", "план": "ОБЩИЙ", "место": "двор"},
        {"сцена": 2, "шаблон": "T8", "план": "ОБЩИЙ", "место": "плац"},
        {"сцена": 3, "шаблон": "T8", "план": "ОБЩИЙ", "место": "отделение"},
    ]
    assert coverage_template_reason(shots) is None
    shots_t3 = [
        {"сцена": 1, "шаблон": "T3", "план": "ОБЩИЙ", "место": "двор"},
        {"сцена": 2, "шаблон": "T3", "план": "ОБЩИЙ", "место": "отделение"},
    ]
    assert coverage_template_reason(shots_t3) is None


def test_load_shot_templates_has_t1_and_select() -> None:
    clear_shot_templates_cache()
    data = load_shot_templates()
    ids = {t["id"] for t in data["templates"]}
    assert "T0" in ids and "T1" in ids and "T2" in ids and "X1" in ids
    assert data["select"][0]["then_template"] == "T1"
    # У каждого шага выбора — полный вопрос из листа «Выбор».
    assert all(r.get("question") for r in data["select"])
    shots = [s for s in data["shots"] if s["template_id"] == "T2"]
    assert any(s.get("required") == 1 for s in shots)
    # У каждого шаблона — эталон и варианты сжатия.
    assert all(t.get("example") for t in data["templates"])
    assert all(t.get("compression") for t in data["templates"])


def test_catalog_mentions_questions_examples_compression() -> None:
    clear_shot_templates_cache()
    text = format_shot_templates_catalog()
    assert "ШАБЛОНЫ СЦЕНА→КАДРЫ" in text
    assert "ВЫБОР" in text
    assert "Двое говорят" in text  # вопрос дерева, не машинный ключ
    assert "эталон" in text
    assert "T1-c2" in text  # именованный вариант сжатия
    assert "keep_sides" in text
    assert "T1|" in text
    assert "ЭТАЛОННАЯ ЦЕПЬ" in text
    assert "полици" in text  # пример с заявлением о краже


def test_format_when_assignments_police() -> None:
    clear_shot_templates_cache()
    frames = [
        SimpleNamespace(
            uuid="aa" * 8,
            number=1,
            attrs={"главное_действие": POLICE_ACTION},
        )
    ]
    text = format_when_assignments(frames)
    assert "ПРЕДВЫБОР" in text
    assert "сцена 4" in text and "T5" in text
    assert "сцена 1" in text and "T6" in text
    assert "сжатие" in text  # сцены 3–4 на том же месте → подсказки сжатия
    assert "select_fix" in text  # разрешение поправить предвыбор с пометкой


def test_neighbor_place_hints_prev() -> None:
    frames = [
        SimpleNamespace(
            uuid="aa" * 8,
            number=1,
            attrs={"главное_действие": "1. двор — мальчик бегает\n(текст)"},
        ),
        SimpleNamespace(
            uuid="bb" * 8,
            number=2,
            attrs={"главное_действие": "1. двор — раскладывает вещи\n(текст)"},
        ),
        SimpleNamespace(
            uuid="cc" * 8,
            number=3,
            attrs={"главное_действие": "1. зал суда — приговор\n(текст)"},
        ),
    ]
    text = neighbor_place_hints(frames)
    assert "prev_place≈двор" in text
    assert "number=3" in text


def test_scenes_to_frames_resolves_v8_template() -> None:
    from app.services.prompt_library import (
        SCRIPT_FRAMES_QC_PROMPT_NAMES,
        list_excel_gpt_prompts,
        list_group_owned_prompts,
    )

    path = resolve_excel_gpt_prompt_path("scenes_to_frames_ru")
    assert "node_groups/script_frames_qc" in str(path).replace("\\", "/")
    text = path.read_text(encoding="utf-8")
    assert "v8 — шаблоны" in text or "шаблоны T/X" in text
    assert "drop_order" in text
    listed = list_excel_gpt_prompts()
    for name in SCRIPT_FRAMES_QC_PROMPT_NAMES:
        assert name not in listed
    grouped = list_group_owned_prompts("script_frames_qc")
    assert grouped is not None
    assert "scenes_to_frames_ru" in grouped
    assert list_group_owned_prompts("scene_design_fanout") is None


def test_write_group_prompt_stays_out_of_excel_gpt(
    tmp_path, monkeypatch
) -> None:
    from app.services import prompt_library as pl

    group_dir = tmp_path / "templates" / "node_groups" / "script_frames_qc"
    group_dir.mkdir(parents=True)
    excel_dir = tmp_path / "prompts" / "05_excel_gpt"
    excel_dir.mkdir(parents=True)
    monkeypatch.setattr("app.project_root.find_project_root", lambda: tmp_path)
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path / "prompts")

    pl.write_prompt("excel_gpt", "scenes_to_frames_ru", "GROUP BODY\n")
    group = group_dir / "scenes_to_frames_ru.md"
    common = excel_dir / "scenes_to_frames_ru.md"
    assert group.read_text(encoding="utf-8") == "GROUP BODY\n"
    assert not common.exists()
    assert "scenes_to_frames_ru" not in pl.list_excel_gpt_prompts()
    assert pl.resolve_excel_gpt_prompt_path("scenes_to_frames_ru") == group
