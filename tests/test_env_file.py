"""Последний непустой ключ в .env — пустой дубль не затирает."""

from __future__ import annotations

from pathlib import Path

from app.services.env_file import last_nonempty_dotenv_value, last_nonempty_dotenv_values


def test_last_nonempty_skips_empty_duplicate(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "KIE_API_KEY=real-kie-key\n"
        "KIE_API_KEY=\n"
        "KIE_API_KEY=   \n"
        "GPT_API_KEY=gpt-key\n",
        encoding="utf-8",
    )
    assert last_nonempty_dotenv_value(p, "KIE_API_KEY") == "real-kie-key"
    assert last_nonempty_dotenv_values(p, ("KIE_API_KEY", "GPT_API_KEY")) == {
        "KIE_API_KEY": "real-kie-key",
        "GPT_API_KEY": "gpt-key",
    }


def test_last_nonempty_later_value_wins(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("KIE_API_KEY=old\nKIE_API_KEY=new\n", encoding="utf-8")
    assert last_nonempty_dotenv_value(p, "KIE_API_KEY") == "new"


def test_last_nonempty_quoted_and_comment(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        'KIE_API_KEY="abc#notcomment"\n# KIE_API_KEY=nope\nGPT_API_KEY=zzz # хвост\n',
        encoding="utf-8",
    )
    assert last_nonempty_dotenv_value(p, "KIE_API_KEY") == "abc#notcomment"
    assert last_nonempty_dotenv_value(p, "GPT_API_KEY") == "zzz"


def test_last_nonempty_missing_file(tmp_path: Path) -> None:
    assert last_nonempty_dotenv_value(tmp_path / "nope.env", "KIE_API_KEY") == ""
