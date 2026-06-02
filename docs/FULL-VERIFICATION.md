# Полная проверка Video Studio — инструкция на 6–9 часов

Это **не** то, что делал короткий автоматический прогон (`run-studio-audit.ps1`, 6 e2e-тестов, 12 web-pytest).  
Здесь — **ручная + полуавтоматическая** проверка: **каждая кнопка**, **каждая нода**, **каждый шаг пайплайна**, **каждый тип генерации** (GPT / Outsee / ElevenLabs / FFmpeg / HITL).

Используйте как чеклист: ставьте `[ ]` → `[x]` или `FAIL` + скрин + строка из Network.

**Репозиторий:** `C:\Users\Love Space\video-pipeline` (или AiCreator Desktop — та же ветка `devin/windows-installer`).

---

## 0. Что считается «полной проверкой»

| Уровень | Время | Содержание |
|---------|-------|------------|
| **Минимум (автомат)** | ~15 мин | Guardian + e2e + web pytest — *уже недостаточно* |
| **Стандарт** | ~3 ч | Все кнопки UI + dry_run всех безопасных шагов + 1 нода каждого типа |
| **Полный** | **6–9 ч** | Два QA-проекта + реальные генерации + HITL + стоп/рестарт + полный пайплайн |

**Критерий завершения:** все строки в матрицах ниже отмечены; для `FAIL` — запись в журнал дефектов (раздел 12).

---

## 1. Подготовка окружения (~30 мин)

### 1.1 Сервисы

```powershell
cd "C:\Users\Love Space\video-pipeline"

# Свежий UI + перезапуск бэкенда
powershell -ExecutionPolicy Bypass -File .\apply-local.ps1 -NoBrowser

# В другом окне — автоматический базис (не заменяет ручную часть)
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-studio-audit.ps1 -E2E
.\.venv\Scripts\python.exe -m pytest tests/test_web_api_integration.py tests/test_web_dry_run_step.py tests/test_studio_version.py -q
```

- Студия: http://127.0.0.1:8765 — **Ctrl+F5** (бейдж версии без «старый UI»).
- Outsee: браузер/профиль залогинен, CDP доступен (как в `run-backend` / `.env`).
- ElevenLabs: ключ в настройках проекта.
- ChatGPT: сессия/куки для шагов GPT (plan, script, enrich…).

### 1.2 Два тестовых проекта

Создайте **через UI** («Новый проект»):

| Проект | Slug (пример) | `hero_mode` | Назначение |
|--------|---------------|-------------|------------|
| **QA-SMOKE** | `qa-smoke-YYYYMMDD` | `no_hero` | Быстрые шаги без Outsee hero/items |
| **QA-FULL** | `qa-full-YYYYMMDD` | `hero` или `auto` | Полный пайплайн с персонажами, картинками, видео, озвучкой |

Тема: короткая («Тест QA: 3 кадра про тюрьму»), **3–5 кадров** в split — иначе 9 часов уйдут только на Outsee.

Запишите `id` проектов: QA-SMOKE = ____ , QA-FULL = ____ .

### 1.3 Workflow

- Откройте **QA-FULL** → канвас → **Сохранить граф** после проверки, что в графе есть цепочка:

`topic → plan → script → split → hero → items → enrich_1 → image_prompts → images → animation_prompts → videos → audio → assemble → publish`

- HITL-ноды (`hitl_*`) — по желанию на графе; баннер HITL появляется по данным API, не только по ноде.

### 1.4 Журнал

Файл `docs/QA-RUN-YYYY-MM-DD.md` (копируйте таблицы из раздела 12) или Excel.

---

## 2. Фаза A — Автоматика (обязательный старт, ~20 мин)

