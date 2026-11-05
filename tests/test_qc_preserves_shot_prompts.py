from app.services.apply_ops_batches import coalesce_cell_shot_ops


def test_qc_partial_patch_preserves_generated_prompts_and_style():
    frames = [{'uuid': 'scene', 'кадры': [
        {'id': 'K1', 'действие': 'old', 'закадр': 'First VO.', 'промт_картинки': 'approved image', 'стиль': 'noir'},
        {'id': 'K2', 'промт_старт': 'approved start', 'закадр': 'VO.'},
    ]}]
    ops = [{'frame_uuid': 'scene', 'fields': {'кадры': [
        {'id': 'K1', 'действие': 'corrected'}, {'id': 'K2', 'закадр': 'Updated VO.'},
    ]}}]
    result = coalesce_cell_shot_ops(ops, frames, patch=True)[0]['fields']['кадры']
    assert result[0]['действие'] == 'corrected'
    assert result[0]['промт_картинки'] == 'approved image'
    assert result[0]['стиль'] == 'noir'
    assert result[1]['промт_старт'] == 'approved start'
    assert result[1]['закадр'] == 'Updated VO.'
