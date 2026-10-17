"""Ревью #352 п.5: хвост закадра длиннее ячейки склеивается в последний кадр."""

from app.services.montage_coverage_ops import fold_excess_shot_voiceover


def test_excess_voiceover_is_glued_onto_last_shot() -> None:
    kadry = [
        {"id": "1-K1", "закадр": "первый кусок"},
        {"id": "1-K2", "закадр": "второй кусок"},
        {"id": "1-K3", "закадр": "хвост реплики"},
    ]
    kept = fold_excess_shot_voiceover(kadry, 2)
    assert len(kept) == 2
    assert kept[0]["закадр"] == "первый кусок"
    assert kept[1]["закадр"] == "второй кусок хвост реплики"
    assert kadry[2]["закадр"] == "хвост реплики"
