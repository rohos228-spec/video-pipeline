"""Unit-тесты на парсер ответов GPT в app.services.gpt_check.

Парсер свободного текста — критическое место (от него зависит правильное
решение во всех шагах пайплайна). Тестируем самостоятельно от ChatGPTBot,
без реальных API-вызовов.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.gpt_check import (
    GptCheckDecision,
    _extract_score,
    build_check_message,
    gpt_check_text_artifact,
    parse_gpt_response,
)

# ---------------------------------------------------------------------------
# parse_gpt_response: основные кейсы
# ---------------------------------------------------------------------------


class TestParseApproved:
    """Когда GPT отдаёт «одобрено» / «approved» — должен быть approved."""

    @pytest.mark.parametrize(
        "raw",
        [
            "одобрено",
            "Одобрено.",
            "ОДОБРЕНО",
            "  одобрено  ",
            "✅ Одобрено",
            "План одобрено.",
            "Approved",
            "approved by reviewer",
            "АПРРОВ! шучу, approve.",
            "Я считаю одобряю эту версию.",
            "Одобрен. score: 0.95",
        ],
    )
    def test_approved_variants(self, raw: str) -> None:
        decision, hint, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.approved, f"Failed for: {raw!r}"
        assert hint == ""


class TestParseRegenerate:
    """Когда GPT отдаёт «перегенерация» / «regenerate» — должен быть regenerate."""

    @pytest.mark.parametrize(
        "raw, expected_hint_contains",
        [
            ("Перегенерация: блок 3 слишком длинный.", "блок 3"),
            ("перегенерировать с акцентом на героя", "акцентом"),
            ("Regenerate: please make it shorter.", "shorter"),
            ("regen — нужно больше деталей", "больше деталей"),
            ("Перегенерируй полностью.", "полностью"),
            ("перегенерация", "перегенерация"),  # fallback: tail пуст → весь ответ
        ],
    )
    def test_regenerate_variants(self, raw: str, expected_hint_contains: str) -> None:
        decision, hint, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.regenerate, f"Failed for: {raw!r}"
        assert expected_hint_contains.lower() in hint.lower(), (
            f"Expected hint to contain {expected_hint_contains!r}, got: {hint!r}"
        )

    def test_regenerate_priority_over_approved(self) -> None:
        """Если в ответе есть и «перегенерировать», и «одобрено» — regenerate приоритетнее."""
        raw = "Перегенерируй так, чтобы можно было одобрить."
        decision, _, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.regenerate


class TestRegenerateWithReference:
    """Распознавание «перегенерация на основе референса: ...» (шаги 7 и 9)."""

    @pytest.mark.parametrize(
        "raw, expected_hint_contains",
        [
            (
                "Перегенерация на основе референса: сделай освещение мягче.",
                "освещение мягче",
            ),
            (
                "перегенерируй на основе референса — добавь больше деталей",
                "больше деталей",
            ),
            (
                "regenerate with reference: more dramatic lighting",
                "more dramatic lighting",
            ),
            (
                "Перегенерация с референсом: измени фон.",
                "измени фон",
            ),
        ],
    )
    def test_regenerate_with_reference_variants(
        self, raw: str, expected_hint_contains: str
    ) -> None:
        decision, hint, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.regenerate_with_reference, (
            f"Failed for: {raw!r}"
        )
        assert expected_hint_contains.lower() in hint.lower(), (
            f"Expected hint to contain {expected_hint_contains!r}, got: {hint!r}"
        )

    def test_plain_regenerate_does_not_match_reference(self) -> None:
        """Простое «перегенерация» (без слов про референс) — обычный regenerate."""
        raw = "Перегенерация: попробуй ещё раз."
        decision, _, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.regenerate
        # КЛЮЧЕВОЕ: НЕ regenerate_with_reference.

    def test_reference_far_from_token_does_not_trigger(self) -> None:
        """Если «референс» упоминается далеко от regen-токена — это просто regenerate."""
        # Слово «референс» в начале, regen в самом конце — 200+ символов разрыв.
        raw = (
            "Это был референс из исходного материала. "
            + "Длинное-длинное обоснование без всяких выводов. " * 5
            + "В итоге — перегенерация."
        )
        decision, _, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.regenerate


class TestParseEdgeCases:
    def test_empty_response(self) -> None:
        decision, hint, score = parse_gpt_response("")
        assert decision is GptCheckDecision.parse_error
        assert hint == "empty response"
        assert score is None

    def test_whitespace_only(self) -> None:
        decision, _, _ = parse_gpt_response("   \n\n  \t  ")
        assert decision is GptCheckDecision.parse_error

    def test_unknown_response(self) -> None:
        """Ответ без маркеров — это parse_error."""
        raw = "Здравствуйте! Я подумаю и вернусь к вам с ответом."
        decision, hint, _ = parse_gpt_response(raw)
        assert decision is GptCheckDecision.parse_error
        assert "подумаю" in hint  # snippet для отладки

    def test_long_unknown_response_truncated(self) -> None:
        raw = "x" * 1000
        _, hint, _ = parse_gpt_response(raw)
        assert len(hint) <= 200  # snippet ограничен 200 символами


class TestScore:
    """Извлечение численной оценки."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Одобрено. score: 0.85", 0.85),
            ("score=0.7", 0.7),
            ("Оценка: 0.5", 0.5),
            ('"score": 0.92', 0.92),
            ("оценка — 0,75", 0.75),  # русская запятая
            ("rating: 0.6", 0.6),
            # Нормализация диапазонов:
            ("score: 8.5", 0.85),  # 0..10
            ("score: 85", 0.85),   # 0..100
            ("score: 1.0", 1.0),
            ("score: 0.0", 0.0),
            ("score: 200", 1.0),   # клампим
        ],
    )
    def test_score_extraction(self, raw: str, expected: float) -> None:
        score = _extract_score(raw)
        assert score is not None
        assert abs(score - expected) < 1e-3, f"For {raw!r}: expected {expected}, got {score}"

    def test_no_score(self) -> None:
        assert _extract_score("Одобрено, без оценки.") is None
        assert _extract_score("Это число 42") is None

    def test_score_in_approved_response(self) -> None:
        """Парсер должен извлекать score из approved-ответа."""
        decision, _, score = parse_gpt_response("Одобрено. score: 0.87")
        assert decision is GptCheckDecision.approved
        assert score is not None
        assert abs(score - 0.87) < 1e-3

    def test_score_in_regenerate_response(self) -> None:
        """Парсер должен извлекать score даже из regenerate-ответа."""
        decision, _, score = parse_gpt_response(
            "Перегенерация: слишком короткое. score: 0.3"
        )
        assert decision is GptCheckDecision.regenerate
        assert score is not None
        assert abs(score - 0.3) < 1e-3


