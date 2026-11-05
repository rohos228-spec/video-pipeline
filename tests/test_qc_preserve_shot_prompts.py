from copy import deepcopy

import pytest

from app.services.db_apply import preserve_qc_shot_prompts


def test_qc_preserves_prompts_and_style_by_exact_id_even_after_reordering():
    old = [
        {'id': 'K1', 'действие': 'old', 'промт_картинки': 'scene', 'стиль': 'noir', 'промт_без_стиля': 'raw', 'стиль_поле': 'промт_картинки'},
        {'id': 'K2', 'промт_старт': 'start', 'промт_конец': 'end', 'промт_видео': 'video'},
    ]
    new = [{'id': 'K2', 'действие': 'fixed', 'промт_старт': 'malicious'}, {'id': 'K1', 'промт_картинки': ''}]
    before = deepcopy((old, new))
    result = preserve_qc_shot_prompts(old, new)
    assert result[0] == {'id': 'K2', 'действие': 'fixed', 'промт_старт': 'start', 'промт_конец': 'end', 'промт_видео': 'video'}
    assert result[1]['промт_картинки'] == 'scene'
    assert result[1]['стиль'] == 'noir'
    assert result[1]['промт_без_стиля'] == 'raw'
    assert (old, new) == before


@pytest.mark.parametrize('sid', ['k1', ' K1', 'K1 ', 'NEW', None])
def test_no_fuzzy_or_positional_transfer(sid):
    result = preserve_qc_shot_prompts([{'id': 'K1', 'промт_картинки': 'protected'}], [{'id': sid, 'промт_картинки': 'invented', 'действие': 'fixed'}])
    assert 'промт_картинки' not in result[0]
    assert result[0]['действие'] == 'fixed'


def test_duplicate_source_ids_are_ambiguous_and_not_transferred():
    old = [{'id': 'K1', 'промт_картинки': 'A'}, {'id': 'K1', 'промт_картинки': 'B'}]
    assert preserve_qc_shot_prompts(old, [{'id': 'K1'}]) == [{'id': 'K1'}]


def test_duplicate_destination_ids_do_not_duplicate_reference_prompts():
    old = [{'id': 'K1', 'промт_картинки': 'A'}]
    assert preserve_qc_shot_prompts(old, [{'id': 'K1'}, {'id': 'K1'}]) == [{'id': 'K1'}, {'id': 'K1'}]


def test_missing_ids_and_non_dicts_are_not_inferred():
    assert preserve_qc_shot_prompts([{'промт_старт': 'A'}], [{'действие': 'new'}, None]) == [{'действие': 'new'}, None]


def test_prompt_for_existing_id_cannot_be_added_by_qc():
    assert preserve_qc_shot_prompts([{'id': 'K1'}], [{'id': 'K1', 'стиль': 'hacked', 'image_prompt': 'hacked'}]) == [{'id': 'K1'}]


def test_qc_template_requires_nonempty_vo_and_exact_ids():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / 'templates/node_groups/script_frames_qc/shots_qc_ru.md').read_text(encoding='utf-8')
    assert 'Каждый кадр обязан иметь непустой закадр' in text
    assert 'точному уникальному `id`' in text
    assert 'совместимые смысловые части' in text
    assert '(можно пустой)' not in text
    assert 'их позже пишет `img_pr`' not in text