| # | Команда / действие | Ожидание |
|---|-------------------|----------|
| A1 | `run-studio-audit.ps1` | All checks passed |
| A2 | `run-studio-audit.ps1 -E2E` | 6 passed |
| A3 | web pytest (см. выше) | 12 passed |
| A4 | `GET /api/projects/steps/catalog` в браузере или curl | 200, у каждого шага есть `label` |
| A5 | Полный `pytest tests/` (опционально) | Зафиксировать 10 known-fail (см. `AUTONOMOUS-AUDIT-REPORT.md`) |

---

## 3. Фаза B — Оболочка: каждая кнопка вне канваса (~45 мин)

Проект **любой** выбран, если не указано иное.

### 3.1 Topbar

| ID | Элемент | Действие | Ожидание | ✓ |
|----|---------|----------|----------|---|
| B-T1 | Промты | Клик без выделенной ноды | Событие / toast / нет краша | |
| B-T2 | Промты | Выделить `plan` на канвасе → Промты | Открывается студия, вкладка промтов | |
| B-T3 | Логи | Открыть панель | Логи текущего проекта, скролл | |
| B-T4 | API | Ссылка | `/api/docs` открывается | |
| B-T5 | StudioVersionBadge | Hover | Нет «ui_stale» после Ctrl+F5 | |

### 3.2 Sidebar (`project-sidebar`)

| ID | Элемент | Действие | Ожидание | ✓ |
|----|---------|----------|----------|---|
| B-S1 | Поиск | Ввести slug QA-FULL | Фильтр списка | |
| B-S2 | Строка проекта | Клик | Канвас загружается, нет Application error | |
| B-S3 | Очередь генерации | Toggle на проекте | Badge/позиция меняется, API 200 | |
| B-S4 | Удалить проект | Только на **копии**-мусоре | Подтверждение, проект исчез | |
| B-S5 | Свернуть панель | Collapse | Иконки, снова развернуть | |
| B-S6 | Новая папка | Создать | Папка в списке | |
| B-S7 | Удалить папку | Пустая папка | Удалена | |
| B-S8 | DnD проект → папка | Перетащить | `folder_id` сохранился после F5 | |
| B-S9 | Новый проект | Wizard | Все поля: topic, hero_mode (auto/hero/no_hero), workflow | |
| B-S10 | Preset в wizard | Сохранить/удалить preset | Без 500 | |

### 3.3 Inspector (правая колонка)

| ID | Элемент | Действие | Ожидание | ✓ |
|----|---------|----------|----------|---|
| B-I1 | Без проекта | — | «Выбери проект» | |
| B-I2 | Проект, нода не выбрана | — | Тема, статус, hero_mode, настройки | |
| B-I3 | Project settings | Смена auto_mode / ai_control | Сохранение, refetch | |
| B-I4 | Topic editor | Изменить тему | В БД обновилось | |
| B-I5 | Кадры preview | «Открыть сетку» | `FramesGrid` modal | |
| B-I6 | Нода выбрана | «Открыть студию ноды» | Node Studio открылась | |

### 3.4 Панель Run (над канвасом, `flow-canvas`)

| ID | Элемент | Действие | Ожидание | ✓ |
|----|---------|----------|----------|---|
| B-R1 | Создать Run | Проект без run | Run создан, ноды pending/… | |
| B-R2 | Перезапустить | Есть run, выбрана нода со step | enabled (в т.ч. после V-menu!) | |
| B-R3 | ⏹ Остановить | Во время `generating_*` | Статус сбрасывается, воркер стоп | |
| B-R4 | Запустить (вторичная) | Из контекста run bar | Шаг стартует для `runStepNodeKey` | |

---

## 4. Фаза C — Toolbar канваса (~30 мин)

