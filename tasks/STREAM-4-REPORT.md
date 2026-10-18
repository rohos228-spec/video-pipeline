# STREAM-4-REPORT

## Сделано с доказательствами (команда и её вывод)

Ветке `night/scene-space-4` добавлены валидатор CANON §6 и property-тесты §7.

- `validate_scene(scene_id, frames_space_rows, space_json, frames_by_uuid)` → список `{level, rule_id, shot_order, message}`.
- Все `rule_id` из CONTRACTS §3: `side_lock`, `side_cross_silent`, `screen_pos`, `screen_dir`, `size_same`, `size_adjacent`, `size_smash`, `angle_30`, `reestablish`, `degenerate`, `pair_norm`, `missing_uuid`, `schema`, `prompt_len`, `prompt_forbidden`, `prompt_style`, `interest_*`, `no_turn`.
- Лимит промта: `OUTSEE_PROMPT_MAX_CHARS`. Штампы из `prompts/blocks/forbidden_phrases/ai_cliches_ru.md` (если файла нет в worktree — fallback тех же фраз из текущего файла на housepc). Стиль/негатив: `PROMPT:` / `PROMPT ` / `Стиль:` / `Style:` и `NEGATIVE PROMPT` / `NEGATIVE:`, только позитив до маркера.
- CLI `scripts/scene_space_validate.py --fixtures` / `--scene SCENE_ID` пишет `tasks/VALIDATION.md`, код 2/1/0.
- Тесты 1–8: `tests/test_scene_space_geom.py`, 200 прогонов `random.Random`, без hypothesis. Имена как в CHECKS.md. Падают красным, если нет `geom` (не skip).
- Тесты 9–10: `importorskip` только если нет `store` / `rewrite`.

Команда:

```
cd .worktrees/stream-4
$env:PYTHONPATH = (Get-Location).Path
C:\Users\Admin\Desktop\video-pipeline\.venv\Scripts\python.exe -m pytest tests/test_scene_space_geom.py tests/test_scene_space_properties.py tests/test_scene_space_validator.py -q --tb=short
```

Вывод (verbatim):

```
FFFFFFFFss..............................                                 [100%]
================================== FAILURES ===================================
_____________________ test_rigid_transform_preserves_side _____________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:85: in test_rigid_transform_preserves_side
    apply_deltas, axis_side, _fv, normalize_pair, _err = _load_geom()
                                                         ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
___________________________ test_mirror_flips_side ____________________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:103: in test_mirror_flips_side
    _ad, axis_side, _fv, _np, _err = _load_geom()
                                     ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
__________________ test_scale_preserves_side_and_screen_pos ___________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:115: in test_scale_preserves_side_and_screen_pos
    _ad, axis_side, _fv, normalize_pair, _err = _load_geom()
                                                ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
______________ test_cross_axis_flips_screen_pos_and_reestablish _______________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:133: in test_cross_axis_flips_screen_pos_and_reestablish
    apply_deltas, axis_side, _fv, normalize_pair, _err = _load_geom()
                                                         ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
_________________ test_delta_accumulation_matches_full_replay _________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:169: in test_delta_accumulation_matches_full_replay
    apply_deltas, _side, _fv, _np, _err = _load_geom()
                                          ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
_______________________ test_pair_order_normalizes_side _______________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:197: in test_pair_order_normalizes_side
    _ad, axis_side, _fv, normalize_pair, _err = _load_geom()
                                                ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
_________________________ test_camera_on_axis_raises __________________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:213: in test_camera_on_axis_raises
    _ad, axis_side, _fv, _np, DegenerateAxisError = _load_geom()
                                                    ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
__________________________ test_facing_0_360_90_270 ___________________________
tests\test_scene_space_geom.py:20: in _load_geom
    from app.services.scene_space.errors import DegenerateAxisError
E   ModuleNotFoundError: No module named 'app.services.scene_space.errors'

During handling of the above exception, another exception occurred:
tests\test_scene_space_geom.py:229: in test_facing_0_360_90_270
    _ad, _side, facing_vector, _np, _err = _load_geom()
                                           ^^^^^^^^^^^^
tests\test_scene_space_geom.py:28: in _load_geom
    pytest.fail(
E   Failed: geom missing until stream 1 lands: No module named 'app.services.scene_space.errors'. CONTRACTS require normalize_pair, axis_side, apply_deltas, DegenerateAxisError; geom.facing_vector for CANON 7.8.
=========================== short test summary info ===========================
FAILED tests/test_scene_space_geom.py::test_rigid_transform_preserves_side - ...
FAILED tests/test_scene_space_geom.py::test_mirror_flips_side - Failed: geom ...
FAILED tests/test_scene_space_geom.py::test_scale_preserves_side_and_screen_pos
FAILED tests/test_scene_space_geom.py::test_cross_axis_flips_screen_pos_and_reestablish
FAILED tests/test_scene_space_geom.py::test_delta_accumulation_matches_full_replay
FAILED tests/test_scene_space_geom.py::test_pair_order_normalizes_side - Fail...
FAILED tests/test_scene_space_geom.py::test_camera_on_axis_raises - Failed: g...
FAILED tests/test_scene_space_geom.py::test_facing_0_360_90_270 - Failed: geo...
8 failed, 30 passed, 2 skipped in 0.57s
```