# ---------------------------------------------------------------------------
# build_check_message
# ---------------------------------------------------------------------------


class TestBuildCheckMessage:
    def test_only_prompt(self) -> None:
        msg = build_check_message("Проверь план.")
        assert msg == "Проверь план."

    def test_prompt_and_accompanying(self) -> None:
        msg = build_check_message(
            "Проверь план.",
            accompanying_text="Тема: космос",
        )
        assert "Проверь план." in msg
        assert "Тема: космос" in msg
        assert msg.index("Проверь план.") < msg.index("Тема: космос")

    def test_prompt_and_artifact_inline(self) -> None:
        msg = build_check_message(
            "Проверь план.",
            artifact_inline_text="План: разрабатываем X",
        )
        assert "Проверь план." in msg
        assert "План: разрабатываем X" in msg
        assert msg.index("Проверь план.") < msg.index("План:")

    def test_all_three(self) -> None:
        msg = build_check_message(
            "Проверь план.",
            accompanying_text="Тема: космос",
            artifact_inline_text="План: вот такой",
        )
        # Все три есть, в правильном порядке.
        idx_prompt = msg.index("Проверь")
        idx_acc = msg.index("Тема:")
        idx_art = msg.index("План:")
        assert idx_prompt < idx_acc < idx_art

    def test_empty_optional_parts(self) -> None:
        msg = build_check_message(
            "Проверь.",
            accompanying_text="   ",
            artifact_inline_text="",
        )
        assert msg == "Проверь."


# ---------------------------------------------------------------------------
# gpt_check_text_artifact: интеграционные с моком бота
# ---------------------------------------------------------------------------


