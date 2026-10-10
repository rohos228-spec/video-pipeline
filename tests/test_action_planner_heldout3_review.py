"""Общие исправления планировщика по разбору отложенного набора №3 (офлайн, без модели).

Классы ошибок: (A) нужное действие убрано защитой из-за разговорного глагола или оборота без глагола;
(B) модель взяла не то действие или добавила лишнее парное; (C) значение, названное словами, модель
опустила («опусти необязательное»): площадки, предел операций, режим героя; (D) модель выдумала значения
настроек вместо вопроса; (E) очистка всей очереди без слов о всей очереди. Фразы здесь свои, не из наборов.
"""
from __future__ import annotations

import json

import pytest

from app.services.action_layer import planner, planner_rules as rules
from app.services.action_layer.spec import ensure_builtin_actions, registry

PUBLISH_ALL = ["tiktok", "yt_shorts", "ig_reels", "vk_clips", "likee"]
FACTS = {"project_id": 2, "frames": [[3, 1203, "p", 1, "1", "1"]], "frame_uuids": {}, "defaults": {},
         "queue": {"image": 3, "video": 1, "ops": [[3, "image_regen", 1], [3, "image_ai_change", 1],
                                                   [4, "image_regen", 1], [8, "video_regen", 1]]},
         "publish_server": {"caption": "Тема проекта", "platforms": PUBLISH_ALL}}


def _reg():
    ensure_builtin_actions()
    return registry


def _reply(steps, clar=None, expl="ok") -> str:
    return json.dumps({"steps": steps, "needs_clarification": clar, "explanation_ru": expl})


def _step(aid: str, params: dict | None = None, deps: list[int] | None = None) -> dict:
    return {"action_id": aid, "params": params if params is not None else {"project_id": 2},
            "depends_on": deps or []}


def _run(steps: list[dict], text: str):
    return planner.validate_reply(_reply(steps), _reg(), max_steps=5, project_id=2, facts=FACTS,
                                  request_text=text)


def _kept(aid: str, text: str) -> bool:
    kept, dropped = rules.drop_unrequested(
        [{"action_id": aid, "params": {}, "depends_on": []}], text, _reg())
    return bool(kept) and not dropped


# --- (A) защита не убирает названное действие ----------------------------------------------------

@pytest.mark.parametrize("aid,text", [
    ("montage.upload_voice", "закачай мне озвучку диктора"),
    ("montage.upload_music", "закачай фоновый трек"),
    ("montage.upload_video", "вот ролик для кадра 5, в первый шот"),
    ("montage.upload_image", "вот эта картинка в кадр 9"),
    ("node.reset_from_step", "откати проект с шага split и всё, что после"),
    ("publish.send", "размести ролик в соцсетях"),
    ("publish.send", "выпусти готовое видео на площадки"),
    ("montage.apply_queue", "жми, применяем очередь"),
])
def test_colloquial_wording_keeps_the_action(aid: str, text: str) -> None:
    assert _kept(aid, text), (aid, text)


@pytest.mark.parametrize("aid,text", [
    ("montage.upload_image", "вот так сейчас выглядит доска"),
    ("montage.upload_video", "покажи кадр 5"),
    ("node.reset_from_step", "покажи статус нод"),
    ("publish.send", "покажи доску"),
])
def test_unrelated_wording_still_drops_the_action(aid: str, text: str) -> None:
    assert not _kept(aid, text), (aid, text)


# --- (B) лишнее парное действие ------------------------------------------------------------------

def test_auto_mode_request_does_not_continue_the_pipeline() -> None:
    steps = [_step("project.continue_pipeline"),
             _step("project.set_auto_mode", {"project_id": 2, "enabled": True}, [0])]
    out = _run(steps, "включи авторежим, пусть дальше едет само")
    assert [s["action_id"] for s in out.steps] == ["project.set_auto_mode"]
    assert out.steps[0]["depends_on"] == []
    both = _run(steps, "продолжи конвейер и включи автопродвижение")
    assert [s["action_id"] for s in both.steps] == ["project.continue_pipeline", "project.set_auto_mode"]


