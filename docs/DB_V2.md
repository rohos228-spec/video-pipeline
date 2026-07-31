# DB v2 — гибкая модель данных (карточки · дробный порядок · связи)

> **Статус:** DB v2 — **источник правды** для правок через чат/GPT и кнопку
> «База». Excel (`project.xlsx`) — **производный view**: обновляется кнопкой
> «Экспорт в Excel» и автоматически после любых правок в «Базе»/через
> `apply-ops` (write-through). Расхождение «в Excel одно, в DB другое»
> недопустимо: писатель всегда один — DB.
>
> **Читать первым** любому агенту, который трогает хранение кадров, вставку
> кадров между сценами, версии промтов или кнопку «База» в Studio.

---

## 1. Зачем

Старая модель — Excel-центричная: адрес данных = «строка 48 листа план»
(R48), связи — текст в ячейках, персонажи — колонки c01..c05, промт в ячейке
затирается при регенерации. Боли:

- нельзя вставить кадр между сценами без сдвига всех строк и перенумерации;
- нельзя добавить «доп. текст» к кадру — нет такой колонки;
- история промтов теряется;
- строгая валидация листов ломает sync на старых книгах.

DB v2 решает это тремя идеями:

1. **Стабильный uuid у каждой карточки** — адрес не плывёт при вставках.
2. **Дробный порядок `sort_key`** — вставка между = среднее соседей,
   без перенумерации остальных.
3. **Связи и сущности — таблицами** (`frame_edges`, `entities`),
   а не текстом/колонками.

Excel остаётся **внешним видом** (view): экспорт из v2-таблиц по кнопке
«Экспорт в Excel» (`POST /api/db/projects/{pid}/export-xlsx`) и write-through
после правок. Прямой правки Excel в обход DB больше быть не должно — иначе
две правды и баги.

---

## 2. Схема (SQLite, `data/state.db`)

Новые таблицы (`app/models.py`, создаются `create_all`):

| Таблица | Назначение | Ключевые колонки |
|---------|-----------|------------------|
| `scenes` | Сцены-«папки» для кадров | `project_id`, `sort_key` (float), `title`, `place`, `meaning`, `scene_type`, `attrs` JSON |
| `frame_texts` | Тексты кадра любых типов | `frame_id`, `kind` (`voiceover`/`extra`/`note`/…), `text`, `sort_key` |
| `prompt_versions` | Версии промтов, история не затирается | `frame_id`, `kind` (`img`/`video`/`hero`), `version` int, `text`, `is_active` |
| `entities` | Персонажи/фоны/предметы строками | `project_id`, `type` (`character`/`background`/`prop`), `code` (`c01`/`f01`/`p01`), `name`, `attrs` JSON, `sort_key` |
| `frame_edges` | Связи-«ниточки» между кадрами | `from_frame_id`, `to_frame_id`, `type` (`next`/`continues`/`references`) |

Новые колонки в существующей `frames` (ALTER-миграция):

| Колонка | Тип | Смысл |
|---------|-----|-------|
| `uuid` | VARCHAR(64) | Стабильный адрес карточки (24 hex) |
| `sort_key` | FLOAT | Дробный порядок внутри проекта |
| `scene_id` | INTEGER FK → scenes | Сцена, к которой привязан кадр |

Старые колонки (`number`, `voiceover_text`, `image_prompt`, …) **не
удалены** — legacy-код продолжает их использовать. `number` сохраняет
unique constraint (`project_id`,`number`); на порядок отображения v2 он не
влияет.

### Дробный порядок: математика

`app/services/db_v2.py::sort_key_between(before, after)`:

- пустой список → `10.0`; шаг `_SORT_STEP = 10.0`;
- в начало → `first - 10`; в конец → `last + 10`;
- между → `(before + after) / 2`.

Backfill из legacy: `sort_key = number * 10` (10, 20, 30…).

---

## 3. Миграция и backfill

**SoT:** [`app/services/db_v2.py`](../app/services/db_v2.py).

Точки вызова (обе! иначе Studio-only старт колонки не добавит):

1. `app/main.py::_init_db()` — после `create_all`;
2. `app/web/api.py::_lifespan` — после `create_all`.

`migrate_db_v2_schema(conn)` — `PRAGMA table_info(frames)` + ALTER ADD
COLUMN для отсутствующих (идемпотентно, паттерн скопирован с миграции
`projects`).

`backfill_project_v2(session, project)` — идемпотентно заполняет v2 из
legacy-данных проекта:

1. одна сцена по умолчанию «Сцена 1», если нет ни одной;
2. кадрам без `uuid`/`sort_key`/`scene_id` — заполнить;
3. `voiceover_text` → `frame_texts(kind="voiceover")` (если ещё нет);
4. `image_prompt`/`animation_prompt` → `prompt_versions` v1 active
   (kinds `img`/`video`);
