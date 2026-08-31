from app.models import Project
from app.services.elevenlabs_voices import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    resolve_elevenlabs_voice_id,
)


def test_resolve_default():
    p = Project(topic="t")
    p.meta = {}
    assert resolve_elevenlabs_voice_id(p) == DEFAULT_ELEVENLABS_VOICE_ID


def test_resolve_from_meta():
    p = Project(topic="t")
    p.meta = {
        "node_step_params": {
            "audio": {"elevenlabs_voice_id": "JBFqnCBsd6RMkjVDRZzb"},
        },
    }
    assert resolve_elevenlabs_voice_id(p) == "JBFqnCBsd6RMkjVDRZzb"


def test_resolve_invalid_falls_back():
    p = Project(topic="t")
    p.meta = {"node_step_params": {"audio": {"elevenlabs_voice_id": "bad"}}}
    assert resolve_elevenlabs_voice_id(p) == DEFAULT_ELEVENLABS_VOICE_ID
