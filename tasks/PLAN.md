# PLAN — 5 потоков, владение файлами

База: `housepc`. Ветки: `night/scene-space-1` … `night/scene-space-5`.
Worktree: `.worktrees/stream-N`.

Канон домена: `tasks/CANON.md`. Имена: `tasks/CONTRACTS.md`. Команды: `tasks/CHECKS.md`.

## Поток 1 — миграция и слой доступа

Ветка `night/scene-space-1`. Геометрия, дельты, нормализация пар, вырождение, CRUD.

Владеет:

```
app/services/scene_space/__init__.py
app/services/scene_space/models.py
app/services/scene_space/migrate.py
app/services/scene_space/geom.py
app/services/scene_space/store.py
app/services/scene_space/errors.py
scripts/scene_space_migrate.py
tests/test_scene_space_migrate.py
```

Патч-хуки (только вызов migrate + import models, см. CONTRACTS §5):

```
app/main.py
app/web/api.py
app/project_db.py
```

Не владеет: blocking/validate/render/rewrite, `app/models.py`, `db_apply.py`.

Готово потока: `--up/--down/--up`; store round-trip; `DegenerateAxisError`; normalize_pair; state_at = replay deltas.

## Поток 2 — постановка

Ветка `night/scene-space-2`. Назначение крупностей, ракурсов, сторон, screen_dir, beat_role, служебные кадры, 30°, 180, reestablish, smash, drama charge.

Владеет:

```
app/services/scene_space/shot_size.py
app/services/scene_space/blocking.py
app/services/scene_space/rules.py
```

Читает geom/store (не правит). Не трогает `camera_expand.py`, `scene_shot_grammar.py`.

Публичные функции:

```python
def size_index(code: str) -> int
def size_from_ru(text: str) -> str
def junction_verdict(prev: str, cur: str) -> str  # ok|warn_adj|error_same|warn_smash
def assign_blocking(scene_state: dict, bits: list[dict], frames: list[dict]) -> list[dict]
```

`assign_blocking` возвращает ряды `frames_space` (без записи). Если правило нельзя соблюсти — вставляет служебный ряд (reestablish / turn / pov), не «подгоняет» сторону.

## Поток 3 — отображение

Ветка `night/scene-space-3`.

Владеет:

```
app/services/scene_space/render_plan.py
app/services/scene_space/render_board.py
scripts/scene_space_plan.py
scripts/scene_space_board.py
```

Читает store + вывод валидатора (функция, не файл-владение). Не трогает `web/`, `shots_report.py`.

Детерминированный SVG на кадр; HTML раскладка со сводкой 2.9 и подсветкой `rule_id`.

## Поток 4 — валидатор и property-тесты

Ветка `night/scene-space-4`.

Владеет:

```
app/services/scene_space/validate.py
scripts/scene_space_validate.py
tests/test_scene_space_geom.py
tests/test_scene_space_properties.py
tests/test_scene_space_validator.py
```

Пишет при прогоне: `tasks/VALIDATION.md` (артефакт, не «владение навсегда»).
Не добавляет hypothesis в `pyproject.toml` (stdlib random, 200).
Не меняет чужие тесты.

Если geom ещё нет — тестирует через заявленные сигнатуры CONTRACTS; падение = красный тест, не skip.

## Поток 5 — правка, фикстуры, e2e

Ветка `night/scene-space-5`.

Владеет:

```
app/services/scene_space/rewrite.py
scripts/scene_space_rewrite.py
scripts/scene_space_rebuild.py
scripts/scene_space_e2e.py
tests/test_scene_space_rewrite.py
tests/test_scene_space_e2e.py
tests/fixtures/scene_space/dialogue.json
tests/fixtures/scene_space/cross.json
tests/fixtures/scene_space/turn.json
```

Три сцены по 8–12 кадров:

| id | Смысл | Покрывает |
|----|--------|-----------|
| `fix:dialogue` | диалог двоих, ценность +→− | покрытие 2.9, 3+ крупности, turning_point, reaction |
| `fix:cross` | движение через ось | screen_dir, reestablish, crossing_method |
| `fix:turn` | разворот + smash-вставка | turn кадр, insert, уникальные accent |

Каждый кадр фикстуры: uuid, Frame (voiceover заглушка, image_prompt с PROMPT/NEGATIVE), frames_space ряд. Seed через store + минимальный Project/Scene/Frame в tmp sqlite как в `tests/conftest.py`.

`rewrite` / `rebuild` идемпотентны, `manual=1` жив.

## Таблица владения (один файл — один поток)

| Файл | Поток |
|------|-------|
| `app/services/scene_space/__init__.py` | 1 |
| `app/services/scene_space/models.py` | 1 |
| `app/services/scene_space/migrate.py` | 1 |
| `app/services/scene_space/geom.py` | 1 |
| `app/services/scene_space/store.py` | 1 |
| `app/services/scene_space/errors.py` | 1 |
| `scripts/scene_space_migrate.py` | 1 |
| `tests/test_scene_space_migrate.py` | 1 |
| `app/main.py` | 1 (хук) |
| `app/web/api.py` | 1 (хук) |
| `app/project_db.py` | 1 (хук) |
| `app/services/scene_space/shot_size.py` | 2 |
| `app/services/scene_space/blocking.py` | 2 |
| `app/services/scene_space/rules.py` | 2 |
| `app/services/scene_space/render_plan.py` | 3 |
| `app/services/scene_space/render_board.py` | 3 |
| `scripts/scene_space_plan.py` | 3 |
| `scripts/scene_space_board.py` | 3 |
| `app/services/scene_space/validate.py` | 4 |
| `scripts/scene_space_validate.py` | 4 |
| `tests/test_scene_space_geom.py` | 4 |
| `tests/test_scene_space_properties.py` | 4 |
| `tests/test_scene_space_validator.py` | 4 |
| `app/services/scene_space/rewrite.py` | 5 |
| `scripts/scene_space_rewrite.py` | 5 |
| `scripts/scene_space_rebuild.py` | 5 |
| `scripts/scene_space_e2e.py` | 5 |
| `tests/test_scene_space_rewrite.py` | 5 |
| `tests/test_scene_space_e2e.py` | 5 |
| `tests/fixtures/scene_space/*` | 5 |
| `tasks/INVENTORY.md` `CONTRACTS.md` `CHECKS.md` `PLAN.md` `CANON.md` | оркестратор |
| `tasks/ROUND-N.md` `REPORT.md` | оркестратор |
| `tasks/VALIDATION.md` `tasks/out/**` | артефакты прогона |

## Порядок внутри раунда 1

Потоки 1–5 стартуют параллельно. 2–5 пишут код к сигнатурам потока 1; если store ещё не в их worktree — копируют контракт и работают против интерфейса. После merge оркестратор чинит import-дыры сам.

## Вне скоупа (в отчёт списком, не делать)

- Studio UI / монтажная доска web
- Новая нода канваса
- Excel R* write-through
- Перепись `camera_expand` / grammar
- hypothesis как зависимость
- Push, merge в main
