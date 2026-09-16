# STREAM-1-REPORT

Ветка: `night/scene-space-1`. Worktree: `.worktrees/stream-1`. Push не делался.

## Сделано с доказательствами (команда и её вывод)

Слой `app.services.scene_space`: модели `scenes_space` / `frames_space` (`scene_id TEXT`, колонка `manual`, индекс `idx_frames_space_scene`), миграция `--up/--down`, геометрия (`normalize_pair`, `axis_side`, `apply_deltas`, `DegenerateAxisError` при `cross==0`), store CRUD и `state_at` = `space_json.plan` плюс дельты по `shot_order`. Хуки в `app/main.py`, `app/web/api.py`, `app/project_db.py`: import models до `create_all` и `await migrate_scene_space_schema(conn)` рядом с `migrate_db_v2_schema`.

Команда (worktree, shared venv, `PYTHONPATH` = корень worktree):

```
cd C:\Users\Admin\Desktop\video-pipeline\.worktrees\stream-1
$env:PYTHONPATH = (Get-Location).Path
C:\Users\Admin\Desktop\video-pipeline\.venv\Scripts\python.exe -m pytest tests/test_scene_space_migrate.py -q --tb=short
```

Вывод:

```
...............                                                          [100%]
15 passed in 0.95s
```

Exit code: 0.

Покрытие этих 15 тестов: CREATE TEXT/`manual`/индекс; ALTER `manual` на уже существующей таблице; `--up/--down/--up` не DROP `frames`/`scenes`/`projects`; CLI `scripts/scene_space_migrate.py` через `settings.sqlite_path` (tmp из conftest); хуки в трёх файлах; `normalize_pair`; сторона A/B; `DegenerateAxisError`; facing 0≡360, 90=+X, 270=−X; `apply_deltas` без мутации входа; round-trip store; `state_at` совпадает с replay дельт; KeyError на неизвестной сцене; удаление среднего кадра не ломает replay; `create_all` регистрирует таблицы.

## Не сделано с причиной

- Property-тесты CANON §7 (`tests/test_scene_space_geom.py`, `tests/test_scene_space_properties.py`) — владение потока 4.
- Валидатор, SVG/HTML, rewrite/rebuild/фикстуры — потоки 3–5.
- Подпроцесс CLI против боевого `data/state.db` — тесты обязаны идти в tmp через conftest; CLI проверен in-process на `settings.sqlite_path`.
- Push / merge в `main` / чужие ветки — запрещено заданием.
- Правки `tasks/CONTRACTS.md`, промтов, `app/models.py`, `db_apply.py` — вне скоупа.

## Затронутые файлы

Новые:

- `app/services/scene_space/__init__.py`
- `app/services/scene_space/models.py`
- `app/services/scene_space/migrate.py`
- `app/services/scene_space/geom.py`
- `app/services/scene_space/store.py`
- `app/services/scene_space/errors.py`
- `scripts/scene_space_migrate.py`
- `tests/test_scene_space_migrate.py`
- `tasks/STREAM-1-REPORT.md`

Хуки (только import models + `await migrate_scene_space_schema(conn)`):

- `app/main.py`
- `app/web/api.py`
- `app/project_db.py`

## Запросы на чужие файлы и контракты

Нет блокирующих запросов. Контрактные сигнатуры реализованы в указанных PLAN-файлах и реэкспортированы из `store.py` / пакета, чтобы импорт `from app.services.scene_space.store import axis_side` тоже работал.

Уточнения для чужих потоков (менять CONTRACTS не требуется):

- `get_scene_space` возвращает распарсенный объект `space_json` или `None`.
- `state_at` возвращает копию `space_json` с полем `plan` после replay; полные копии плана в `frames_space` не пишутся.
- `axis_side(a_xy, b_xy, cam_xy)` считает ось как `b-a`; нормализуйте пару через `normalize_pair` до передачи координат.
- Доп. хелпер `geom.facing_vector` (не в замороженном списке): 0=+Y по часовой.

## Что сломалось

Ничего. Первый прогон pytest: 15 passed, повторных ошибок не было.
