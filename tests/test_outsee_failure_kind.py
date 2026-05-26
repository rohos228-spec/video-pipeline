from app.bots.outsee import _outsee_failure_kind


def test_outsee_failure_kind_moderation() -> None:
    assert _outsee_failure_kind("Контент отклонён модерацией") == "moderation"


def test_outsee_failure_kind_generation() -> None:
    assert _outsee_failure_kind("Ошибка генерации. Попробуйте снова.") == "generation"


def test_outsee_failure_kind_moderation_before_generation() -> None:
    text = "Контент отклонён. Ошибка генерации."
    assert _outsee_failure_kind(text) == "moderation"
