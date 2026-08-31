"""Последний непустой ключ в .env — пустой дубль не затирает."""

from pathlib import Path

from app.services.env_file import last_nonempty_dotenv_value


def test_last_nonempty_skips_empty_duplicate(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("KIE_API_KEY=real-kie-key\nKIE_API_KEY=\n", encoding="utf-8")
    assert last_nonempty_dotenv_value(p, "KIE_API_KEY") == "real-kie-key"
