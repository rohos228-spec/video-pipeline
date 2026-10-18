# CONTRACTS — что нельзя менять параллельно

После старта раундов этот файл правит **только оркестратор**.

## 0. Жёсткий периметр

- Ветка база: `housepc`. `main` / `origin/main` не трогать. Push нет.
- Промты сцен и img_pr **не редактировать** (`prompts/scene_design/**`, `prompts/05_image_prompts/**`, `prompts/05_excel_gpt/sd_skeleton.md`, `prompts/blocks/**` кроме чтения forbidden).
- Не менять `FIELD_ALIASES` и allowlist нод, кроме явного разрешения оркестратора ниже.
- Не добавлять ноду в `node_registry.py` / `auto_advance.py`.
- Один файл — один поток (таблица в `tasks/PLAN.md`). Нет пересечений.
- Контракты и имена ниже — заморожены. Нужно иное — запрос в отчёте потока, оркестратор правит этот файл.

## 1. Имена полей БД (новые таблицы)

`scenes_space`

| Поле | Тип | Канон |
|------|-----|--------|
| `scene_id` | TEXT PK | см. §1.1 |
| `space_json` | TEXT NOT NULL | JSON object, ключи §1.2 |

`frames_space`

| Поле | Канон значений |
|------|----------------|
| `uuid` | TEXT PK = `frames.uuid` (24 hex) |
| `scene_id` | TEXT FK → `scenes_space.scene_id` |
| `shot_order` | INTEGER ≥ 1 внутри сцены |
| `axis_pair` | `"{lo}\|{hi}"` или NULL |
| `axis_side` | `A` \| `B` \| NULL |
| `screen_pos` | JSON-строка `{"id":"L"|"R"|"C",...}` |
| `screen_dir` | `L→R` \| `R→L` \| `to camera` \| `from camera` \| `none` |
| `shot_size` | `EWS`\|`WS`\|`FS`\|`MWS`\|`MS`\|`MCU`\|`CU`\|`ECU` |
| `angle_v` | одно из 2.4 |
| `angle_h` | одно из 2.4 |
| `beat_role` | `beat`\|`turning_point`\|`reaction`\|`insert`\|`establish`\|`reestablish` |
| `crossing_method` | `pov`\|`reestablish`\|`overhead`\|`camera_move` \| NULL |
| `space_delta_json` | JSON object дельт, не полный план |
| `reestablish_due` | 0 \| 1 |
| `manual` | 0 \| 1 (доп. к SQL ТЗ, раздел 5) |

Индекс: `idx_frames_space_scene (scene_id, shot_order)`.

Не класть коды EWS в `Frame.attrs["крупность"]`. Не писать `shot_size` через apply-ops (алиас уже занят: → русская `крупность`).

### 1.1 `scene_id`

Текст. Не сырой `str(scenes.id)` без префикса (коллизии между проектами).

- Привязка к Scene пайплайна: `p{project_id}:s{scenes.id}` пример `p13:s4`
- Фикстуры: `fix:dialogue`, `fix:cross`, `fix:turn`

### 1.2 `space_json` ключи (заморожены)

```json
{
  "plan": [{"id": "c01", "x": 0.0, "y": 2.0, "facing": 0}],
  "obstacles": [{"id": "wall1", "x": -1, "y": 0, "w": 0.2, "h": 3}],
  "axes": [{"pair": ["c01", "c02"], "side_locked": "A"}],
  "value_charge": {"start": "+", "end": "-"},
  "input_hash": "<sha256 hex>",
  "monotony_surface": "ground"
}
```

`plan[].id`: персонаж/объект внимания/`cam`. `facing` как в каноне.
`axes[].pair` всегда нормализован (сортировка строк).

Дельта кадра:

```json
{"c02": {"x": 0.6, "y": 2.8, "facing": 180}, "cam": {"x": 1.0, "y": -1.2, "facing": 0}, "turn": {"subject": "c02"}}
```

Только изменившиеся поля. Удаление с плана: `{"c03": {"_remove": true}}`.

## 2. Apply-ops — не расширять в раунде 1

Запись кадров пайплайна по-прежнему `db_apply.apply_ops`.
Служебные кадры: `db_v2.insert_frame_after` + `new_frame_uuid`, затем строка `frames_space`.

Запрещено добавлять в `FIELD_ALIASES`: `axis_side`, `axis_pair`, `screen_pos`, `screen_dir`, `angle_v`, `angle_h`, `beat_role`, `crossing_method`, `reestablish_due` — пока оркестратор не снимет замок. Иначе сломается allowlist нод.

Слой доступа пространства: только `app/services/scene_space/store.py`. Сигнатуры заморожены:

