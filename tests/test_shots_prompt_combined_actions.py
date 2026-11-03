from pathlib import Path

PROMPT = Path(__file__).resolve().parents[1] / 'templates/node_groups/script_frames_qc/scenes_to_frames_ru.md'


def test_shots_require_combined_actions_and_voiceover_on_every_shot():
    text = PROMPT.read_text(encoding='utf-8')
    assert 'Объединяй несколько совместимых смысловых частей' in text
    assert 'последовательно или как параллельные действия' in text
    assert 'ОБЯЗАТЕЛЕН В КАЖДОМ КАДРЕ' in text
    assert 'Пустой, отсутствующий или состоящий из пробелов закадр — БРАК' in text
    assert 'Если целых предложений закадра меньше' in text
    for stale in ('Одна смысловая часть = один кадр', 'пустые кадры между кусками **норма**',
                  'Пустой закадр разрешён', '`закадр` кадра с репликой всегда пустой',
                  'Лучше несколько пустых', '"закадр": ""', 'это ДВА кадра'):
        assert stale not in text
