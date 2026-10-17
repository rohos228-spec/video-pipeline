"""Фильтр пула holdout-3: одно правило — один тест."""

from __future__ import annotations

from evals.orchestrator.holdout3 import (
    RULE_DATASET_FRAME,
    RULE_FEW_CONTENT_WORDS,
    RULE_SINGLE_LETTER,
    RULE_VO_BRACKET,
    rejection_rules,
)

_KEPT = "оператор ставит камеру у окна"


def test_filter_rejects_vo_bracket() -> None:
    marked = "[vo-65-clean-01] оператор ставит камеру у окна"
    ellipsis = "[vo-…] оператор ставит камеру у окна"
    assert rejection_rules(marked) == [RULE_VO_BRACKET]
    assert rejection_rules(ellipsis) == [RULE_VO_BRACKET]
    assert rejection_rules(_KEPT) == []


def test_filter_rejects_dataset_frame_id() -> None:
    text = "65-clean fr-65-keep-01"
    record = {
        "dataset": "65-seeded",
        "record_type": "frame",
        "record_id": "fr-65-del-01",
        "text": "",
    }
    assert rejection_rules(text) == [RULE_DATASET_FRAME, RULE_FEW_CONTENT_WORDS]
    assert RULE_DATASET_FRAME in rejection_rules("65-seeded fr-65-del-01", record)
    assert rejection_rules(_KEPT, record) == []


def test_filter_rejects_single_letter_row() -> None:
    assert RULE_SINGLE_LETTER in rejection_rules("аааа")
    assert RULE_SINGLE_LETTER in rejection_rules("[vo-65-short-01] ааа")
    assert RULE_SINGLE_LETTER in rejection_rules("а а а")
    assert RULE_SINGLE_LETTER not in rejection_rules("кадр и свет")
    assert rejection_rules(_KEPT) == []


def test_filter_rejects_fewer_than_three_content_words() -> None:
    # «и» — стоп-слово, id и односимвольные токены не считаются.
    short = "кадр и свет"
    with_id = "кадр fr-65-keep-01 и я"
    assert rejection_rules(short) == [RULE_FEW_CONTENT_WORDS]
    assert rejection_rules(with_id) == [RULE_FEW_CONTENT_WORDS]
    assert len(short.split()) >= 3
    assert rejection_rules(_KEPT) == []
