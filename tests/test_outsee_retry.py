"""Тесты GPT-сжатия / rewrite в outsee_retry."""

from __future__ import annotations

import pytest

from app.bots.outsee import OutseeContentRejectedError, OutseeImageError
from app.generation_options import OUTSEE_PROMPT_MAX_CHARS
from app.services import outsee_retry as mod


def test_is_prompt_related_error_truncation() -> None:
    err = OutseeImageError(
        "outsee: промт обрезан outsee (3200 из 4800 симв)",
        context={"actual_len": 3200, "expected_len": 4800},
    )
    assert mod._is_prompt_related_error(err) is True


def test_is_prompt_related_error_moderation_false() -> None:
    err = OutseeContentRejectedError(
        "outsee image: контент отклонён модерацией",
        context={"kind": "moderation"},
    )
    assert mod._is_prompt_related_error(err) is False


def test_target_body_from_truncation_error() -> None:
    err = OutseeImageError(
        "outsee: промт обрезан outsee (3100 из 4500 симв)",
        context={"actual_len": 3100, "expected_len": 4500},
    )
    prefix = "[ID: P12-F3-a7f2b01c r2a1]"
    target = mod._target_body_chars_from_error(err, prefix)
    assert target is not None
    assert target < OUTSEE_PROMPT_MAX_CHARS - mod._prefix_reserve(prefix)


def test_prefix_reserve_includes_uniquify_suffix() -> None:
    base = "[ID: P12-F3-a7f2b01c]"
    assert mod._prefix_reserve(base) < mod._prefix_reserve(f"{base[:-1]} r9a9]")


@pytest.mark.asyncio
async def test_prepare_prompt_compresses_when_over_limit(monkeypatch) -> None:
    calls: list[str] = []

    class FakeGpt:
        async def ask_fresh(self, ask: str, *, timeout: float = 300, project_id=None) -> str:
            calls.append(ask)
            return "x" * 4000

    body = "y" * 5100
    prefix = "[ID: P1-F1-abc]"
    out = await mod._prepare_prompt_for_outsee(
        FakeGpt(), body, prefix, project_id=1
    )
    assert len(out) == 4000
    assert calls


@pytest.mark.asyncio
async def test_generate_image_rewrite_after_moderation_stops_duplicate_retries(
    monkeypatch,
) -> None:
    """Модерация: GPT-rewrite после первой ошибки; без нового текста — не 3× тот же промт."""
    attempts: list[str] = []
    rewrite_calls: list[str] = []

    class FakeOutsee:
        async def generate_image(self, prompt: str, out_path, **kwargs):
            attempts.append(prompt)
            raise OutseeContentRejectedError(
                "outsee image: контент отклонён модерацией",
                context={"kind": "moderation"},
            )

    class FakeGpt:
        async def ask_fresh(self, ask: str, *, timeout: float = 300, project_id=None) -> str:
            if "триггерные слова" in ask or "сохрани смысл картины" in ask:
                rewrite_calls.append(ask)
                if len(rewrite_calls) == 1:
                    return "rewritten prompt without triggers " * 3
                return "rewritten prompt without triggers " * 3
            return ask

    async def fake_prepare(gpt, body, prefix, *, project_id=None):
        return body

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", fake_prepare)

    with pytest.raises(OutseeContentRejectedError):
        await mod.generate_image_with_retries(
            FakeOutsee(),
            FakeGpt(),
            prompt="original bad prompt " * 20,
            out_path=__import__("pathlib").Path("out.png"),
            max_attempts_per_prompt=3,
            gpt_rewrite=True,
            project_id=1,
        )

    # 2× original (второй без лишних retry) + 1× rewritten
    assert len(attempts) == 3
    assert rewrite_calls
