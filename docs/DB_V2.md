# DB v2 — гибкая модель данных (карточки · дробный порядок · связи)

> **Статус:** v1 задеплоена в `main` (commit `fba5059`, 2026-07-31). Работает
> **параллельно** со старой Excel/frames-моделью — пайплайн не сломан,
> v2-таблицы заполняются автоматически при старте бэкенда.
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

Excel остаётся **внешним видом** (view): в будущем — экспорт из v2-таблиц,
импорт по uuid. Пока Excel-пайплайн работает как раньше.

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

Бизнес-логика вставки/версий/графа — в `app/services/db_v2.py`
(`insert_frame_after`, `add_prompt_version`, `project_graph`).

---

## 5. UI: кнопка «База» в Studio

**SoT:** [`web/src/components/baza/baza-workspace.tsx`](../web/src/components/baza/baza-workspace.tsx).

- Кнопка «База» в topbar: `web/src/components/shell/topbar.tsx`
  (иконка `Database`, event `studio-open-baza`).
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

## 7. Переписывание промтов: Excel → DB v2

Меняется **только адресация, не содержание**. Блоки стиля, rule-no-text,
9:16, описания сцен — без изменений.

| Было (Excel) | Стало (DB v2) |
|--------------|---------------|
| «строка 48», R48, `@row=48` | «кадр `<uuid>`» — адрес не плывёт |
| «лист `план`, блок из 30 строк» | карточка кадра с полями |
| колонки c01..c05 | записи `entities` (не ограничено пятью) |
| «связь с кадром 12» текстом в ячейке | `frame_edges`: from → to, type |
| промт в ячейке, затирается | `prompt_versions`: v1, v2… активна одна |
| ответ GPT: `# Лист:` + `@row=` | JSON-операции по uuid |

Правила переписывания промтов:

1. Убрать адреса ячеек: «запиши в строку 48» → «верни для кадра `<uuid>`».
2. TSV-ответ → JSON: `{"frame_uuid": "…", "fields": {"voiceover": "…", "img_prompt": "…"}}`.
3. Коды персонажей `c01` — без изменений (теперь это `entities.code`,
   а не колонка листа «Персонажи»).
4. Содержательные блоки (стиль, палитра, таймлайны) не трогать.

---

## 8. Roadmap (следующие шаги, ещё НЕ сделано)

1. **Экспорт v2 → project.xlsx** (Excel как view): генерация книги из
   `scenes/frames/frame_texts/prompt_versions`, `validate_xlsx_sheets`
   перестанет быть источником правды.
2. **Импорт xlsx → v2 по uuid** (вместо адресов строк).
3. **Перевод GPT-нод на чтение из v2** (`gpt_client`/`excel_gpt_node`) —
   постепенно, нода за нодой; промты переписывать по §7.
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