5. цепочка `frame_edges(type="next")` по текущему порядку (если связей нет).

Также вызывается лениво при `GET /api/db/projects/{id}/graph` — то есть
открытие проекта в «Базе» само дозаполняет v2.

**Проверено на реальной базе** (копия `data/state.db`, 2026-07-31):
17 проектов, 1938 кадров — uuid/sort_key/тексты/промты/связи созданы без
потерь.

---

## 4. REST API

**SoT:** [`app/web/routers/db_browser.py`](../app/web/routers/db_browser.py),
префикс `/api/db`, регистрация в `app/web/api.py`.

| Метод | Путь | Что делает |
|-------|------|-----------|
| GET | `/api/db/overview` | Все проекты + счётчики (frames/scenes/entities/edges) |
| GET | `/api/db/projects/{pid}/graph` | Полный граф: project + scenes + frames(тексты/промты/edges) + entities; перед ответом — backfill |
| PATCH | `/api/db/frames/{fid}` | Правка status/duration/meaning/voiceover/attrs |
| POST | `/api/db/projects/{pid}/frames/insert` | **Вставка кадра между**: body `{after_frame_id, scene_id}`; sort_key = среднее соседей, ниточка next перекидывается |
| POST/DELETE | `/api/db/frames/{fid}/texts`, `/api/db/texts/{id}` | Доп. тексты кадра |
| POST | `/api/db/frames/{fid}/prompts` | Новая версия промта (version+1, активная) |
| POST | `/api/db/prompts/{id}/activate` | Сделать версию активной (остальные off) |
| POST/PATCH/DELETE | `/api/db/projects/{pid}/entities`, `/api/db/entities/{id}` | Сущности (character/background/prop) |
| POST/DELETE | `/api/db/frames/{fid}/edges`, `/api/db/edges/{id}` | Связи между кадрами (между проектами запрещено — 400) |
| POST | `/api/db/projects/{pid}/scenes` | Новая сцена (дробный sort_key, `after_scene_id`) |
| POST | `/api/db/projects/{pid}/export-xlsx` | **Экспорт DB → project.xlsx** (R45/R46/R48/R64/R49 + R50/R15, backup в `old/`) |
| POST | `/api/db/projects/{pid}/apply-ops` | **Fail-closed запись от GPT**: JSON `{ops:[{frame_uuid, fields:{...}}]}`; неизвестный uuid/пустые fields → 400, откат; после записи — авто-экспорт в xlsx (`export_xlsx=false` отключает) |

Бизнес-логика вставки/версий/графа — в `app/services/db_v2.py`
(`insert_frame_after`, `add_prompt_version`, `project_graph`).

---

## 5. UI: кнопка «База» в Studio

**SoT:** [`web/src/components/baza/baza-workspace.tsx`](../web/src/components/baza/baza-workspace.tsx).

- Кнопка «База» в topbar: `web/src/components/shell/topbar.tsx`
  (иконка `Database`, event `studio-open-baza`).
- В header «Базы» — кнопка **«Экспорт в Excel»** (`api.dbExportXlsx`):
  пишет DB в `project.xlsx` и показывает, сколько кадров/ячеек записано.
  Любая правка в «Базе» (статус, закадр, промты, тексты, сцены, вставка
  кадра) после сохранения автоматически делает тот же экспорт
  (write-through), поэтому `project.xlsx` не отстаёт от DB.
- Listener + state: `web/src/app/page.tsx` (паттерн как у `GptWorkspace`);
  в `BazaWorkspace` передаётся **текущий выбранный проект пайплайна**
  (`selectedProjectId`) — отдельного выбора проекта слева нет.
- Полноэкранный overlay `fixed inset-0 z-[80]`, две колонки:
  - **сцены → карточки кадров** открытого проекта (номер кадра = колонка
    Excel `номер+2`, русский статус, маркеры заполненности R45/R48;
    hover-кнопка «+» = вставить кадр после этого);
  - **детали карточки**: статус/длительность, блок **«Excel-строки кадра»**
    (read-only: R45/R46/R48/R64/R49/R50/R15, персонажи R8/23/38, предметы
    R9/24/39 — читается из `project.xlsx` листа «план»), закадр/смысл,
    тексты (add/del), версии промтов (add/activate), связи (add/del),
    вкладка «Сущности». Все подписи и кнопки — на русском; английские
    enum-значения (status/kind/type) переведены в UI-маппингах.
- `GET /api/db/projects/{pid}/graph` дополнительно возвращает
  `excel_rows: {frame_number: {column, r45_image_prompt, r46_image_prompt_2,
  r48_video_prompt, r64_video_prompt_2, r49_voiceover, r50_duration,
  r15_timecode, persons, items}}` — мостик на период, пока Excel остаётся
  источником правды для промтов (см. §7).
