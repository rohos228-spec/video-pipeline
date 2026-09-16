# CHECKS — исполняемые проверки

Каталог: корень репо. Интерпретатор: `.venv\Scripts\python.exe` или `python`.
Фикстуры: `tests/fixtures/scene_space/` (диалог, пересечение, поворот).

Код выхода валидатора: 0 чисто, 1 только warning, 2 error.

## Миграция

```
python scripts/scene_space_migrate.py --up
python scripts/scene_space_migrate.py --down
python scripts/scene_space_migrate.py --up
```

Проверка данных целы: `--down` не DROP `frames`/`scenes`/`projects`. Только `frames_space`, `scenes_space`.

```
python -m pytest tests/test_scene_space_migrate.py -q
```

## Property-тесты §7 (минимум 200 прогонов каждый)

```
python -m pytest tests/test_scene_space_properties.py tests/test_scene_space_geom.py -q
```

Имена тестов (должны существовать):

| # | test function |
|---|----------------|
| 1 | `test_rigid_transform_preserves_side` |
| 2 | `test_mirror_flips_side` |
| 3 | `test_scale_preserves_side_and_screen_pos` |
| 4 | `test_cross_axis_flips_screen_pos_and_reestablish` |
| 5 | `test_delta_accumulation_matches_full_replay` |
| 6 | `test_pair_order_normalizes_side` |
| 7 | `test_camera_on_axis_raises` |
| 8 | `test_facing_0_360_90_270` |
| 9 | `test_delete_middle_frame_state_stable` |
| 10 | `test_rebuild_idempotent` |

## Валидатор §6

```
python scripts/scene_space_validate.py --fixtures
```

Пишет `tasks/VALIDATION.md`. На трёх фикстурах **ноль error**. Warning 2.9 допустимы только если критерий ещё чинится; к «готово» warning 2.9 тоже 0.

## Отображение

```
python scripts/scene_space_plan.py --scene fix:dialogue --out tasks/out/fix-dialogue
python scripts/scene_space_plan.py --scene fix:cross --out tasks/out/fix-cross
python scripts/scene_space_plan.py --scene fix:turn --out tasks/out/fix-turn
python scripts/scene_space_board.py --scene fix:dialogue --out tasks/out/fix-dialogue/board.html
python scripts/scene_space_board.py --scene fix:cross --out tasks/out/fix-cross/board.html
python scripts/scene_space_board.py --scene fix:turn --out tasks/out/fix-turn/board.html
```

Ожидание: в каждом `tasks/out/fix-*/` файлы `plan_01.svg` … по числу кадров (≥8) и `board.html`. Повтор команды — байт-в-байт тот же SVG (кроме разрешённого отсутствия PNG).

## Правка смысла и разбивка

```
python scripts/scene_space_rewrite.py --scene fix:dialogue --meaning "A wins then loses the folder"
python scripts/scene_space_rewrite.py --scene fix:dialogue --meaning "A wins then loses the folder"
python scripts/scene_space_rebuild.py --scene fix:dialogue
python scripts/scene_space_rebuild.py --scene fix:dialogue
```

Второй прогон каждого: `changed` пустой. Кадр с `manual=1` не меняет полей space.

```
python -m pytest tests/test_scene_space_rewrite.py tests/test_scene_space_e2e.py -q
```

## Сквозной прогон

```
python scripts/scene_space_e2e.py
python -m pytest tests/test_scene_space_validator.py tests/test_scene_space_e2e.py -q
```

## Сводка «зелёный раунд»

```
python scripts/scene_space_migrate.py --up
python -m pytest tests/test_scene_space_geom.py tests/test_scene_space_migrate.py tests/test_scene_space_properties.py tests/test_scene_space_validator.py tests/test_scene_space_rewrite.py tests/test_scene_space_e2e.py -q --tb=short
python scripts/scene_space_e2e.py
python scripts/scene_space_validate.py --fixtures
```

Промты `prompts/scene_design/**` не должны быть в `git diff`.
