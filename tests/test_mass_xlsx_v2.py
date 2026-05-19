"""Тесты для новой 26-колоночной xlsx-схемы массовой генерации.

Покрывает:
  - app.storage.batch_sheet: init / write / read со всеми колонками,
    инжекция дефолтов в пустые ячейки, парсинг чисел и yes/no.
  - app.services.batches: _parse_hero_combo, _norm_label_to_id,
    _apply_xlsx_settings (правка kwargs/meta по xlsx-карточке),
    project_to_xlsx_row (обратное отображение Project → xlsx-словарь).
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("TELEGRAM_OWNER_CHAT_ID", "1")

from app.services.batches import (  # noqa: E402
    _IMG_GEN_BY_LABEL,
    _VIDEO_GEN_BY_LABEL,
    _apply_xlsx_settings,
    _norm_label_to_id,
    _parse_hero_combo,
)
from app.storage import batch_sheet as bs  # noqa: E402

# --------------------------------------------------------------------------- #
# _parse_hero_combo
# --------------------------------------------------------------------------- #


def test_parse_hero_combo_normal() -> None:
    assert _parse_hero_combo("0и1") == (0, [])
    assert _parse_hero_combo("1и3") == (1, [3])
    assert _parse_hero_combo("2и3") == (2, [3, 3])
    assert _parse_hero_combo("3и1") == (3, [1, 1, 1])
    assert _parse_hero_combo("4и5") == (4, [5, 5, 5, 5])


def test_parse_hero_combo_edge_cases() -> None:
    # Пустые / мусорные значения → (None, None)
    assert _parse_hero_combo(None) == (None, None)
    assert _parse_hero_combo("") == (None, None)
    assert _parse_hero_combo("   ") == (None, None)
    assert _parse_hero_combo("hello") == (None, None)
    assert _parse_hero_combo("aиb") == (None, None)


def test_parse_hero_combo_clamps_to_safe_range() -> None:
    # Очень большие числа клампятся до 9.
    assert _parse_hero_combo("99и99") == (9, [9] * 9)


# --------------------------------------------------------------------------- #
# _norm_label_to_id
# --------------------------------------------------------------------------- #


def test_norm_label_to_id_image_generators() -> None:
    assert _norm_label_to_id("Nano Banana Pro", _IMG_GEN_BY_LABEL) == "nano_banana_pro"
    assert _norm_label_to_id("Nano Banana 2", _IMG_GEN_BY_LABEL) == "nano_banana_2"
    # Регистронезависимый поиск.
    assert _norm_label_to_id("nano banana pro", _IMG_GEN_BY_LABEL) == "nano_banana_pro"
    # Незнакомый лейбл → None (snapshot остаётся).
    assert _norm_label_to_id("Unknown Gen", _IMG_GEN_BY_LABEL) is None
    # Пустые входы → None.
    assert _norm_label_to_id("", _IMG_GEN_BY_LABEL) is None
    assert _norm_label_to_id(None, _IMG_GEN_BY_LABEL) is None


def test_norm_label_to_id_video_generators() -> None:
    assert _norm_label_to_id("Veo 3.1 Lite", _VIDEO_GEN_BY_LABEL) == "veo_3_1_lite"


# --------------------------------------------------------------------------- #
# _apply_xlsx_settings
# --------------------------------------------------------------------------- #


def test_apply_xlsx_settings_full_row() -> None:
    """Все 26 колонок заданы — все поля Project должны быть выставлены."""
    card = {
        "scenario":          "plan_long_rolik",
        "title":             "Тест ролик",
        "script_style":      "promt_stiven_king",
        "anim_style":        "norm",
        "fact":              "интересный факт",
        "video_prompts_gen": "норм",
        "hero_combo":        "2и3",
        "hero_description":  "воин",
        "duration_sec":      "60",
        "image_generator":   "Nano Banana Pro",
        "image_quality":     "4K",
        "image_aspect":      "9:16",
        "image_relax":       False,
        "video_generator":   "Veo 3.1 Lite",
        "video_quality":     "1080",
        "video_aspect":      "9:16",
        "video_relax":       True,
    }
    kwargs: dict = {}
    meta: dict = {}
    _apply_xlsx_settings(card, kwargs, meta)

    # prompt_overrides из 4 колонок
    assert kwargs["prompt_overrides"] == {
        "plan":    "plan_long_rolik",
        "script":  "promt_stiven_king",
        "img_pr":  "norm",
        "anim_pr": "норм",
    }
    # hero_combo "2и3" → 2 героя, 3 варианта на каждого
    assert kwargs["hero_count"] == 2
    assert kwargs["hero_variations"] == [3, 3]
    assert kwargs["hero_mode"] == "hero"
    assert kwargs["hero_descriptions"] == ["воин", "воин"]
    # duration_sec → meta
    assert meta["duration_target_sec"] == 60
    # image
    assert kwargs["image_generator"]  == "nano_banana_pro"
    assert kwargs["image_resolution"] == "4k"
    assert kwargs["aspect_ratio"]     == "9:16"
    assert kwargs["image_relax"]      is False
    # video
    assert kwargs["video_generator"]  == "veo_3_1_lite"
    assert kwargs["video_resolution"] == "1080p"
    assert kwargs["video_relax"]      is True


def test_apply_xlsx_settings_no_hero() -> None:
    """hero_combo='0и1' → 0 героев, hero_mode='no_hero'."""
    card = {"hero_combo": "0и1"}
    kwargs: dict = {}
    meta: dict = {}
    _apply_xlsx_settings(card, kwargs, meta)
    assert kwargs["hero_count"] == 0
    assert kwargs["hero_variations"] == []
    assert kwargs["hero_mode"] == "no_hero"
    assert "hero_descriptions" not in kwargs


def test_apply_xlsx_settings_empty_row_doesnt_break_kwargs() -> None:
    """Полностью пустая карточка не должна ставить ничего ненужного."""
    kwargs: dict = {}
    meta: dict = {}
    _apply_xlsx_settings({}, kwargs, meta)
    assert kwargs == {}
    assert meta == {}


def test_apply_xlsx_settings_unknown_image_generator_ignored() -> None:
    """Неизвестный лейбл генератора → поле не выставляется (snapshot остаётся)."""
    card = {"image_generator": "Some Random Generator"}
    kwargs: dict = {"image_generator": "snapshot_default"}
    _apply_xlsx_settings(card, kwargs, {})
    # Старое значение из snapshot осталось.
    assert kwargs["image_generator"] == "snapshot_default"


# --------------------------------------------------------------------------- #
# batch_sheet: init / write / read round-trip
# --------------------------------------------------------------------------- #


def test_batch_sheet_init_and_read_empty(tmp_path: Path) -> None:
    """init_topics_xlsx создаёт файл, read_topics возвращает [] на пустых строках."""
    path = tmp_path / "topics.xlsx"
    bs.init_topics_xlsx(path, "Тестовый батч")
    assert path.exists()
    rows = bs.read_topics(path)
    assert rows == []


def test_batch_sheet_write_then_read_full_row(tmp_path: Path) -> None:
    """Записываем 1 полную строку → читаем обратно с теми же значениями."""
    path = tmp_path / "topics.xlsx"
    bs.init_topics_xlsx(path, "Batch")
    bs.write_subprojects_table(
        path,
        [
            {
                "scenario":          "plan_long_rolik",
                "title":             "Тест 1",
                "script_style":      "promt_stiven_king",
                "anim_style":        "norm",
                "hook_type":         "вопрос",
                "fact":              "факт",
                "integration":       "продукт",
                "video_prompts_gen": "норм",
                "hero_combo":        "1и3",
                "hero_description":  "воин",
                "duration_sec":      45,
                "image_generator":   "Nano Banana Pro",
                "image_quality":     "4K",
                "image_aspect":      "9:16",
                "image_relax":       "ДА",
                "video_generator":   "Veo 3.1 Lite",
                "video_quality":     "1080",
                "video_aspect":      "9:16",
                "video_relax":       "НЕТ",
                "voice":             "voice 1",
                "music":             "music 1",
                "slug":              "test-001",
                "status":            "new",
                "progress":          "",
            }
        ],
        "Batch",
    )
    rows = bs.read_topics(path)
    assert len(rows) == 1
    r = rows[0]

    # Промты и карточные поля
    assert r["scenario"]          == "plan_long_rolik"
    assert r["title"]             == "Тест 1"
    assert r["topic"]             == "Тест 1"
    assert r["script_style"]      == "promt_stiven_king"
    assert r["anim_style"]        == "norm"
    assert r["hook_type"]         == "вопрос"
    assert r["fact"]              == "факт"
    assert r["integration"]       == "продукт"
    assert r["video_prompts_gen"] == "норм"

    # Герои + длительность
    assert r["hero_combo"]       == "1и3"
    assert r["hero_description"] == "воин"
    assert r["duration_sec"]     == 45

    # Картинки
    assert r["image_generator"] == "Nano Banana Pro"
    assert r["image_quality"]   == "4K"
    assert r["image_aspect"]    == "9:16"
    assert r["image_relax"] is True   # "ДА" → bool True

    # Видео
    assert r["video_generator"] == "Veo 3.1 Lite"
    assert r["video_quality"]   == "1080"
    assert r["video_aspect"]    == "9:16"
    assert r["video_relax"] is False  # "НЕТ" → bool False

    # Сервисные
    assert r["slug"]   == "test-001"
    assert r["status"] == "new"


def test_batch_sheet_defaults_injected_on_empty_cells(tmp_path: Path) -> None:
    """Пустая строка → write должен подставить ROW_DEFAULTS."""
    path = tmp_path / "topics.xlsx"
    bs.init_topics_xlsx(path, "Batch")
    bs.write_subprojects_table(path, [{"title": "Тест без полей"}], "Batch")
    rows = bs.read_topics(path)
    assert len(rows) == 1
    r = rows[0]

    # Дефолтные промты
    assert r["scenario"]          == "default"
    assert r["script_style"]      == "default"
    assert r["anim_style"]        == "default"
    assert r["video_prompts_gen"] == "default"

    # Дефолтные настройки картинок/видео
    assert r["image_generator"] == "Nano Banana Pro"
    assert r["video_generator"] == "Veo 3.1 Lite"
    assert r["image_quality"]   == "2K"
    assert r["video_quality"]   == "1080"
    assert r["image_aspect"]    == "9:16"
    assert r["video_aspect"]    == "9:16"
    assert r["image_relax"] is False
    assert r["video_relax"] is False

    # Hero combo default = "0и1"
    assert r["hero_combo"]   == "0и1"
    # Длительность дефолт 30
    assert r["duration_sec"] == 30


def test_batch_sheet_collect_new_topics_skips_known_slugs(tmp_path: Path) -> None:
    """collect_new_topics возвращает только строки без привязки к существующим
    slug'ам (новые темы) — старые привязанные строки пропускаются.
    """
    path = tmp_path / "topics.xlsx"
    bs.init_topics_xlsx(path, "Batch")
    bs.write_subprojects_table(
        path,
        [
            {"title": "Новая 1"},                       # без slug → new
            {"title": "Уже привязана", "slug": "old-1"},  # с известным slug
            {"title": "Новая 2"},                       # без slug → new
        ],
        "Batch",
    )
    new = bs.collect_new_topics(path, known_slugs={"old-1"})
    titles = [t["title"] for t in new]
    assert "Новая 1" in titles
    assert "Новая 2" in titles
    assert "Уже привязана" not in titles
    assert len(new) == 2
