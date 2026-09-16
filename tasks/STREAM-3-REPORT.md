# STREAM-3-REPORT

Ветка: `night/scene-space-3`  
Worktree: `.worktrees/stream-3`

## Сделано с доказательствами

Детерминированный top-down план на кадр (SVG + PNG через Pillow) и монтажная HTML-раскладка. CLI совпадают с INVENTORY; добавлен `--from-json PATH` для дыма без store/validate.

Публичные точки:

- `app.services.scene_space.render_plan.write_plans(out_dir, space, frames, *, png=True)` → `plan_NN.svg` / `.png`
- `app.services.scene_space.render_board.write_board(path, scene_id, space, frames, violations)`
- `python scripts/scene_space_plan.py --scene SCENE_ID --out DIR [--from-json PATH] [--no-png]`
- `python scripts/scene_space_board.py --scene SCENE_ID --out FILE.html [--from-json PATH]`

SVG (канон §4 / CONTRACTS §3): субъекты + facing, препятствия, линия оси на весь viewBox, заливка запертой стороны (A `rgba(0,128,0,0.15)`, B `rgba(255,165,0,0.15)`), треугольник `cam`, стрелка движения, подписи `shot_order` / `shot_size` / `axis_side`. viewBox общий на сцену = bbox плана+дельт + 2 м. Сортировка id. Нет datetime.

HTML: кадры по `shot_order`; поля size/angles/side/`screen_pos`/`screen_dir`/accent/`beat_role`; нарушения `class="err"` / `class="warn"` + текст `rule_id`; сверху сводка 2.9 (`interest_size_variety`, `interest_unique_accents`, `interest_turning_point`, `interest_reaction`, `interest_monotony`, `interest_geography`, `interest_coverage`). charset utf-8.

Без `--from-json` скрипты импортируют store (plan) и store+validate (board) и выходят с кодом 2 и явным текстом. С `--from-json` store не нужен; validate вызывается если модуль есть, иначе берётся `violations` из дампа.

Дым (venv `.venv`, `PYTHONPATH` = корень worktree, без `pip install -e`):

```
=== ruff ===
All checks passed!
=== plan no json (expect 2) ===
ERROR: cannot import app.services.scene_space.store (stream 1). Pass --from-json PATH with a scene dump to run without DB. Detail: No module named 'app.services.scene_space.store'
exit=2
=== board no json (expect 2) ===
ERROR: cannot import app.services.scene_space.store (stream 1). Pass --from-json PATH with a scene dump to run without DB. Detail: No module named 'app.services.scene_space.store'
ERROR: cannot import app.services.scene_space.validate (stream 4). Pass --from-json PATH with optional 'violations' to run without the validator. Detail: No module named 'app.services.scene_space.validate'
exit=2
=== plan json ===
{"scene_id": "fix:dialogue", "out": "tasks\\out\\fix-dialogue", "svg": ["plan_01.svg", ..., "plan_08.svg"], "count": 8}
=== board json ===
{"scene_id": "fix:dialogue", "out": "tasks\\out\\fix-dialogue\\board.html", "shots": 8, "violations": 4}
=== timestamps in svg? ===
(нет совпадений)
=== files ===
board.html, plan_01.svg … plan_08.svg, plan_01.png … plan_08.png
=== html markers ===
charset=utf-8; все 7 interest_* class=ok; class="warn" на кадрах 2 и 6 (size_adjacent, size_smash); class="err" на кадре 8 (side_lock)
=== determinism ===
svg_diff_count=0
```

Команды:

```
python scripts/scene_space_plan.py --scene fix:dialogue --out tasks/out/fix-dialogue --from-json tasks/out/stream-3-smoke.json
python scripts/scene_space_board.py --scene fix:dialogue --out tasks/out/fix-dialogue/board.html --from-json tasks/out/stream-3-smoke.json
```

Повтор plan → SVG байт-в-байт те же. Артефакты в `tasks/out/` не коммитятся.

## Не сделано

- Живой путь через DB (`get_scene_space` / `list_frame_spaces` / `state_at`) — store потока 1 в этом worktree нет.
- Живой вызов валидатора потока 4 — `validate.py` нет; на дампе работают локальная сводка 2.9 + поле `violations`.
- CHECKS для `fix:cross` / `fix:turn` — фикстуры потока 5 отсутствуют.
- PNG без Pillow (в deps есть; при сбое PNG молча пропускается).
- Studio / `web/` / `shots_report.py` / промты — вне скоупа.

## Затронутые файлы

Владение потока 3:

- `app/services/scene_space/render_plan.py`
- `app/services/scene_space/render_board.py`
- `scripts/scene_space_plan.py`
- `scripts/scene_space_board.py`

Отчёт: `tasks/STREAM-3-REPORT.md`

Не создавался `app/services/scene_space/__init__.py` (поток 1). Импорт идёт как namespace package.

## Запросы

1. Зафиксировать `--from-json PATH` в INVENTORY/CHECKS (сейчас только у потока 3, нужен оркестратору для дыма до merge 1/4/5).
2. Поток 4: ожидаемая функция `validate_scene(scene_id, space, frames)` (также пробуются `validate_scene_space` / `run_validation` / `check_scene` / `validate`). Возврат — list[dict] с `rule_id`, `level` (`error`/`warn`), `message`, опционально `shot_order`/`uuid`; либо объект с `.violations` / `.issues`.
3. `interest_*` в сводке 2.9 (если поток 4 назовёт иначе — подсветит кадры, но строки сводки останутся этими семью id).
4. Поток 1: `get_scene_space` → dict `space_json` (или обёртка с ключом `space_json`); `list_frame_spaces` → ряды как в CONTRACTS; `state_at` → list plan или `{"plan": [...]}`. Дельты кадра — объект, не полная копия плана. `apply_deltas` используется если импортируется, иначе локальный fold.
5. `accent` для доски: поле ряда или `Frame.attrs["accent"]` при живом store.

## Что сломалось

1. Первый прогон board без `--from-json` сообщал только об отсутствии store, не validate. Исправлено: `_missing_live_deps()` проверяет оба модуля, код 2, две строки ERROR.
2. ruff: B009 на `getattr(..., "violations")`, SIM102/SIM105, E402 из-за `sys.path`. Починено; повторный `ruff check` по четырём файлам — чисто.
3. Сводка 2.9 на первом дампе помечала `interest_monotony` fail: три подряд `beat`/`turning_point` сжались в `action`. Это корректная проверка, не баг рендера. В дымовом JSON роль кадра 2 сменена на `establish`, после этого все семь критериев ok.
4. `from app.services.scene_space import validate` на namespace package без файла давал «cannot import name». CLI переведены на `importlib.import_module("app.services.scene_space.validate")` / `.store`.
