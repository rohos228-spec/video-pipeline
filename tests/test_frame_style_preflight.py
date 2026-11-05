from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.services import frame_style as fs


def row(number, prompt=None, role='vo_parent'):
    entry = {'id': f'{number}-S1-K1', 'parent_id': None, 'действие': 'Действие не является промтом.'}
    if prompt is not None:
        entry['промт_картинки'] = prompt
    return SimpleNamespace(number=number, uuid=f'u{number}', image_prompt='', status='planned',
                           attrs={'camera_subdivide': {'role': role}, 'кадры': [entry]})


def test_strict_style_missing_prompt_does_not_mutate_valid_scene_first():
    good, missing = row(1, 'Сцена один.'), row(2)
    before = deepcopy([vars(good), vars(missing)])
    with pytest.raises(fs.StyleError, match='исходные промты Шотов'):
        fs.apply_style_to_frames([good, missing], style_name='style_test',
                                 style_text='STYLE: noir.', require_all_prompts=True)
    assert [vars(good), vars(missing)] == before


def test_strict_style_requires_at_least_one_main_scene():
    with pytest.raises(fs.StyleError, match='нет главных кадров'):
        fs.apply_style_to_frames([], style_name='style_test', style_text='STYLE: noir.',
                                 require_all_prompts=True)


def test_strict_style_ignores_child_missing_prompt_and_styles_root():
    root, child = row(1, 'Сцена один.'), row(2, role='shot')
    report = fs.apply_style_to_frames([root, child], style_name='style_test',
                                     style_text='STYLE: noir.', require_all_prompts=True)
    assert report['counts']['styled'] == 1
    assert report['counts']['no_prompt'] == 0
    assert root.image_prompt.endswith('STYLE: noir.')
    assert child.image_prompt == ''