| ID | Элемент | Действие | Ожидание | ✓ |
|----|---------|----------|----------|---|
| C1 | + Нода (select) | Добавить `plan`, `videos`, … по одной | Нода на графе, **Сохранить граф** | |
| C2 | Сохранить граф | После правок | toast успех, F5 — ноды на месте | |
| C3 | Копировать / Вставить | 2 ноды, Ctrl+C/V | Дубликаты со смещением | |
| C4 | Дублировать граф ниже | Кнопка с двумя Copy | Копия цепочки ниже | |
| C5 | Excel feed | Кнопка spreadsheet | `excel_feed` слева | |
| C6 | WF | duplicate workflow | Новый workflow в API | |
| C7 | Удалить | Выделение + Delete | Ноды удалены, рёбра сняты | |
| C8 | Соединения | Drag handle → handle | Edge сохраняется | |
| C9 | Крестик на handle | Снять все рёбра стороны | Рёбра удалены | |
| C10 | Marquee / multiselect | Выделить рамкой | Несколько нод selected | |

---

## 5. Фаза D — Матрица: каждая нода (~3–4 ч)

Для **каждой** строки: проект **QA-FULL** (или QA-SMOKE где помечено), нода **на графе**, клик по ноде → V-menu → Studio.

**Коды шагов** (`node-step-map.ts`):  
`plan`, `script`, `split`, `hero`, `items`, `enrich_1..5`, `img_pr`, `img`, `anim_pr`, `video`, `audio`, `assemble`, `publish`.

### 5.1 Легенда колонок

| Колонка | Что проверить |
|---------|----------------|
| **V** | Меню **V** на ноде: все пункты без краша |
| **Studio** | Вкладки: Настройки / Промты / Excel / Результаты |
| **Run** | «Запустить шаг» или Run bar — шаг ушёл в `generating_*` |
| **Артефакт** | Файл/превью/статус ноды → `done` |
| **API** | Network: `POST .../steps/{code}/run` → 200; тело без сырого 500 |

### 5.2 Ноды планирования

| type | label | step | V | Studio | Run | Артефакт | API | ✓ |
|------|-------|------|---|--------|-----|----------|-----|---|
| `topic` | Тема | — | — | редактор темы | — | тема в inspector | PATCH topic | |
| `plan` | Сценарий | plan | ✓ | промты GPT | ✓ | `general_plan` / plan_ready | 200 | |
| `script` | Закадровый | script | ✓ | ✓ | ✓ | script_ready, текст кадров | 200 | |
| `split` | Разбивка | split | ✓ | параметры split | ✓ | N кадров в frames | 200 | |
| `excel_feed` | Excel темы | — | — | upload topics.xlsx | — | связи к plan | upload API | |

### 5.3 Объекты (Outsee / Nano)

| type | step | Проект | Run | Артефакт | ✓ |
|------|------|--------|-----|----------|---|
| `hero` | hero | QA-FULL | ✓ | hero refs, hitl approve_hero? | |
| `items` | items | QA-FULL (если есть предметы в xlsx) | ✓ | item images | |

`no_hero` (QA-SMOKE): hero/items **пропуск** — статус должен перепрыгивать к enrich (см. `project_state`).

### 5.4 Enrich 1–5 (ChatGPT + xlsx)

Для **каждого** `enrich_N`, N=1..5:

| Проверка | ✓ |
|----------|---|
| V → «Просмотр Excel» / excel tab | |
| Загрузка/перезагрузка `project.xlsx` | |
| «Текст для GPT» (если есть) | |
| Run `enrich_N` → `enriching_N` → `enrich_N_ready` | |
| Слот отключён в графе → 400 с понятным текстом | |

### 5.5 Медиа (промты + Outsee)

| type | step | Генератор | Run | Артефакт | ✓ |
|------|------|-----------|-----|----------|---|
| `image_prompts` | img_pr | GPT | ✓ | prompts на кадрах | |
| `images` | img | Outsee | ✓ | scene_image per frame | |
| `animation_prompts` | anim_pr | GPT | ✓ | animation_prompt | |
| `videos` | video | Outsee | ✓ | scene_video mp4 | |

**Обязательные регрессии на `videos`:**

| # | Сценарий | Ожидание |
|---|----------|----------|
| V-REG1 | ⏹ во время video → снова Run | **Нет** повторного F1 если файл уже на диске |
| V-REG2 | Recovery после рестарта бэкенда | Статус `video_generated`, skip в логах |