def test_run_step_without_run_words_but_with_resolution_becomes_a_setting() -> None:
    reg = _reg()
    swap = rules.pick_similar_action("step.run_step", {"project_id": 2, "step_code": "video"},
                                     "видео делай в 1080p", FACTS, None, reg)
    assert swap and swap[0] == "project.patch_settings"
    assert swap[1] == {"video_resolution": "1080p", "project_id": 2}
    img = rules.pick_similar_action("step.run_step", {"project_id": 2, "step_code": "img"},
                                    "кадры рисуй в 4к", FACTS, None, reg)
    assert img and img[1] == {"image_resolution": "4k", "project_id": 2}
    for text in ("запусти видео в 1080p", "сгенерируй картинки в 4k сейчас", "видео сделай"):
        assert rules.pick_similar_action("step.run_step", {"project_id": 2, "step_code": "video"},
                                         text, FACTS, None, reg) is None, text
    paid = {"project_id": 2, "step_code": "img", "count": 5}  # настоящий запуск с числом генераций
    assert rules.pick_similar_action("step.run_step", paid, "кадры в 4k", FACTS, None, reg) is None
    assert rules.pick_similar_action("step.run_step", {"project_id": 2, "step_code": "video"},
                                     "видео делай в 1080p", FACTS, {"step.run_step"}, reg) is None


# --- (C) значения, названные словами -------------------------------------------------------------

def test_named_platforms() -> None:
    assert rules.named_platforms("залей в тикток и инстаграм") == ["tiktok", "ig_reels"]
    assert rules.named_platforms("выложи ролик во вконтакте") == ["vk_clips"]
    assert rules.named_platforms("на ютубе опубликуй") == ["yt_shorts"]
    assert rules.named_platforms("опубликуй на все платформы") == []
    assert rules.named_platforms("на все площадки кроме вк") == []


def test_publish_takes_the_platform_from_the_request() -> None:
    out = _run([_step("publish.send", {"project_id": 2})], "опубликуй ролик во вконтакте")
    assert out.steps[0]["params"]["platforms"] == ["vk_clips"]
    allp = _run([_step("publish.send", {"project_id": 2})], "опубликуй ролик везде")
    assert allp.steps[0]["params"]["platforms"] == PUBLISH_ALL
    fixed = _run([_step("publish.send", {"project_id": 2, "platforms": PUBLISH_ALL})], "выложи ролик в тикток")
    assert fixed.steps[0]["params"]["platforms"] == ["tiktok"]


def test_named_ops_limits() -> None:
    assert rules.named_ops_limits("примени очередь, максимум 4 видео и не больше двух картинок") == {
        "max_video_ops": 4, "max_image_ops": 2}
    assert rules.named_ops_limits("запусти очередь, не более трёх картиночных правок") == {"max_image_ops": 3}
    assert rules.named_ops_limits("примени очередь до кадра 8") == {}
    assert rules.named_ops_limits("выполни очередь") == {}


def test_apply_queue_takes_the_named_limit_instead_of_the_queue_size() -> None:
    out = _run([_step("montage.apply_queue")], "выполни очередь, но не больше двух картинок")
    params = out.steps[0]["params"]
    assert params["max_image_ops"] == 2 and params["max_video_ops"] == 1
    plain = _run([_step("montage.apply_queue")], "выполни очередь")
    assert plain.steps[0]["params"]["max_image_ops"] == 3


def test_hero_mode_from_words() -> None:
    assert rules.named_hero_mode("новый проект «Дача» с героем") == "hero"
    assert rules.named_hero_mode("создай проект без героя") == "no_hero"
    assert rules.named_hero_mode("создай проект «Дача»") == ""
    made = _run([_step("project.create", {"title": "Дача"})], "заведи проект «Дача» с главным героем")
    assert made.steps[0]["params"]["hero_mode"] == "hero"
    plain = _run([_step("project.create", {"title": "Дача"})], "заведи проект «Дача»")
    assert "hero_mode" not in plain.steps[0]["params"]


# --- (D) настройки: значения только из запроса ---------------------------------------------------

def test_settings_values_not_in_the_request_become_a_question() -> None:
    invented = {"project_id": 2, "image_generator": "nano_banana_2", "video_generator": "veo_3_fast"}
    out = _run([_step("project.patch_settings", invented)], "поменяй модель для картинок")
    assert not out.steps and out.needs_clarification and "генератор" in out.needs_clarification
    res = _run([_step("project.patch_settings", {"project_id": 2, "image_resolution": "2k",
                                                  "video_resolution": "1080p"})], "поправь качество")
    assert not res.steps and "разрешение" in res.needs_clarification
    assert res.clarification_options