Skip 9–10 (`-rs`):

```
SKIPPED [1] tests\test_scene_space_properties.py:48: could not import 'app.services.scene_space.store': No module named 'app.services.scene_space.store'
SKIPPED [1] tests\test_scene_space_properties.py:91: could not import 'app.services.scene_space.rewrite': No module named 'app.services.scene_space.rewrite'
```

CLI:

```
python scripts/scene_space_validate.py --fixtures
wrote ...\tasks\VALIDATION.md exit=0
```

(фикстур stream 5 в этом worktree нет.)

Проверка тестов 1–8 против geom потока 1 (тот же процесс, `geom.py`/`errors.py` из `.worktrees/stream-1`): **8 passed**. Тест 9 против `store` потока 1: **passed**. Тест 10 без `rewrite`: skip.

`ruff check` на пяти своих файлах: All checks passed.

## Не сделано с причиной

- Нет `geom.py` / `store.py` / `__init__.py` в этом worktree — чужое владение потока 1. Тесты 1–8 красные до merge. Это намеренно.
- Фикстуры `tests/fixtures/scene_space/*.json` — поток 5. `--fixtures` пишет пустой отчёт, exit 0.
- `test_rebuild_idempotent` skip: нет `app.services.scene_space.rewrite`.
- Не трогал `pyproject.toml`, prompts, CONTRACTS, geom, store.

## Затронутые файлы

- `app/services/scene_space/validate.py`
- `scripts/scene_space_validate.py`
- `tests/test_scene_space_geom.py`
- `tests/test_scene_space_properties.py`
- `tests/test_scene_space_validator.py`
- `tasks/STREAM-4-REPORT.md` (этот отчёт)
- артефакт прогона, не в коммите: `tasks/VALIDATION.md`

## Запросы на чужие файлы и контракты

1. **Поток 1:** после merge `errors.py` / `geom.py` / `store.py` / `__init__.py` тесты 1–8 и 9 должны стать зелёными. Нужен `geom.facing_vector` (уже есть в stream-1).
2. **Поток 1 `__init__.py`:** не импортировать `validate` циклично; достаточно оставить validate отдельным модулем.
3. **Поток 2 `rules.compute_screen_pos`:** проекция на `right = (axis.y, -axis.x)` даёт одинаковый proj у обоих концов оси → всегда C/C. Валидатор считает screen x как проекцию `(pos − cam)` на clockwise-perp вектора look `(mid − cam)`. Пример stream-1 (`c01` (0,2), `c02` (1,2), `cam` (0.5,0)): `c01=L`, `c02=R`. Просьба выровнять blocking на ту же формулу, иначе фикстуры получат `screen_pos` error.
4. **Поток 5:** фикстуры с `PROMPT:` + `NEGATIVE PROMPT:` / `NEGATIVE:`, без штампов, `axis_pair` уже `lo|hi`, сторона согласована с `axis_side`, камера сдвинута ≥30° между соседними кадрами.
5. **Файл штампов** `prompts/blocks/forbidden_phrases/ai_cliches_ru.md` не в git этого worktree (untracked на housepc). Валидатор читает его, если есть; иначе fallback семи фраз из текущего файла.
6. **Поток 5 `rewrite.rebuild`:** тест 10 ищет `rebuild` / `rebuild_shots` / `rebuild_scene` / `rebuild_frames`, второй вызов — `changed.inserted` и `changed.updated` пустые.

## Что сломалось

- В изоляции worktree 8 красных тестов geom — нет модуля потока 1. Это ожидаемый красный merge-сигнал, не баг валидатора.
- `screen_pos` потока 2 (если останется axis-perp) разъедется с валидатором; чинить в blocking, не ослаблять тест.
- Ничего в пайплайне / промтах / main не ломалось: чужие файлы не менялись.
