"""Persist ElevenLabs lab voice library on disk."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.settings import settings

_VOICES_FILE = Path("./data/elevenlabs_lab/voices.json")


def _path() -> Path:
    p = settings.data_dir / "elevenlabs_lab" / "voices.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_voices() -> list[dict]:
    path = _path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return list(data.get("voices") or [])


def save_voices(voices: list[dict]) -> None:
    path = _path()
    path.write_text(
        json.dumps({"voices": voices, "updated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_voice(*, name: str, voice_id: str, sample_path: str | None = None, meta: dict | None = None) -> dict:
    voices = load_voices()
    row = {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip(),
        "voice_id": voice_id.strip(),
        "sample_path": sample_path,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    voices.insert(0, row)
    save_voices(voices)
    return row


def delete_voice(voice_row_id: str) -> bool:
    voices = load_voices()
    new_list = [v for v in voices if v.get("id") != voice_row_id]
    if len(new_list) == len(voices):
        return False
    save_voices(new_list)
    return True
