"""Первый шаг читает сырой apply-ops, не заготовленный список битов."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _mod():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_spesivtsev_script_frames_qc.py"
    spec = importlib.util.spec_from_file_location("spesivtsev_step1", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_first_step_loads_ops_not_handwritten_bits(tmp_path: Path) -> None:
    mod = _mod()
    reply = tmp_path / "gpt_reply.txt"
    frames = tmp_path / "db_frames.json"
    vo = "Он вошёл в кабинет. Отец остался у двери."
    uid = "8f3c1a2b-4d5e-4678-9abc-def012345678"
    frames.write_text(
        json.dumps(
            {"frames": [{"number": 1, "uuid": uid, "voiceover_text": vo}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reply.write_text(
        json.dumps(
            {
                "ops": [
                    {
                        "frame_uuid": uid,
                        "fields": {
                            "биты": [
                                {
                                    "порядок": 1,
                                    "глагол": "входя / принимая",
                                    "изменение": "снаружи → внутри",
                                    "якорь": "Он вошёл",
                                },
                                {
                                    "порядок": 2,
                                    "глагол": "оставаясь / не входя",
                                    "изменение": "вместе → отец у порога",
                                    "якорь": "Отец остался",
                                },
                            ]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    mod.REPLY = reply
    mod.FRAMES = frames
    loaded_vo, bits = mod.load_node_bits(reply)
    assert loaded_vo == vo
    assert [b["глагол"] for b in bits] == ["входя / принимая", "оставаясь / не входя"]
    assert not hasattr(mod, "BITS")
