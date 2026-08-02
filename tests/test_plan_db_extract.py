"""Plan step: extract общий_план from apply-ops (DB-first, no xlsx download)."""

from __future__ import annotations

from app.services.plan_validation import is_meaningful_general_plan
from app.services.xlsx_step_runners import extract_general_plan_from_gpt_reply


def test_extract_general_plan_from_apply_ops_json() -> None:
    body = "A" * 220
    reply = (
        'Вот план:\n'
        '{"ops":[{"target":"project","fields":{"общий_план":"' + body + '"}}]}'
    )
    got = extract_general_plan_from_gpt_reply(reply)
    assert got == body
    assert is_meaningful_general_plan(got)


def test_extract_general_plan_missing_returns_empty() -> None:
    assert extract_general_plan_from_gpt_reply('{"ops":[{"frame_uuid":"x","fields":{"закадр":"hi"}}]}') == ""
    assert extract_general_plan_from_gpt_reply("просто текст без json") == ""
