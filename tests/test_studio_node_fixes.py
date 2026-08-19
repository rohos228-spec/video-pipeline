"""Unit tests for Studio node runners and plan extraction."""

from __future__ import annotations

import pytest
from app.services.xlsx_step_runners import extract_general_plan_from_gpt_reply


def test_extract_general_plan_from_apply_ops() -> None:
    reply = '{"ops":[{"target":"project","fields":{"общий_план":"План: 1. Введение 2. Развитие 3. Финал"}}]}'
    plan = extract_general_plan_from_gpt_reply(reply)
    assert plan == "План: 1. Введение 2. Развитие 3. Финал"


def test_extract_general_plan_rejects_empty_or_non_plan() -> None:
    assert extract_general_plan_from_gpt_reply("") == ""
    assert extract_general_plan_from_gpt_reply("   ") == ""
    assert extract_general_plan_from_gpt_reply('{"ops":[{"frame_uuid":"x","fields":{"закадр":"hi"}}]}') == ""