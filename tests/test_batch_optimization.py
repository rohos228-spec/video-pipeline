# -*- coding: utf-8 -*-
import pytest
from app.services.output_batch_plan import (
    IMG_PR_FRAMES_PER_BATCH,
    batch_count_img_pr,
    pack_frames_img_pr,
    split_into_n_batches,
)
from app.services.apply_ops_batches import (
    _DENSE_FRAMES_PER_BATCH,
    SCRIPT_FRAMES_QC_UNITS_PER_BATCH,
    _shot_vo_len_reason,
    bits_ops_reason,
    shots_coverage_ops_reason,
)
from app.services.volume_batches import merge_apply_ops_payloads


def test_batch_constants_standardized():
    assert IMG_PR_FRAMES_PER_BATCH == 8
    assert _DENSE_FRAMES_PER_BATCH == 8
    assert SCRIPT_FRAMES_QC_UNITS_PER_BATCH == 8


def test_pack_frames_img_pr_slices():
    frames = [{'uuid': f'u{i}'} for i in range(24)]
    batches = pack_frames_img_pr(frames)
    assert len(batches) == 3
    assert all(len(b) == 8 for b in batches)


def test_split_into_n_batches():
    items = list(range(20))
    batches = split_into_n_batches(items, 4)
    assert len(batches) == 4
    assert sum(len(b) for b in batches) == 20


def test_short_vo_len_reason_passes():
    short_vo = 'Он оглянулся вокруг.'
    shots = [{'id': '1-S2-K2', 'закадр': short_vo, 'план': 'средний'}]
    reason = _shot_vo_len_reason(shots, short_vo, 'uuid12345678')
    assert reason is None


def test_bits_ops_reason_short_vo_and_auto_anchor():
    vo = 'Он быстро открыл дверь.'
    frames = [{'uuid': 'uuid1234', 'attrs': {'закадр': vo}}]
    ops = [
        {
            'frame_uuid': 'uuid1234',
            'fields': {
                'биты': [
                    {'глагол': 'открыл', 'изменение': 'дверь открыта'}
                ]
            }
        }
    ]
    reason = bits_ops_reason(ops, frames)
    assert reason is None
    assert ops[0]['fields']['биты'][0].get('якорь') is not None


def test_shots_coverage_single_shot_on_short_vo():
    vo = 'Короткая фраза.'
    frames = [{'uuid': 'uuid1234', 'attrs': {'закадр': vo}}]
    ops = [
        {
            'frame_uuid': 'uuid1234',
            'fields': {
                'кадры': [
                    {
                        'id': '1-S1-K1',
                        'закадр': vo,
                        'план': 'общий',
                        'ракурс': 'на уровне глаз',
                        'место': 'комната'
                    }
                ]
            }
        }
    ]
    reason = shots_coverage_ops_reason(ops, frames)
    assert reason is None


def test_merge_apply_ops_payloads():
    base = {'ops': [{'frame_uuid': 'u1', 'fields': {'a': 1}}]}
    part1 = {'ops': [{'frame_uuid': 'u2', 'fields': {'b': 2}}]}
    part2 = {'ops': [{'frame_uuid': 'u3', 'fields': {'c': 3}}]}
    merged = merge_apply_ops_payloads(base, part1, part2)
    assert len(merged['ops']) == 3
    uids = {op['frame_uuid'] for op in merged['ops']}
    assert uids == {'u1', 'u2', 'u3'}
