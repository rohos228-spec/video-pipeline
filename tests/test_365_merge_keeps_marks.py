"""PR #365: контентная ветка merge_kadry_keep_missing не снимает пометки скрытия.

Раньше шот без id, помеченный только «удалён»/«карантин» или строковым
``активен: "ложь"``, оживал от входящей копии с тем же текстом: ветка
сравнивала ``активен`` через ``is False`` вместо предиката activity_flags.
"""

from app.services.frame_tombstones import (
    ACTIVE_MARK,
    DELETED_MARK,
    QUARANTINE_MARK,
    merge_kadry_keep_missing,
    shot_is_active,
)


def _merge_one(old_shot: dict, new_shot: dict) -> dict:
    merged = merge_kadry_keep_missing([old_shot], [new_shot])
    assert len(merged) == 1, merged
    assert isinstance(merged[0], dict)
    return merged[0]


def test_bez_id_tolko_udalen_ostaetsya_neaktivnym():
    out = _merge_one({"описание": "лес", DELETED_MARK: True}, {"описание": "лес"})
    assert out[DELETED_MARK] is True
    assert not shot_is_active(out)


def test_bez_id_tolko_karantin_ostaetsya_neaktivnym():
    out = _merge_one({"описание": "лес", QUARANTINE_MARK: True}, {"описание": "лес"})
    assert out[QUARANTINE_MARK] is True
    assert not shot_is_active(out)


def test_bez_id_aktiven_lozh_ostaetsya_neaktivnym():
    out = _merge_one({"описание": "лес", ACTIVE_MARK: "ложь"}, {"описание": "лес"})
    assert not shot_is_active(out)


def test_yavnyi_aktiven_true_po_prezhnemu_ozhivlyaet():
    out = _merge_one(
        {"описание": "лес", DELETED_MARK: True},
        {"описание": "лес", ACTIVE_MARK: True},
    )
    assert shot_is_active(out)
    assert DELETED_MARK not in out


def test_vetka_po_id_ne_izmenilas():
    out = _merge_one(
        {"id": "s1", "описание": "лес", DELETED_MARK: True},
        {"id": "s1", "описание": "лес"},
    )
    assert out[DELETED_MARK] is True
    assert not shot_is_active(out)
