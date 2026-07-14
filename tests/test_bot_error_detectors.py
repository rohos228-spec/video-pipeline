"""Тесты детекторов ошибок ботов (без реального браузера)."""

from __future__ import annotations

from app.bots.chatgpt import (
    chatgpt_login_page_text,
    chatgpt_login_url,
    chatgpt_rate_limit_in_text,
)
from app.bots.elevenlabs import (
    elevenlabs_error_in_text,
    elevenlabs_login_page_text,
    elevenlabs_login_url,
)
from app.bots.outsee import (
    outsee_login_page_text,
    outsee_login_url,
    _outsee_timeout_message,
)


def test_chatgpt_rate_limit_english() -> None:
    assert chatgpt_rate_limit_in_text("You've reached your limit for today")


def test_chatgpt_rate_limit_russian() -> None:
    assert chatgpt_rate_limit_in_text("Достигнут лимит запросов, подождите")


def test_chatgpt_rate_limit_negative() -> None:
    assert not chatgpt_rate_limit_in_text("Готово, вот ваш ответ")


def test_chatgpt_login_url() -> None:
    assert chatgpt_login_url("https://chatgpt.com/auth/login?next=/")
    assert not chatgpt_login_url("https://chatgpt.com/c/abc123")


def test_chatgpt_login_page_text() -> None:
    assert chatgpt_login_page_text("Sign in to ChatGPT\nContinue with Google")
    assert chatgpt_login_page_text("Войти\nEmail\nPassword")


def test_outsee_login_url() -> None:
    assert outsee_login_url("https://outsee.io/login")
    assert not outsee_login_url("https://outsee.io/generate")


def test_outsee_login_page_text() -> None:
    assert outsee_login_page_text("Sign in\nEmail\nPassword")
    assert outsee_login_page_text("Войти в аккаунт\nПароль")


def test_elevenlabs_login_url() -> None:
    assert elevenlabs_login_url("https://elevenlabs.io/sign-in")
    assert not elevenlabs_login_url("https://elevenlabs.io/app/speech-synthesis")


def test_elevenlabs_login_page_text() -> None:
    assert elevenlabs_login_page_text("Log in\nEmail address\nPassword")


def test_elevenlabs_error_marker() -> None:
    snip = elevenlabs_error_in_text("Something went wrong. Please try again later.")
    assert snip is not None
    assert "went wrong" in snip.lower() or "try again" in snip.lower()


def test_elevenlabs_error_russian() -> None:
    assert elevenlabs_error_in_text("Произошла ошибка генерации. Попробуйте снова.")


def test_outsee_timeout_message_includes_alerts() -> None:
    msg = _outsee_timeout_message(
        "outsee image: результат не появился за 600 сек",
        ["Новая ошибка сервиса XYZ", "Попробуйте позже"],
    )
    assert "Плашки на странице" in msg
    assert "Новая ошибка сервиса XYZ" in msg