- API-методы фронта: `api.db*` в `web/src/lib/api.ts` (типы `DbGraph`,
  `DbExcelRow` и др.).

После любых правок `web/src/**`: `python scripts/bump_studio_version.py`
(бамп + rebuild `web/out/`, коммитить оба).

---

## 6. Тесты

[`tests/test_db_v2.py`](../tests/test_db_v2.py) — 6 тестов:

- backfill создаёт сцену/uuid/sort_key/тексты/промты/edges + идемпотентность;
- вставка между кадрами: sort_key=15.0, соседи не тронуты, ниточка
  `1 → new → 2` перекинута;
- вставка в начало/конец;
- версии промтов: v1 не затёрта, активна одна;
- `project_graph` возвращает вложенную структуру;
- `sort_key_between` края.

```bash
python -m pytest tests/test_db_v2.py -q
```

Известные **предсуществующие** падения полного прогона (~38, не связаны с
DB v2): `test_assembly_sync`, `test_outsee_retry`, `test_storage_node` и др.
Доказано: падают и на чистом HEAD в основном репо — причина среда
(реальный `.env`/data/: тесты делают настоящие сетевые вызовы Outsee API).
На чистом checkout без `.env` проходят.

---

## 7. Контракт GPT ↔ DB (источник правды)

GPT/чат **не пишет в Excel напрямую**. Запись идёт только через
`POST /api/db/projects/{pid}/apply-ops` — fail-closed JSON по uuid:

```json
{
  "ops": [
    {
      "target": "frame",
      "frame_uuid": "a1b2c3d4e5f6a7b8c9d0e1f2",
      "fields": {
        "закадр": "…",
        "промт_картинки": "…",
        "промт_видео": "…",
        "длительность": 3.2
      }
    },
    {
      "target": "project",
      "fields": { "общий_план": "Эпизод 1 …" }
    }
  ],
  "export_xlsx": true
}
```

**Поля можно писать по-человечески** — сервер сам переводит в канонические
ключи (`_FIELD_ALIASES`): «закадр»→`voiceover_text`, «промт_картинки»→
`image_prompt`, «промт_видео»→`animation_prompt`, «длительность»→
`duration_seconds` и т.д. Канонические ключи тоже принимаются. Неизвестное
поле → `400` со списком разрешённых. Полная таблица — §7.2.

Правила контракта:

1. **Адрес = `frame_uuid`**, не «строка 48». uuid не плывёт при вставках.
2. **Fail-closed**: неизвестный `frame_uuid`, пустой `ops`, пустые `fields`
   у операции → `400`, вся транзакция откатывается, DB не тронута.
3. **`export_xlsx: true` (по умолчанию)** — после записи DB сразу
   экспортируется в `project.xlsx`, поэтому Excel-зависимые шаги (монтаж,
   озвучка) видят те же данные. Расхождения DB/Excel нет по построению.
4. Поля `image_prompt`/`animation_prompt` дополнительно создают активную
   `prompt_versions` (история не затирается); `Frame.image_prompt` /
   `Frame.animation_prompt` всегда равны активной версии (синк в
   `add_prompt`/`activate_prompt`).
5. Коды персонажей `c01` — без изменений (`entities.code`).
6. Содержательные блоки промтов (стиль, палитра, таймлайны) не трогать —
   меняется только транспорт записи.
7. Контекст для модели — `GET /api/db/projects/{pid}/graph`:
   `frames[].uuid` + текущие поля + `excel_rows` (что сейчас в книге).
8. **`target`** — какая нода пишет: `frame` (по умолчанию; кадр, нужен
   `frame_uuid`) или `project` (поля уровня проекта: «общий_план»).
   Неизвестный target → `400`.

### 7.1. Готовый системный блок для агента (копипаст)