### 5.6 Аудио и сборка

| type | step | Генератор | Run | Артефакт | ✓ |
|------|------|-----------|-----|----------|---|
| `audio` | audio | ElevenLabs + Whisper | ✓ | voiceover, subs | |
| `assemble` | assemble | FFmpeg | ✓ | final mp4 | |
| `publish` | publish | внешние API | ✓* | *если настроены ключи | |

### 5.7 HITL-ноды (визуальные)

| type | kind (API) | Действия в модалке | ✓ |
|------|------------|-------------------|---|
| `hitl_hero` | approve_hero | Одобрить / переген / отклонить / правка промта | |
| `hitl_images` | approve_images | Галерея, approve all | |
| `hitl_videos` | approve_videos | Галерея клипов | |
| `hitl_final` | approve_final | Просмотр финала | |

Баннер HITL (верх): клик → те же кнопки **Одобрить**, **Переген**, **Отклонить**, **Правка промта**, hotkeys Enter / Ctrl+Enter.

### 5.8 Меню V — один раз на типичной ноде `plan` (повторить выборочно на `videos`)

| Пункт V-menu | ✓ |
|--------------|---|
| Просмотр промтов | |
| Скачать промты (.txt) | |
| Запустить шаг | |
| Файлы и превью (если есть assets) | |
| Открепить связи | |
| Отключить ноду → Run disabled | |
| Включить ноду снова | |
| Удалить ноду | |
| + Промт / удалить кастомный промт | |
| Текст для GPT (violet block) | |

---

## 6. Фаза E — Полный пайплайн по порядку (~2–3 ч)

Проект **QA-FULL**, **auto_mode** по сценарию (сначала manual HITL, потом auto — два прогона или два проекта).

### 6.1 Последовательность шагов (не перескакивать без причины)

```
1. plan          → plan_ready
2. script        → script_ready
3. split         → frames_ready
4. hero          → hero_ready        (пропуск если no_hero)
5. items         → items_ready       (опционально)
6. enrich_1..5   → enrich_*_ready    (только активные слоты в workflow)
7. img_pr        → image_prompts_ready
8. img           → images_ready
9. anim_pr       → animation_prompts_ready
10. video        → videos_ready
11. audio        → audio_ready
12. assemble     → assembled
13. publish      → (если настроено)
```

После **каждого** шага записать:

- `project.status` (UI badge + `GET /api/projects/{id}`)
- Число кадров / артефактов
- Строка в `data\backend.log` без ERROR (или текст ошибки в журнал)

### 6.2 Dry-run (QA-SMOKE, без ботов)

Для шагов **без** Outsee/ElevenLabs:

```powershell
$base = "http://127.0.0.1:8765"
$pid = <QA-SMOKE_ID>
foreach ($code in "plan","script","split","img_pr","anim_pr","assemble") {
  Invoke-RestMethod -Method POST "$base/api/projects/$pid/steps/$code/run?dry_run=true"
}
# Должны быть 400:
foreach ($code in "hero","items","img","video","audio") {
  try { Invoke-RestMethod -Method POST "$base/api/projects/$pid/steps/$code/run?dry_run=true"; throw "fail" }
  catch { if ($_.Exception.Response.StatusCode.value__ -ne 400) { throw } }
}
```

---

## 7. Фаза F — Все виды генераций (сводка)

| Вид | step / node | Где смотреть результат | Мин. время |
|-----|-------------|------------------------|------------|
| GPT текст | plan, script, split, enrich_*, img_pr, anim_pr | Studio → Результаты, xlsx, `general_plan` | 15–40 мин/шаг |
| Hero/Items image | hero, items | Artifacts, HITL hero | 20–60 мин |
| Scene image | img | Media review images, files | 30–90 мин |
| Scene video | video | Media review videos, Outsee logs | 60–180 мин |
| TTS + subs | audio | voiceover.txt, srt | 10–30 мин |
| FFmpeg assemble | assemble | final mp4 | 5–15 мин |
| Publish | publish | meta / внешние площадки | опционально |