class FakeChatGPTBot:
    """Минимальный stub-бот для тестов gpt_check_text_artifact."""

    def __init__(self, *, response: str, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict] = []
        self.new_conversation_called = 0

    async def new_conversation(self) -> None:
        self.new_conversation_called += 1

    async def ask(self, prompt: str, *, timeout: float = 300) -> str:
        self.calls.append({"prompt": prompt, "timeout": timeout})
        if self._raises is not None:
            raise self._raises
        return self._response

    async def download_attachment_from_last_reply(
        self, target_path: Path, *, timeout: float = 900
    ) -> Path:
        # По умолчанию — ChatGPT файла не приложил.
        raise RuntimeError("test: no attachment")


@pytest.mark.asyncio
async def test_gpt_check_text_artifact_approved() -> None:
    bot = FakeChatGPTBot(response="Одобрено. score: 0.92")
    result = await gpt_check_text_artifact(
        chatgpt_bot=bot,
        check_prompt="Проверь план.",
        artifact_text="План: космос",
    )
    assert result.decision is GptCheckDecision.approved
    assert bot.new_conversation_called == 1
    assert bot.calls[0]["timeout"] == 1200.0
    assert "Проверь план." in bot.calls[0]["prompt"]
    assert "План: космос" in bot.calls[0]["prompt"]
    assert result.score is not None
    assert abs(result.score - 0.92) < 1e-3


@pytest.mark.asyncio
async def test_gpt_check_text_artifact_regenerate() -> None:
    bot = FakeChatGPTBot(response="Перегенерация: добавь конкретики.")
    result = await gpt_check_text_artifact(
        chatgpt_bot=bot,
        check_prompt="Проверь.",
        artifact_text="План: общие слова",
    )
    assert result.decision is GptCheckDecision.regenerate
    assert "конкретики" in result.hint


@pytest.mark.asyncio
async def test_gpt_check_text_artifact_no_new_conversation() -> None:
    bot = FakeChatGPTBot(response="Одобрено.")
    await gpt_check_text_artifact(
        chatgpt_bot=bot,
        check_prompt="Проверь.",
        artifact_text="x",
        new_conversation=False,
    )
    assert bot.new_conversation_called == 0


@pytest.mark.asyncio
async def test_gpt_check_text_artifact_gpt_call_exception() -> None:
    """Если ChatGPTBot.ask() кинул исключение — ловим и возвращаем parse_error."""
    bot = FakeChatGPTBot(response="", raises=RuntimeError("network down"))
    result = await gpt_check_text_artifact(
        chatgpt_bot=bot,
        check_prompt="Проверь.",
        artifact_text="x",
    )
    assert result.decision is GptCheckDecision.parse_error
    assert "network down" in result.raw_response


@pytest.mark.asyncio
async def test_gpt_check_text_artifact_replace_file(tmp_path: Path) -> None:
    """Если GPT приложил файл, и мы передали download_replacement_to —
    decision должен быть replace_artifact с replaced_path."""
    replacement_target = tmp_path / "new_plan.txt"

    bot = FakeChatGPTBot(response="Одобрено, но прислал обновлённую версию.")

    async def fake_download(target: Path, *, timeout: float = 900) -> Path:
        target.write_text("обновлённый план", encoding="utf-8")
        return target

    bot.download_attachment_from_last_reply = fake_download  # type: ignore[method-assign]

    result = await gpt_check_text_artifact(
        chatgpt_bot=bot,
        check_prompt="Проверь.",
        artifact_text="x",
        download_replacement_to=replacement_target,
    )
    assert result.decision is GptCheckDecision.replace_artifact
    assert result.replaced_path is not None
    assert result.replaced_path == replacement_target
    assert replacement_target.exists()
    assert replacement_target.read_text(encoding="utf-8") == "обновлённый план"


@pytest.mark.asyncio
async def test_gpt_check_text_artifact_replace_unavailable_falls_back_to_text(
    tmp_path: Path,
) -> None:
    """Если download_attachment_from_last_reply бросил RuntimeError —
    значит файла нет; парсим текст."""
    bot = FakeChatGPTBot(response="Одобрено.")
    result = await gpt_check_text_artifact(
        chatgpt_bot=bot,
        check_prompt="Проверь.",
        artifact_text="x",
        download_replacement_to=tmp_path / "should_not_be_created.txt",
    )
    assert result.decision is GptCheckDecision.approved
    assert result.replaced_path is None
    assert not (tmp_path / "should_not_be_created.txt").exists()
