"""img_pr_style: STYLE-lock из проекта, не hardcoded Archival Noir (T07).

Инцидент #14 (PSYCO, knitted): 110 промтов ушли в нуар-обёртке, потому что
wrap был безусловным. Теперь стиль — из meta проекта / явного аргумента;
не задан → сцена без обёртки + warning.
"""

from __future__ import annotations

from loguru import logger

from app.services import img_pr_style as ips


def _ops() -> list[dict]:
    return [
        {
            "frame_uuid": "aaa",
            "fields": {"промт_картинки": "Фон: вагон метро. Действие: читает газету."},
        }
    ]


def _collect_warnings(fn):
    records: list[str] = []
    sink_id = logger.add(lambda m: records.append(str(m.record["message"])), level="WARNING")
    try:
        result = fn()
    finally:
        logger.remove(sink_id)
    return result, records


def test_knitted_style_id_wraps_knitted_no_noir() -> None:
    out = ips.wrap_ops_styles(_ops(), style_id="knitted")
    body = out[0]["fields"]["промт_картинки"]
    assert "Knitted Wool Felt Textile" in body
    assert "yarn" in body
    assert "felt" in body.casefold()
    assert "Archival Noir" not in body
    # сцена сохранена между HEAD и TAIL
    assert "Фон: вагон метро. Действие: читает газету." in body
    assert "Final style lock" in body


def test_no_style_no_wrap_and_warning() -> None:
    out, records = _collect_warnings(lambda: ips.wrap_ops_styles(_ops()))
    body = out[0]["fields"]["промт_картинки"]
    assert body == "Фон: вагон метро. Действие: читает газету."
    assert "Archival Noir" not in body
    assert any("img_pr style not set — no wrap applied" in r for r in records)


def test_noir_explicit_keeps_legacy_text() -> None:
    out = ips.wrap_ops_styles(_ops(), style_id="noir")
    body = out[0]["fields"]["промт_картинки"]
    assert "Archival Noir Watercolor" in body
    assert "Final style lock" in body
    assert "Фон: вагон метро" in body


def test_explicit_style_block_wins_over_style_id() -> None:
    out = ips.wrap_ops_styles(
        _ops(), style_id="noir", style_block="STYLE: Custom Studio Block"
    )
    body = out[0]["fields"]["промт_картинки"]
    assert body.startswith("STYLE: Custom Studio Block")
    assert "Фон: вагон метро" in body
    assert "Archival Noir" not in body


def test_plasticine_style_id_skips_wrap_without_warning() -> None:
    # Пластилин: стиль пишет GPT внутри промта (×3) — пайплайн не вшивает.
    out, records = _collect_warnings(
        lambda: ips.wrap_ops_styles(_ops(), style_id="plasticine")
    )
    assert out[0]["fields"]["промт_картинки"] == (
        "Фон: вагон метро. Действие: читает газету."
    )
    assert not records


def test_unknown_style_id_no_wrap_and_warning() -> None:
    out, records = _collect_warnings(
        lambda: ips.wrap_ops_styles(_ops(), style_id="watercolor-2020")
    )
    body = out[0]["fields"]["промт_картинки"]
    assert "Archival Noir" not in body
    assert body == "Фон: вагон метро. Действие: читает газету."
    assert any("unknown" in r and "no wrap applied" in r for r in records)


def test_normalize_style_id_aliases() -> None:
    assert ips.normalize_style_id("noir") == "noir"
    assert ips.normalize_style_id("Archival Noir") == "noir"
    assert ips.normalize_style_id("нуар") == "noir"
    assert ips.normalize_style_id("knitted") == "knitted"
    assert ips.normalize_style_id("Вязаный") == "knitted"
    assert ips.normalize_style_id("войлок") == "knitted"
    assert ips.normalize_style_id("пластилин") == "plasticine"
    assert ips.normalize_style_id("claymation") == "plasticine"
    assert ips.normalize_style_id("") is None
    assert ips.normalize_style_id(None) is None
    assert ips.normalize_style_id("steampunk") is None


def test_resolve_project_style_id_meta_priority() -> None:
    assert ips.resolve_project_style_id({"img_pr_style_id": "knitted"}) == "knitted"
    # канонический ключ побеждает прочие style-ключи
    assert ips.resolve_project_style_id(
        {"img_pr_style_id": "knitted", "visual_style": "noir"}
    ) == "knitted"
    # fallback на конвенцию scene_design (image_style/visual_style/img_style/style)
    assert ips.resolve_project_style_id({"visual_style": "вязаный"}) == "knitted"
    assert ips.resolve_project_style_id({"img_style": "noir"}) == "noir"
    assert ips.resolve_project_style_id({"style": "пластилин"}) == "plasticine"
    assert ips.resolve_project_style_id({}) is None
    assert ips.resolve_project_style_id(None) is None
    assert ips.resolve_project_style_id({"img_pr_style_id": "unknown-x"}) is None
    assert ips.resolve_project_style_id({"img_pr_style_id": 42}) is None


def test_wrap_scene_with_style_defaults_no_noir() -> None:
    body, records = _collect_warnings(
        lambda: ips.wrap_scene_with_style("Фон: двор. Действие: бежит.")
    )
    assert body == "Фон: двор. Действие: бежит."
    assert any("img_pr style not set" in r for r in records)


def test_already_has_style_detects_knitted() -> None:
    wrapped = ips.wrap_ops_styles(_ops(), style_id="knitted")
    body = wrapped[0]["fields"]["промт_картинки"]
    # повторная обёртка не дублирует style-блок
    again = ips.wrap_ops_styles(wrapped, style_id="knitted")
    assert again[0]["fields"]["промт_картинки"] == body