**Параллельно не гонять** два тяжёлых Outsee-шага на одном профиле CDP.

---

## 8. Фаза G — Регрессии и краевые случаи (~1 ч)

| ID | Сценарий | Ожидание | ✓ |
|----|----------|----------|---|
| G1 | Run с **отключённой** нодой | 400 / toast, шаг не стартует | |
| G2 | Run во время другого шага | toast «сейчас выполняется…» | |
| G3 | ⏹ → Run тот же шаг | Нет двойной генерации (video!) | |
| G4 | V-menu open → Перезапустить в toolbar | Кнопка **enabled** | |
| G5 | Refetch run (подождать 10 с) | Ноды **не** мигают «Ожидание» без причины | |
| G6 | `hero_mode=no_hero`, видео готовы | status ≠ `frames_ready` | |
| G7 | Mass factory panel | «Запустить очередь» (если используете) | |
| G8 | AI control edge dialog | Сохранение ребра | |
| G9 | Duplicate workflow + другой проект | Вставить ноды Ctrl+V | |
| G10 | Ошибка API | Toast **человекочитаемый**, не `[object Object]` | |

---

## 9. Фаза H — Логи и Network (на всём прогоне)

| Источник | Когда смотреть |
|----------|----------------|
| `data\backend.log` | После каждого Run / ⏹ |
| DevTools → Network | Любой 4xx/5xx на `/api/projects/...` |
| Outsee / CDP окно | video, img, hero |
| Playwright trace | При падении e2e: `web/playwright-report/` |

Шаблон записи дефекта:

```
ID: G3
Экран: канвас, нода videos
Действие: ⏹ → Run
Ожидание: skip F1, clip reused
Факт: повтор F1
API: POST .../steps/video/run 200
Лог: [строка из backend.log]
```

---

## 10. План по времени (9 часов)

| Час | Фаза |
|-----|------|
| 0:00–0:30 | Подготовка (§1) |
| 0:30–0:50 | A автоматика |
| 0:50–1:35 | B оболочка |
| 1:35–2:05 | C toolbar |
| 2:05–5:30 | D матрица нод + реальные Run на QA-FULL |
| 5:30–8:00 | E полный пайплайн 1→13 |
| 8:00–8:45 | F HITL + G регрессии |
| 8:45–9:00 | Журнал, повтор FAIL, e2e |

---

## 11. Быстрые команды (копипаст)

```powershell
cd "C:\Users\Love Space\video-pipeline"
.\STUDIO-AUDIT.cmd
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-studio-audit.ps1 -E2E
.\.venv\Scripts\python.exe -m pytest tests/test_web_api_integration.py -q
cd web && npm run build
```

---

## 12. Журнал дефектов (шаблон)

| ID | Фаза | Экран | Шаг/кнопка | Ожидание | Факт | Severity |
|----|------|-------|------------|----------|------|----------|
| | | | | | | P0/P1/P2 |

---

## 13. Связанные документы

- `docs/AUTONOMOUS-AUDIT-REPORT.md` — что уже прогнали автоматически (не полная проверка).
- `docs/HANDOFF-STUDIO-WEB.md` — красная линия и merge.
- `docs/MASS_CREATION.md` — excel_feed / очередь.

---

## 14. Для агента / следующей сессии

Полная проверка **не автоматизируется за 20 минут**: Outsee/CDP/HITL требуют живого браузера и часов генерации.  
Максимум агента без пользователя:

1. Разделы **A**, **dry-run**, **e2e**, **pytest web**.
2. Расширение e2e по одной строке из §5 (отдельные spec-файлы на ноду).
3. Ручные §5–7 — только человек или агент с **browser MCP** + явным QA-проектом.

**Не закрывать задачу «полная проверка»** пока матрицы §3–§8 не заполнены.