def test_settings_values_named_in_the_request_are_kept() -> None:
    for text, params in (
            ("поставь картинкам разрешение 4к", {"image_resolution": "4k"}),
            ("видео сделай 720p", {"video_resolution": "720p"}),
            ("видео в full hd", {"video_resolution": "1080p"}),
            ("переключи картинки на nano banana 2", {"image_generator": "nano_banana_2"}),
            ("переключи картинки на нано банана", {"image_generator": "nano_banana_2"}),
            ("видео пусть рисует veo", {"video_generator": "veo_3_fast"})):
        out = _run([_step("project.patch_settings", {"project_id": 2, **params})], text)
        assert out.steps and {k: out.steps[0]["params"][k] for k in params} == params, text


def test_settings_keep_named_values_and_drop_invented_ones() -> None:
    steps = [_step("project.patch_settings", {"project_id": 2, "image_resolution": "3k",
                                              "video_resolution": "1080p"})]
    out = _run(steps, "картинки хочу в 3k")
    assert out.steps[0]["params"] == {"project_id": 2, "image_resolution": "3k"}
    assert "video_resolution" in out.explanation_ru


def test_settings_without_generator_or_resolution_are_untouched() -> None:
    out = _run([_step("project.patch_settings", {"project_id": 2, "title": "Тихая гавань"})],
               "назови проект «Тихая гавань»")
    assert out.steps and out.steps[0]["params"]["title"] == "Тихая гавань"


# --- (E) очистка всей очереди --------------------------------------------------------------------

def test_clearing_the_queue_needs_words_about_the_whole_queue() -> None:
    clear = _step("montage.cancel_queue_ops", {"project_id": 2, "clear_all": True})
    vague = _run([clear], "сними их")
    assert not vague.steps and vague.needs_clarification and "очеред" in vague.needs_clarification
    assert vague.clarification_options
    for text in ("очисти очередь правок", "убери всю очередь", "сбрось очередь целиком"):
        assert _run([clear], text).steps, text
    ops = _step("montage.cancel_queue_ops", {"project_id": 2, "ops": [{"type": "image_regen", "frame_number": 3}]})
    assert _run([ops], "убери правку кадра 3 из очереди").steps


# --- подсказки модели ----------------------------------------------------------------------------

def test_notes_cover_the_new_classes() -> None:
    notes = rules.ACTION_NOTES
    assert "yt_shorts" in notes["publish.send"] and "только названные" in notes["publish.send"]
    assert "max_*_ops" in notes["montage.apply_queue"] and "предел" in notes["montage.apply_queue"]
    assert "no_hero" in notes["project.create"]
    assert "continue_pipeline" in notes["project.set_auto_mode"]
    assert "project.patch_settings" in notes["step.run_step"]


# --- словарь защиты и каталога: слэнг, опечатки, указание без глагола (разбор офлайн-прогона набора №4) ---

@pytest.mark.parametrize("aid,text", [
    ("hitl.approve", "апрувь запрос 11"),
    ("hitl.reject", "зарежь заявку 3"),
    ("montage.generate_scene_images", "накидай картинок для кадра 5"),
    ("montage.generate_scene", "перепиши сцену кадра 2 по моему заказу"),
    ("montage.upload_music", "вот трек, пусть играет на фоне"),
    ("montage.upload_voice", "вот эта озвучка проекта"),
    ("montage.delete_frame", "удоли кадр 4"),
])
def test_slang_typos_and_bare_deictic_uploads_keep_the_action(aid: str, text: str) -> None:
    assert _kept(aid, text), (aid, text)


@pytest.mark.parametrize("aid,text", [
    ("montage.delete_frame", "покажи кадр 4"),
    ("montage.upload_music", "вот так выглядит доска"),
    ("montage.run_montage", "покажи доску"),
    ("hitl.approve", "покажи ожидающие запросы"),
])
def test_typo_tolerance_does_not_keep_unrelated_actions(aid: str, text: str) -> None:
    assert not _kept(aid, text), (aid, text)


def test_catalog_offers_actions_for_bare_imperatives_and_aspect_words() -> None:
    from app.services.action_layer import planner_catalog
    reg = _reg()
    facts = {"project_id": 2, "status": "idle", "frame_count": 10, "max_frame_number": 10, "nodes": []}
    assert "montage.run_montage" in planner_catalog.select_catalog("монтируй", facts, reg, 24).offered
    assert "project.patch_settings" in planner_catalog.select_catalog(
        "соотношение сторон 4:3", facts, reg, 24).offered
