"""Первый шаг: бит = действие/реакция, закадр только по якорю."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _mod():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_spesivtsev_script_frames_qc.py"
    spec = importlib.util.spec_from_file_location("spesivtsev_step1", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_spesivtsev_first_step_is_mckee_exchange() -> None:
    mod = _mod()
    model = mod.run()
    bits = model["bits"]
    assert len(bits) == 11
    assert "shots" not in model
    assert "action" not in model
    glued = " ".join(str(b["закадр"]) for b in bits)
    assert glued == model["vo"]
    for bit in bits:
        verb = str(bit["глагол"])
        assert " / " in verb
        assert "→" in str(bit["изменение"])
        assert str(bit["якорь"]) in model["vo"]
        assert str(bit["изменение"]) not in str(bit["закадр"])


def test_spesivtsev_bits_are_not_two_word_slogans() -> None:
    slogans = {"мать → центр", "мир → закрыт", "отец → периферия", "поиск → путаница"}
    for bit in _mod().BITS:
        assert bit["изменение"] not in slogans
        assert len(str(bit["глагол"]).split()) >= 3
