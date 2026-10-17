"""Нода «Стиль» (n_excel_gpt_fw_style) группы script_frames_qc."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import frame_style as fs

STYLE = (
    "STYLE: test watercolour wash, ink contour.\n\n"
    "Final style lock: unified watercolour frame.\n\n"
    "Negative: photorealism, text."
)
STYLE2 = "STYLE: knitted felt.\n\nNegative: plastic."


def _row(number, kadry, *, role="vo_parent", image_prompt="", uuid=None):
    from app.models import FrameStatus

    return SimpleNamespace(
        number=number,
        uuid=uuid or f"u{number:03d}",
        image_prompt=image_prompt,
        status=FrameStatus.planned,
        attrs={"camera_subdivide": {"role": role}, "кадры": kadry},
    )


@pytest.fixture()
def style_dir(tmp_path, monkeypatch):
    d = tmp_path / "styles"
    d.mkdir()
    (d / "style_test.md").write_text(STYLE + "\n", encoding="utf-8")
    (d / "style_two.md").write_text(STYLE2 + "\n", encoding="utf-8")
    (d / "style_huge.md").write_text("STYLE: " + "x" * 4800, encoding="utf-8")
    (d / "not_a_style.md").write_text("nope", encoding="utf-8")
    monkeypatch.setattr(fs, "styles_dir", lambda: d)
    return d


def test_limit_is_outsee_body():
    assert fs.body_limit() == 4877
    assert fs.max_style_chars() == 4677


def test_list_and_load(style_dir):
    assert fs.list_style_names() == ["style_huge", "style_test", "style_two"]
    assert fs.load_style_text("style_test.md") == STYLE
    assert fs.style_path("not_a_style") is None
    with pytest.raises(fs.StyleError):
        fs.load_style_text("style_huge")
    with pytest.raises(fs.StyleError):
        fs.load_style_text("style_missing")


def test_compose_no_trim():
    final, scene, trimmed = fs.compose_styled_prompt("Сцена. Ткач стоит.", STYLE)
    assert final == "Сцена. Ткач стоит.\n\n" + STYLE
    assert scene == "Сцена. Ткач стоит." and not trimmed


def test_trim_scene_at_sentence_never_style():
    scene = " ".join(f"Предложение номер {i} про кабинет и стол." for i in range(300))
    final, cut, trimmed = fs.compose_styled_prompt(scene, STYLE, limit=1000)
    assert trimmed
    assert len(final) <= 1000
    assert final.endswith(STYLE)
    assert cut.endswith(".")
    assert scene.startswith(cut)


def test_trim_without_sentence_end_cuts_on_space():
    scene = "слово " * 500
    final, cut, trimmed = fs.compose_styled_prompt(scene, STYLE, limit=600)
    assert trimmed and len(final) <= 600 and final.endswith(STYLE)
    assert not cut.endswith(" ")


def test_style_leaves_too_little_for_scene():
    with pytest.raises(fs.StyleError):
        fs.compose_styled_prompt("сцена", "S" * 900, limit=1000)


def test_apply_main_rows_only(style_dir):
    parent = _row(
        1,
        [
            {"id": "1-S1-K1", "parent_id": None, "промт_картинки": "Камера: 35 мм. Ткач у стола."},
            {"id": "1-S1-K2", "parent_id": "1-S1-K1", "промт_старт": "Дочерний старт."},
        ],
    )
    child = _row(2, [{"id": "1-S1-K2", "parent_id": "1-S1-K1", "промт_старт": "Дочерний старт."}], role="shot")
    empty = _row(3, [{"id": "3-S1-K1", "parent_id": None, "действие": "без промта"}])
    start_only = _row(4, [{"id": "4-S1-K1", "parent_id": None, "промт_старт": "Старт главного."}])
    rep = fs.apply_style_to_frames(
        [parent, child, empty, start_only], style_name="style_test", style_text=STYLE
    )
    c = rep["counts"]
    assert c["main_rows"] == 3 and c["skipped_shot_rows"] == 1
    assert c["styled"] == 2 and c["no_prompt"] == 1 and c["trimmed"] == 0
    assert c["source_промт_картинки"] == 1 and c["source_промт_старт"] == 1
    k1 = parent.attrs["кадры"][0]
    assert k1["промт_картинки"] == "Камера: 35 мм. Ткач у стола.\n\n" + STYLE
    assert k1["промт_без_стиля"] == "Камера: 35 мм. Ткач у стола."
    assert k1["стиль"] == "style_test"
    assert parent.image_prompt == k1["промт_картинки"]
    assert parent.attrs["кадры"][1] == {"id": "1-S1-K2", "parent_id": "1-S1-K1", "промт_старт": "Дочерний старт."}
    assert parent.status.value == "image_prompt_ready"
    # дочерний и пустой не тронуты
    assert child.image_prompt == "" and "стиль" not in child.attrs["кадры"][0]
    assert empty.image_prompt == "" and "стиль" not in empty.attrs["кадры"][0]
    assert rep["no_prompt"] == [{"number": 3, "uuid": "u003", "k1": "3-S1-K1"}]
    # промт_старт главного — стиль пишется в то же поле
    s1 = start_only.attrs["кадры"][0]
    assert s1["промт_старт"].endswith(STYLE) and "промт_картинки" not in s1
    assert s1["стиль_поле"] == "промт_старт"
    assert start_only.image_prompt == s1["промт_старт"]


def test_idempotent_and_style_switch(style_dir):
    row = _row(1, [{"id": "1-S1-K1", "parent_id": None, "промт_картинки": "Сцена кабинета."}])
    fs.apply_style_to_frames([row], style_name="style_test", style_text=STYLE)
    first = row.image_prompt
    rep2 = fs.apply_style_to_frames([row], style_name="style_test", style_text=STYLE)
    assert row.image_prompt == first
    assert rep2["counts"]["unchanged"] == 1 and rep2["counts"]["styled"] == 0
    assert first.count("STYLE:") == 1
    rep3 = fs.apply_style_to_frames([row], style_name="style_two", style_text=STYLE2)
    assert rep3["counts"]["styled"] == 1
    assert row.image_prompt == "Сцена кабинета.\n\n" + STYLE2
    assert "watercolour" not in row.image_prompt
    assert row.attrs["кадры"][0]["стиль"] == "style_two"


def test_existing_style_tail_stripped(style_dir):
    row = _row(
        1,
        [{"id": "1-S1-K1", "parent_id": None, "промт_картинки": "Сцена.\n\nSTYLE: old noir.\nNegative: x."}],
    )
    fs.apply_style_to_frames([row], style_name="style_test", style_text=STYLE)
    assert row.image_prompt == "Сцена.\n\n" + STYLE
    assert row.attrs["кадры"][0]["промт_без_стиля"] == "Сцена."


def test_trimmed_listed_in_report(style_dir):
    long_scene = " ".join(f"Фраза {i} о плаце." for i in range(400))
    row = _row(1, [{"id": "1-S1-K1", "parent_id": None, "промт_картинки": long_scene}])
    rep = fs.apply_style_to_frames([row], style_name="style_test", style_text=STYLE, limit=900)
    assert rep["counts"]["trimmed"] == 1 and rep["trimmed"][0]["number"] == 1
    assert len(row.image_prompt) <= 900 and row.image_prompt.endswith(STYLE)
    # исходная сцена хранится целиком
    assert row.attrs["кадры"][0]["промт_без_стиля"] == long_scene


def test_repo_style_files_fit_limit():
    names = fs.list_style_names()
    for want in (
        "style_noir_watercolour_16_09",
        "style_archival_noir_watercolor_aug",
        "style_knitted_textile",
    ):
        assert want in names
        text = fs.load_style_text(want)
        assert text.startswith("STYLE:")
        assert len(text) <= fs.max_style_chars()
    assert fs.DEFAULT_STYLE in names


def test_prompt_library_lists_and_resolves_styles():
    from app.services import prompt_library as pl

    assert "style_noir_watercolour_16_09" in pl.list_excel_gpt_prompts()
    assert "style_noir_watercolour_16_09" in (pl.list_group_owned_prompts("script_frames_qc") or [])
    assert pl.is_script_frames_qc_prompt("style_knitted_textile")
    assert pl.excel_gpt_prompt_exists("style_archival_noir_watercolor_aug")
    p = pl.resolve_excel_gpt_prompt_path("style_noir_watercolour_16_09")
    assert p.parent.name == "styles" and p.parent.parent.name == "script_frames_qc"
    assert pl.script_frames_qc_prompt_relpath("style_knitted_textile") == (
        "templates/node_groups/script_frames_qc/styles/style_knitted_textile.md"
    )
    meta = {"prompt_slot_variants": {"n_excel_gpt_fw_style": {"main": "style_knitted_textile"}}}
    name, src = pl.resolve_project_prompt_with_source(
        {}, "excel_gpt", meta=meta, node_key="n_excel_gpt_fw_style", slot_id="main"
    )
    assert (name, src) == ("style_knitted_textile", "slot")


def _qc_canvas():
    def node(nid, x, label):
        return {
            "id": nid,
            "type": "excel_gpt",
            "position": {"x": x, "y": 100.0},
            "data": {"label": label, "groupId": "script_frames_qc", "groupTitle": "G", "slotOverflow": True},
        }

    def edge(a, b):
        return {"id": f"e_{a}_{b}", "source": a, "target": b, "sourceHandle": "out",
                "targetHandle": "in", "data": {"kind": "after"}}

    nodes = [
        node("n_excel_gpt_fw_shots", 1000.0, "Шоты"),
        node("n_excel_gpt_fw_qc", 1290.0, "QC"),
        node("n_excel_gpt_fw_report", 1580.0, "Отчёт"),
        {"id": "n_hero", "type": "hero", "position": {"x": 1870.0, "y": 100.0}, "data": {"label": "Персонажи"}},
    ]
    edges = [
        edge("n_excel_gpt_fw_shots", "n_excel_gpt_fw_qc"),
        edge("n_excel_gpt_fw_qc", "n_excel_gpt_fw_report"),
        edge("n_excel_gpt_fw_report", "n_hero"),
    ]
    return {"canvas_graph": {"workflow_id": 7, "nodes": nodes, "edges": edges}}


def test_insert_style_graph_between_qc_and_report():
    from app.services import node_groups as ng

    meta = _qc_canvas()
    assert ng.script_frames_qc_needs_upgrade(meta)
    assert ng.insert_script_frames_qc_style_graph(meta) is True
    cg = meta["canvas_graph"]
    by_id = {n["id"]: n for n in cg["nodes"]}
    assert by_id["n_excel_gpt_fw_style"]["position"] == {"x": 1580.0, "y": 100.0}
    assert by_id["n_excel_gpt_fw_style"]["data"]["label"] == "Стиль"
    assert by_id["n_excel_gpt_fw_report"]["position"]["x"] == 1870.0
    assert by_id["n_hero"]["position"]["x"] == 1870.0  # остальные не двигаем
    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert ("n_excel_gpt_fw_qc", "n_excel_gpt_fw_report") not in pairs
    assert ("n_excel_gpt_fw_qc", "n_excel_gpt_fw_style") in pairs
    assert ("n_excel_gpt_fw_style", "n_excel_gpt_fw_report") in pairs
    assert ("n_excel_gpt_fw_report", "n_hero") in pairs
    assert len(cg["edges"]) == 4
    assert meta["prompt_slot_variants"]["n_excel_gpt_fw_style"] == {"main": "style_noir_watercolour_16_09"}
    assert meta["excel_gpt_nodes"]["n_excel_gpt_fw_style"]["transport"] == "code"
    assert ng.insert_script_frames_qc_style_graph(meta) is False
    assert not ng.script_frames_qc_needs_upgrade(meta)
    # код-нода: не GPT-шаг группы (без TEMP kie)
    assert not ng.is_script_frames_qc_gpt_node(meta, node_key="n_excel_gpt_fw_style")
    assert ng.is_script_frames_qc_gpt_node(meta, node_key="n_excel_gpt_fw_qc")


def test_group_def_has_style_between_qc_and_report():
    from app.services import node_groups as ng

    g = ng.NODE_GROUPS["script_frames_qc"]
    keys = [n.local_key for n in g.nodes]
    assert keys.index("qc") < keys.index("style") < keys.index("report")
    assert ("qc", "style", "after") in g.internal_edges
    assert ("style", "report", "after") in g.internal_edges
    assert ("qc", "report", "after") not in g.internal_edges
    spec = next(n for n in g.nodes if n.local_key == "style")
    assert spec.preferred_id == "n_excel_gpt_fw_style"
    assert spec.prompt_variant == fs.DEFAULT_STYLE
    assert spec.operator_config == {"outputMode": "artifact", "transport": "code"}


def test_footer_kind_style():
    from app.orchestrator.steps import enrich_xlsx as ex

    assert ex._script_frames_qc_footer_kind(None, None, "n_excel_gpt_fw_style") == "style"
    assert not ex._is_scenes_to_frames_node(None, None, "n_excel_gpt_fw_style")