```python
async def migrate_scene_space_schema(conn) -> None
async def downgrade_scene_space_schema(conn) -> None
async def upsert_scene_space(session, scene_id: str, space: dict) -> None
async def upsert_frame_space(session, row: dict) -> None
async def get_scene_space(session, scene_id: str) -> dict | None
async def list_frame_spaces(session, scene_id: str) -> list[dict]
async def state_at(session, scene_id: str, shot_order: int) -> dict
def normalize_pair(a: str, b: str) -> tuple[str, str]
def axis_side(a_xy, b_xy, cam_xy) -> str  # "A"|"B", raises DegenerateAxisError
def apply_deltas(plan: list[dict], deltas_in_order: list[dict]) -> list[dict]
```

`DegenerateAxisError` — единственное имя вырождения `cross==0`.

## 3. Формат вывода нод / CLI

- CLI не печатают GPT-простыни. Код выхода 0/1/2 как в CANON §6.
- JSON для логов: `{"scene_id", "changed": {"inserted":[], "updated":[], "skipped":[]}}`
- SVG: viewBox стабильный от bbox плана + 2м поля. Цвета: ось stroke black; сторона A fill rgba green 0.15; сторона B fill rgba orange 0.15; cam треугольник. Детерминизм: сортировка id, никаких datetime в SVG.
- HTML раскладки: один файл, charset utf-8, нарушения `class="err"` / `class="warn"` + текст `rule_id` из валидатора.

`rule_id` заморожены: `side_lock`, `side_cross_silent`, `screen_pos`, `screen_dir`, `size_same`, `size_adjacent`, `size_smash`, `angle_30`, `reestablish`, `degenerate`, `pair_norm`, `missing_uuid`, `schema`, `prompt_len`, `prompt_forbidden`, `prompt_style`, `interest_*`, `no_turn`.

## 4. Стилевые блоки и негатив (валидатор читает, не пишет)

Позитив: `Frame.image_prompt` и/или `animation_prompt`.
Негатив считается присутствующим, если в том же тексте есть маркер (регистр-insensitive): `NEGATIVE PROMPT` или строка `NEGATIVE:`.
Стилевой блок: есть хотя бы один из маркеров `PROMPT:` / `PROMPT ` / `Стиль:` / `Style:`.
Лимит символов: `OUTSEE_PROMPT_MAX_CHARS` из `app.generation_options` (сейчас 4900) на каждый из image_prompt, animation_prompt.
Запрещённые слова позитива: строки из `prompts/blocks/forbidden_phrases/ai_cliches_ru.md` (фразы в «ёлочках» и после «Не использовать штампы:»). Сравнивать lowercase, только **позитив до маркера NEGATIVE**.

Фикстуры обязаны иметь короткий валидный промт с PROMPT + NEGATIVE, без штампов, < 4900. Это не генерация сцен — заглушка для проверки валидатора.

## 5. Точки интеграции (крошечный патч, поток 1)

Рядом с `migrate_db_v2_schema(conn)` добавить `await migrate_scene_space_schema(conn)`:

1. `app/main.py` (`_init_db`)
2. `app/web/api.py` (`_lifespan`)
3. `app/project_db.py` (создание project.db)

И import моделей до `create_all`, иначе таблиц не будет:

```python
from app.services.scene_space import models as _scene_space_models  # noqa: F401
```

в тех же трёх местах **или** один импорт в `app/models.py` внизу файла. Предпочтение: **не** трогать `app/models.py`. Импорт в `migrate.py` и в трёх хуках.

Другие потоки эти три файла не трогают.

## 6. Существующие имена, которые нельзя переопределить

| Имя | Владелец | Конфликт |
|------|---------|----------|
| `shot_size` в apply-ops | `db_apply.FIELD_ALIASES` → `крупность` | коды EWS только в `frames_space.shot_size` |
| `Scene.id` | INTEGER PK | не PK пространства |
| `Frame.uuid` | 24 hex | PK `frames_space` |
| `accent` | `Frame.attrs["accent"]` | валидатор 2.9 читает отсюда; дублировать в space не надо |
| `session_scope` | `app.db` | использовать его, не плодить engine |
| пакет `app.services.scene_design` | scene_design | не импортировать ради правок; read-only если нужно VO |

## 7. Git / worktree

Ветки потоков: `night/scene-space-1` … `night/scene-space-5` от `housepc`.
Каталог: `.worktrees/stream-N` (gitignore).
Коммит только своих файлов. Сообщение: `feat(scene-space): stream N — …`.
Не stash чужого. Не merge main.

## 8. Приоритет при конфликте правил

1. Этот файл (контракт имён).
2. `tasks/CANON.md` (домен).
3. Существующий пайплайн (`db_apply`, промты).
4. Код потока.

Если канон требует правки промта — **не править промт**. Обойти в коде слоя. Запрос в отчёте.
