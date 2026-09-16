# ROUND-1

Ветка: `housepc`. Worktree-потоки `night/scene-space-1`…`5` слиты локально. `main` не трогали. Push не было.

## Что прошло

- Миграция `--up` / `--down` / `--up` на `data/state.db`: `projects=59`, `scenes=15`, `frames=2057` до и после. После `--down` нет `scenes_space`/`frames_space`; после `--up` таблицы снова есть.
- `pytest` 6 файлов scene_space: **66 passed**.
- Property §7: 10 тестов собраны и зелёные, по 200 прогонов в geom (1–8).
- Валидатор `--fixtures`: **exit=0**, 3 сцены по 0 error / 0 warning (`tasks/VALIDATION.md`).
- e2e: **exit=0**, второй rewrite/rebuild без INSERT/UPDATE; `manual` кадр `…0003` в skip; фикстуры 10/10/10.
- Раскладки: по 10 SVG+PNG и `board.html` на `fix:dialogue`, `fix:cross`, `fix:turn`. Нарушений на досках 0. План сверху читается как последовательность (сторона B оранжевая, после пересечения оси сторона A зелёная, поворот — стрелка на c01).

## Что упало в раунде и как закрыто

| Симптом | Гипотеза | Что проверено | Фикс |
|---|---|---|---|
| rewrite не идемпотентен | blocking каждый раз новые uuid / hash включал назначенные поля | pytest rewrite | полные ряды в blocking; hash = meaning+bits+content uuid |
| validate exit 2 на singles / 30° | singles требовали L/R; угол только по орбите | CLI validate | C для одиночных; 30° = орбита **или** смена имени ракурса |
| board violations=0 всегда | `try_call_validate` неверная арность | HTML | вызов `validate_scene(id, frames, space, by_uuid)` |
| interest 2.9 warning на cross/turn | smash EWS→MCU, монотония action×3, close-up streak без WS | VALIDATION.md | фикстуры: FS, reaction, WS insert |
| e2e `load_fixture` AttributeError | import validate.load_fixture затенил rewrite.load_fixture | traceback | alias `load_validate_fixture` |
| e2e exit 1 со 2-го запуска | leftover `scene_space_e2e.db` (11 кадров) | coverage_dialogue | unlink db в CLI |

## Интеграция после merge (оркестратор)

Файлы: `rewrite.py`, `validate.py`, `render_board.py`, `render_plan.py`, фикстуры cross/turn, e2e CLI.

## Дальше

Критерии §11 на фикстурах зелёные. Раунд 2 не запускаем. Остаток — не слой, а стык с живым пайплайном (см. `tasks/REPORT.md`).