```text
Ты редактируешь кадры видеопроекта. Источник правды — база (НЕ Excel).

КОНТЕКСТ: тебе дан граф проекта (GET /api/db/projects/{id}/graph):
frames[] с полями uuid, number, voiceover_text, image_prompt,
animation_prompt, meaning, duration_seconds, attrs + excel_rows (что
сейчас лежит в project.xlsx — справочно).

ОТВЕТ — ТОЛЬКО JSON для POST /api/db/projects/{id}/apply-ops, без прозы,
без markdown, без комментариев:
{"ops":[{"frame_uuid":"<uuid кадра>","fields":{...}}]}

ПРАВИЛА:
1. Адрес кадра — ТОЛЬКО frame_uuid из контекста. Никаких «строка 48»,
   «@row=», «# Лист:», номеров Excel, markdown-таблиц.
2. В fields клади ТОЛЬКО то, что реально меняешь. Пиши по-человечески:
   закадр, промт_картинки, промт_видео, смысл, длительность,
   промт_картинки_2, промт_видео_2. (Канонические voiceover_text,
   image_prompt, animation_prompt, meaning, duration_seconds,
   image_prompt_shot2, animation_prompt_shot2 тоже принимаются.)
3. Пустая строка "" = очистить поле. Поле вне fields = не трогать.
4. Не выдумывай uuid и названия полей: неизвестный uuid или поле →
   весь запрос будет отклонён (400), ничего не запишется.
5. Одна операция = один кадр. Сколько кадров правишь — столько объектов
   в ops. Для полей уровня проекта (общий план) — объект
   {"target":"project","fields":{"общий_план":"…"}}.

ПРИМЕР:
{"ops":[{"frame_uuid":"a1b2c3d4e5f6a7b8c9d0e1f2","fields":{"закадр":"Новый закадр","промт_картинки":"knitted style, …"}}]}
```

После записи backend сам экспортирует DB → `project.xlsx`
(`export_xlsx=true` по умолчанию) — Excel остаётся синхронным view.

### 7.2. Глоссарий: что где означает и кто какой нодой пишется

**Поля кадра (target=frame):**

| Человеческое имя | Канонический ключ | Где в DB | Где в Excel (экспорт) | Кто пишет / читает |
|---|---|---|---|---|
| закадр | `voiceover_text` | `Frame.voiceover_text` | лист «план» R49 | нода split/разбивка; читают озвучка и монтаж |
| промт_картинки | `image_prompt` | `Frame.image_prompt` + активная `prompt_versions(img)` | R45 | нода img_pr; читает генерация картинок |
| промт_картинки_2 | `image_prompt_shot2` | `Frame.attrs["image_prompt_shot2"]` | R46 | нода enrich (shot_02); читает генерация картинок |
| промт_видео | `animation_prompt` | `Frame.animation_prompt` + активная `prompt_versions(video)` | R48 | нода anim_pr; читает генерация видео |
| промт_видео_2 | `animation_prompt_shot2` | `Frame.attrs["animation_prompt_shot2"]` | R64 | нода anim_pr (shot_02); читает генерация видео |
| смысл | `meaning` | `Frame.meaning` | — (только DB) | enrich/оператор; контекст для промтов |
| длительность | `duration_seconds` | `Frame.duration_seconds` | R50 | split/монтаж |

**Поля проекта (target=project):**

| Человеческое имя | Канонический ключ | Где в DB | Где в Excel (экспорт) | Кто пишет / читает |
|---|---|---|---|---|
| общий_план | `general_plan` | `Project.meta["general_plan"]` | лист «Общий план»!B2 | нода plan; контекст script/split |

**Адреса и порядок:** `frame_uuid` — стабильный адрес кадра (не плывёт при
вставках); `sort_key` — дробный порядок (вставка между = среднее соседей);
`number` — legacy-номер = колонка Excel `number+2`. История промтов —
`prompt_versions` (активная всегда равна `Frame.image_prompt`/
`Frame.animation_prompt`). Связи кадров — `frame_edges`; сущности
(персонаж/фон/предмет, коды c01/f01/p01) — `entities`.

---

## 8. Roadmap (следующие шаги, ещё НЕ сделано)

1. ~~Экспорт v2 → project.xlsx~~ — **сделано**: `export-xlsx` + кнопка
   «Экспорт в Excel» + write-through после правок (2026-07-31).
2. **Перевод GPT-чата/нод на `apply-ops`**: контекст из
   `GET /api/db/projects/{pid}/graph`, запись только JSON по uuid (§7) —
   вместо TSV `# Лист:`/`@row=`. Нода за нодой.
3. **Импорт xlsx → v2 по uuid** (вместо адресов строк) — для старых книг.
4. Backfill `entities` из `Project.hero_descriptions`/`item_descriptions`
   (сейчас entities заполняются только через UI/API).
5. Move/reorder кадра и сцены drag&drop в «Базе» (API sort_key уже готов).

---

## 9. Быстрые ссылки

| Что | Файл |
|-----|------|
| Модели (таблицы) | `app/models.py` (`Scene`, `FrameText`, `PromptVersion`, `Entity`, `FrameEdge`, `Frame.uuid/sort_key/scene_id`) |
| Миграция + backfill + операции | `app/services/db_v2.py` |
| REST | `app/web/routers/db_browser.py` |
| UI визуализации | `web/src/components/baza/baza-workspace.tsx` |
| Кнопка | `web/src/components/shell/topbar.tsx` |
| API фронта | `web/src/lib/api.ts` (`api.db*`, типы `Db*`) |
| Тесты | `tests/test_db_v2.py` |
