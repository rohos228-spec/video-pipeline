from __future__ import annotations

from app.services.action_layer import planner, planner_rules

FACTS = {"project_id": 2, "defaults": {}, "publish_server": {"caption": "тема", "platforms": ["tiktok"]}}


def test_system_prompt_tells_not_to_ask_optional_or_server_filled_values() -> None:
    assert "14." in planner.SYSTEM
    for word in ("ceiling_credits", "подпись", "площадки", "опусти"):
        assert word in planner.SYSTEM


def test_notes_for_money_ceiling_and_publish_actions() -> None:
    notes = planner_rules.notes_for({"project.resume", "project.continue_pipeline", "publish.send"})
    assert "ceiling_credits" in notes["project.resume"] and "не спрашивай" in notes["project.resume"]
    assert "ceiling_credits" in notes["project.continue_pipeline"]
    assert "сервер" in notes["publish.send"]


def test_question_about_caption_or_platforms_is_answered_by_the_server() -> None:
    got = planner_rules.resolve_clarification("Какую подпись добавить к публикации?", "опубликуй видео", FACTS)
    assert [r["param"] for r in got] == ["caption"]
    both = planner_rules.resolve_clarification(
        "На какие площадки опубликовать и какую подпись указать?", "опубликуй видео", FACTS)
    assert {r["param"] for r in both} == {"caption", "platforms"}
    assert all(r["value"] == "server-side" for r in both)


def test_publish_question_is_not_resolved_without_server_data_or_for_deletion() -> None:
    bare = {"project_id": 2, "defaults": {}}
    assert planner_rules.resolve_clarification("Какую подпись добавить?", "опубликуй", bare) == []
    assert planner_rules.resolve_clarification("Удалить подпись и площадки?", "опубликуй", FACTS) == []


def test_money_questions_still_go_to_the_human() -> None:
    assert planner_rules.resolve_clarification(
        "Какой потолок в кредитах поставить?", "продолжи конвейер", FACTS) == []
