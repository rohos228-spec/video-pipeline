# REPORT — ночной прогон scene_space

Ветка: `housepc`, локально. `main` / `origin/main` не трогали. Push не было.

## Живой пайплайн (2026-09-17)

Overlay ingest + хук assemble. Существующие проекты **не** получают служебные кадры.

- CLI: `python scripts/scene_space_sync.py --project ID`
- После `scene_design` assemble: ingest + blocking (может вставить Frame, картинок ещё нет)
- Прогон overlay: `#50 nicshe-60-sekund` → `p50:s3` (18 кадров); `#57 scene-design-30` → `p57:s10` (8)
- Валидатор на живых: exit=2 — это правда монтажа (все MS, угол не меняется), не баг ingest
- Раскладки: `tasks/out/p50-s3/board.html`, `tasks/out/p57-s10/board.html`

Повторный sync `#50` — `skipped: unchanged`.

На `#50` в `Frame.attrs` нет `крупность`/`план` — overlay ставит MS. Валидатор честно орёт `size_same` + `angle_30`. Это дыра данных пайплайна, не ingest.

## Не работает

- Studio UI / нода канваса по-прежнему нет. CLI + хук assemble.
- Существующие проекты без крупности в attrs выглядят как ряд MS.
- Overlay не чинит монтаж сам (ты выбрал: вставки только на новом assemble).
- `fix:*` по-прежнему через `--from-json`.
- Существующие промты сцен не подключены — так и задумано.

## Работает (цифры)

| Проверка | Результат |
|---|---|
| `python -m pytest tests/test_scene_space_*.py -q` | **70 passed** |
| Property §7.1–10 (200 прогонов в geom) | **10/10** собраны, все зелёные |
| `python scripts/scene_space_validate.py --fixtures` | **exit=0**; 3 сцены × 0 error / 0 warning |
| Критерии 2.9 на dialogue / cross / turn | все `ok` на трёх `board.html` |
| Миграция `--up/--down/--up` | pipeline `projects=59` `scenes=15` `frames=2057` целы; space-таблицы снимаются и создаются |
| `python scripts/scene_space_e2e.py` | **exit=0**; seed 10/10/10; rewrite_second и rebuild_second пустые; manual `d1a1…0003` skipped |
| План сверху | 3×10 SVG + PNG в `tasks/out/fix-{dialogue,cross,turn}/` |
| Монтажные раскладки | 3 HTML, `violations=0` |

Валидатор: `tasks/VALIDATION.md`.

### Последовательности (просмотрены)

- **dialogue:** EWS establish → FS два тела → MS/CU одиночные → ECU insert → MCU reaction → WS переустановка → MWS → MCU turning_point → ECU. Сторона B заперта, камера в оранжевой полуплоскости.
- **cross:** L→R до пересечения → EWS reestablish → recross `crossing_method` → сторона A, `screen_pos` зеркально (c01 справа). На кадре 9 камера в зелёной полуплоскости.
- **turn:** L→R, ECU smash-insert, MCU reaction, MCU pivot со стрелкой, затем R→L, WS insert гасит close-up streak.

## Отложенное

- Studio UI / монтажная доска web
- Новая нода канваса
- Вызов `assign_blocking` из `camera_expand` / scene_design
- Excel R* write-through
- hypothesis как зависимость
- Push, merge в `main`
- Грязное дерево вне слоя (`apply_ops_batches.py`, `scene_shot_grammar.py`, `shots_report.py`, `vo_shot_expand.py`, `app/web/routers/projects.py`, куча `prompts/**`) — **не в этом коммите**
- Fallback raw-SQL внутри `rewrite.py` (изоляция потока 5); после merge store есть, мёртвый путь не вычищали

## Команды

См. `tasks/INVENTORY.md` и `tasks/CHECKS.md`.

Фикстурные раскладки:

```
python scripts/scene_space_plan.py --scene fix:dialogue --out tasks/out/fix-dialogue --from-json tests/fixtures/scene_space/dialogue.json
python scripts/scene_space_board.py --scene fix:dialogue --out tasks/out/fix-dialogue/board.html --from-json tests/fixtures/scene_space/dialogue.json
```

(аналогично `fix:cross`, `fix:turn`).

## Пути артефактов

- Этот отчёт: `tasks/REPORT.md`
- Раунд: `tasks/ROUND-1.md`
- Валидатор: `tasks/VALIDATION.md`
- Раскладки:
  - `tasks/out/fix-dialogue/board.html` + `plan_01.svg`…`plan_10.svg`
  - `tasks/out/fix-cross/board.html` + `plan_01.svg`…`plan_10.svg`
  - `tasks/out/fix-turn/board.html` + `plan_01.svg`…`plan_10.svg`
