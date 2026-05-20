# Массовое создание видео — как это работает

Документ для русскоязычного пользователя. Описывает массовый режим (`/mass` в
боте) и его связь с одиночным режимом.

> **Главный принцип:** массовый режим запускает **те же самые** step-функции
> из `app/orchestrator/steps/*.py`, что и одиночный. Разница только в том,
> **кто принимает решение** между шагами: в одиночном — человек кнопками в
> Telegram, в массовом — GPT-проверка или auto-rules (`auto_mode=True`).
>
> Если массовый «делает что-то не так, чего одиночный не делает» —
> это **баг**, а не by-design.

---

## 1. Как работает массовый режим (одним взглядом)

```
                      ┌─────────────────────────────┐
                      │  Telegram /mass             │
                      │  ⚙ Настройки массовой       │
                      │  📝 Темы (topics.xlsx)      │
                      │  📦 Постоянный продукт      │
                      └──────────────┬──────────────┘
                                     │
                                     ▼
                      ┌─────────────────────────────┐
                      │  BatchProject (контейнер)   │
                      │   settings_snapshot,        │
                      │   topics.xlsx, продукт      │
                      └──────────────┬──────────────┘
                                     │ создать N sub-проектов
                                     ▼
                      ┌─────────────────────────────┐
                      │  Sub-проекты в status=new   │
                      │  с auto_mode=True           │
                      └──────────────┬──────────────┘
                                     │
                                     ▼  serial_tick_batches (1 sub за раз)
                      ┌─────────────────────────────┐
                      │  Один sub переводится       │
                      │  new → planning             │
                      └──────────────┬──────────────┘
                                     │
                                     ▼
                      ┌─────────────────────────────┐
                      │  Pipeline (те же шаги, что  │
                      │  в одиночном)               │
                      └──────────────┬──────────────┘
                                     │
                                     ▼ *_running → step.run() → *_ready
                      ┌─────────────────────────────┐
                      │  maybe_auto_advance         │
                      │   (auto-approve / GPT)      │
                      └──────────────┬──────────────┘
                                     │ approved → следующий *_running
                                     ▼
                      ┌─────────────────────────────┐
                      │  следующий шаг этого sub'a  │
                      └─────────────────────────────┘
                                     ...
                      когда sub дошёл до published/failed:
                      serial_tick_batches берёт следующий new и так далее.
```

### Где в коде

| Слой                     | Файл                                                |
|--------------------------|-----------------------------------------------------|
| Создание/настройка batch | `app/services/batches.py`                           |
| Меню массового           | `app/telegram/mass_menu.py`, `app/telegram/bot.py`  |
| Очередь sub'ов           | `app/orchestrator/auto_advance.py::serial_tick_batches` |
| Auto-approve sub'ов      | `app/orchestrator/auto_advance.py::maybe_auto_advance` |
| Шаги пайплайна           | `app/orchestrator/steps/*.py` (общие с single)      |
| Pipeline-роутер          | `app/orchestrator/pipeline.py::advance_project`     |

---

## 2. Соответствие single ↔ mass (таблица)

Здесь каждая строка — один шаг пайплайна, а колонки — как он выглядит в
одиночном и в массовом режимах. Step-функции одни и те же, отличается только
триггер перехода между шагами.

| #  | Статус (`*_running`)            | Step-функция                                 | Одиночный (single)                                     | Массовый (mass, `auto_mode=True`)                                  |
|----|----------------------------------|----------------------------------------------|--------------------------------------------------------|--------------------------------------------------------------------|
| 1  | `planning`                       | `steps/make_plan.py`                         | Кнопка ✅/✏ из меню → `plan_ready`                     | После `plan_ready` GPT/auto-rule даёт apply → следующий `*_running` |
| 2  | `scripting`                      | `steps/make_script.py`                       | Аналогично, кнопка                                     | Аналогично, авто-апруф                                             |
| 3  | `splitting`                      | `steps/split_frames.py`                      | Кнопка                                                 | Авто-апруф                                                         |
| 4  | `generating_hero`                | `steps/generate_hero.py`                     | Кнопка ✅/🔁 на каждый hero (1..N) и variation (1..M)  | `_apply_approve` в `auto_advance.py`: если есть ещё hero/variation/excel_id — остаётся в `generating_hero`, иначе двигается дальше. **Logика идентична `bot.py:5717-5811`.** |
| 5  | `generating_items`               | `steps/generate_items.py`                    | Кнопка                                                 | Авто-апруф                                                         |
| 5a..5e | `enriching_1..5`             | `steps/enrich_xlsx.py`                       | Кнопка из меню → `enrich_N_ready`                      | `auto_advance` смотрит `enrich_slots_count`: если `N == cap` — прыгает в `generating_image_prompts`, иначе следующий enrich |
| 6  | `generating_image_prompts`       | `steps/generate_image_prompts.py`            | Кнопка                                                 | Авто-апруф                                                         |
| 7  | `generating_images`              | `steps/generate_images.py`                   | Кнопка                                                 | Авто-апруф (GPT-vision если `auto_review_kinds` содержит `approve_images`) |
| 8  | `generating_animation_prompts`   | `steps/make_animation_prompts.py`            | Кнопка                                                 | Авто-апруф                                                         |
| 9  | `generating_videos`              | `steps/generate_videos.py`                   | Кнопка                                                 | Авто-апруф (GPT-vision если `auto_review_kinds` содержит `approve_videos`) |
| 10 | `generating_audio`               | `steps/generate_audio.py`                    | Кнопка                                                 | Авто-апруф                                                         |
| 11 | `assembling`                     | `steps/assemble.py`                          | Кнопка                                                 | Авто-апруф (GPT-vision если `auto_review_kinds` содержит `approve_final`) |
| 12 | `publishing`                     | `steps/publish.py`                           | Кнопка                                                 | Авто-апруф / финал                                                 |

**Auto-mode НЕ исключает GPT-vision.** Если в массовых настройках включена
проверка для какого-то `approve_kind` — auto-mode перед апрувом дёргает
GPT-vision, и решение приходит от GPT.

---

## 3. Куда писать промпт каждого шага

Промпты лежат в `prompts/` (для одиночного) и в `data/batches/<slug>/prompts/`
(snapshot для массового — копируется при создании batch'а). У каждого шага
своя папка:

| #  | Шаг                       | Папка промпта                       | Файл по умолчанию |
|----|---------------------------|-------------------------------------|-------------------|
| 1  | План                      | `prompts/01_plan/`                  | `default.md`      |
| 2  | Сценарий                  | `prompts/02_script/`                | `default.md`      |
| 3  | Разбивка на кадры         | `prompts/03_razbivka/`              | `default.md`      |
| 4  | Hero (персонажи)          | `prompts/04_hero/` + `04_hero_style/` | `default.md`    |
| 5  | Items (вспомогат. вещи)   | `prompts/04b_items/`                | `default.md`      |
| 5a | Enrich slot 1             | `prompts/05a_enrich_1/`             | `default.md`      |
| 5b | Enrich slot 2             | `prompts/05b_enrich_2/`             | `default.md`      |
| 5c | Enrich slot 3             | `prompts/05c_enrich_3/`             | `default.md`      |
| 5d | Enrich slot 4             | `prompts/05d_enrich_4/`             | `default.md`      |
| 5e | Enrich slot 5             | `prompts/05e_enrich_5/`             | `default.md`      |
| 6  | Image prompts             | `prompts/06_image_prompts/`         | `default.md`      |
| 7  | Картинки                  | (генератор, не GPT-промпт)          | —                 |
| 8  | Animation prompts         | `prompts/07_video_prompts/`         | `default.md`      |
| 9  | Видео                     | (генератор)                         | —                 |
| 10 | Аудио / TTS               | (генератор)                         | —                 |
| 11 | Финал (assemble)          | (нет GPT)                           | —                 |
| GPT-vision: план          | `prompts/check_plan/`               | `default.md`      |
| GPT-vision: сценарий      | `prompts/check_script/`             | `default.md`      |
| GPT-vision: hero          | `prompts/check_hero/`               | `default.md`      |
| GPT-vision: images        | `prompts/check_images/`             | `default.md`      |
| GPT-vision: videos        | `prompts/check_videos/`             | `default.md`      |
| GPT-vision: финал         | `prompts/check_final/`              | `default.md`      |

**Логика выбора файла:** если в проекте указан `prompt_overrides[step]` —
используется именно этот файл, иначе `default.md`.

**Массовый режим:** для batch'а используется snapshot
`data/batches/<slug>/prompts/`. Изменение основной папки `prompts/` после
создания batch'а не действует на этот batch.

---

## 4. Настройки массовой (меню `⚙ Настройки массовой`)

Открывается из `/mass` → `📁 <название>` → `⚙ Настройки шаблона`.

Сохраняются в `BatchProject.settings_snapshot["mass_settings"]` (JSON).
Перед стартом очереди значения копируются в sub-проекты со статусом `new`
(в колонки или в `meta`). In-flight sub'ы НЕ переписываются.

| Параметр                       | Тип / диапазон | Где живёт в sub'е                          | Что делает |
|--------------------------------|----------------|--------------------------------------------|------------|
| `auto_mode`                    | bool           | `Project.auto_mode`                        | Если ВЫКЛ — sub НЕ авто-апрувится, ждёт ручных кнопок. |
| `enrich_slots_count`           | int 1..5       | `Project.enrich_slots_count`               | Сколько enrich-слотов будет пройдено перед `generating_image_prompts`. |
| `hero_count`                   | int 1..5       | `Project.hero_count`                       | Кол-во разных персонажей. |
| `hero_variations`              | int 1..5       | `Project.hero_variations` (list)           | Сколько вариаций каждого героя. Применяется одинаково ко всем. |
| `excel_hero_enabled`           | bool           | `Project.meta["excel_hero_enabled"]`       | Включить чтение персонажей из xlsx. |
| `bgm_enabled`                  | bool           | `Project.meta["mass_bgm_enabled"]`         | Фоновая музыка под финал. |
| `bgm_level`                    | int 0..100     | `Project.meta["mass_bgm_level"]`           | Громкость BGM (%). |
| `pause_minutes`                | int 0..1440    | `Project.meta["mass_pause_minutes"]`       | Пауза между двумя sub-проектами (в минутах). |
| `max_parallelism`              | int (фикс. 1)  | `Project.meta["mass_max_parallelism"]`     | Пока всегда 1 (serial worker). Если поднять >1 — `serial_tick_batches` всё равно запустит только 1 sub. |
| `auto_review_kinds`            | list[str]      | `Project.meta["auto_review_kinds"]`        | Для каких visual-шагов включена GPT-vision проверка. Пусто — все картинки/видео auto-approve. Возможные значения: `approve_hero`, `approve_images`, `approve_videos`, `approve_final`. |

**Постоянный продукт** (`📦 Продукт`) хранится в `batch.meta["permanent_product"]`
и копируется в `project.meta["permanent_product"]` всех sub'ов в безопасных
статусах (см. parity #7).

---

## 5. FAQ — частые проблемы

### «Нет промтов в подпроекте»
**Симптом:** `data/batches/<slug>/<sub_slug>/prompts/` пустая или промпт-файл
не найден.

**Причина:** при создании batch'а должен скопироваться snapshot из основного
`prompts/`. Если он не скопировался — батч был создан до того, как папка с
промптами появилась, ИЛИ её удалили вручную.

**Лекарство:** удалить batch (`/mass` → `🗑 Удалить`) и создать заново.
ИЛИ скопировать `prompts/*` в `data/batches/<slug>/prompts/` вручную.

### «Очередь висит — sub не стартует»
**Симптом:** статус batch'а `running`, но `serial_tick_batches` не двигает
sub из `new` в `planning`.

**Причины и лекарства:**
1. Текущий sub застрял в `*_ready` без `auto_mode` — нажми ✅ в его меню или
   включи `auto_mode` в настройках массовой.
2. Текущий sub в `paused` / `failed` — `🔄 Вернуть paused в очередь`.
3. Активен другой batch (serial worker запускает не более 1 sub'а **на
   все batch'и**) — поставь второй на паузу.

### «Sub пропускает варианты hero»
**Симптом:** при `hero_count=2` или `hero_variations=2` массовый сразу
прыгает в `generating_image_prompts`, не догенерив остальные.

**Причина:** старая логика `_apply_approve` не повторяла per-hero/per-variation
loop из `bot.py`. **Починено в Block A (`single-mass parity #1, #2`).**

**Лекарство:** убедись что у sub'а в БД `hero_count` и `hero_variations`
поставлены правильно (это копируется из настроек массовой при старте
очереди). Если sub был создан до того как ты поменяла настройки — он
сохранил старые значения и надо либо удалить sub и пересоздать тему,
либо вручную обновить `hero_count` и `hero_variations`.

### «Enrich крутит до 5 слотов, хотя я хотел 2»
**Симптом:** sub проходит `enriching_1..5` подряд, хотя
`enrich_slots_count=2`.

**Причина:** старый `auto_advance` не уважал `enrich_slots_count`.
**Починено в Block A (`single-mass parity #3`).**

**Лекарство:** убедись что у sub'а `Project.enrich_slots_count = 2`. Это
копируется из `mass_settings` при старте очереди.

### «GPT-vision всегда апрувит, нужны строгие проверки»
По умолчанию `auto_review_kinds=[]` → все visual-шаги авто-апрувятся
без GPT-vision. В настройках массовой включи нужные галочки
(`hero`/`images`/`videos`/`final`).

### «Постоянный продукт обновился, но старые sub'ы его не подхватили»
По дизайну: продукт переписывается только в sub'ах в статусах
`new..splitting` (`splitting_ready` и далее уже опасно — кадры уже
именованы под старый продукт). Sub'ы из `batch.meta["product_late_subs"]`
сохранены как ID — посмотри их и при необходимости пересоздай вручную.

### «`/mass` крашится при старте бота»
Проверь `python -c "import app.telegram.bot"`. Если ImportError — то
`mass_menu.py` или `batches.py` сломаны (например, не хватает функции,
которую импортирует `bot.py`).

---

## 6. Что вызывает что (для отладки)

```
maybe_auto_advance(session, project, bot)         # auto_advance.py
  ├─ if project.status.endswith("_ready") and project.auto_mode:
  │    ├─ _apply_approve(...)                      # auto_advance.py
  │    │    ├─ hero parity (Block A #1, #2)
  │    │    ├─ enrich_slots_count cap (Block A #3)
  │    │    └─ hide HITL buttons (Block A #5)
  │    └─ set project.status = next *_running
  └─ pipeline.advance_project(...) — next call worker

serial_tick_batches(session)                       # auto_advance.py
  └─ выбирает batch=running с минимальным batch_position
     sub-проекта в status=new и переводит его в planning
     (с TOCTOU-проверкой — Block A #6)
```

---

## 7. Где смотреть детали

- Полный flow одиночного: `app/orchestrator/pipeline.py::advance_project`
- Полный flow auto-mode: `app/orchestrator/auto_advance.py::maybe_auto_advance`
- HITL-логика (callback'и кнопок ✅/🔁/❌): `app/telegram/bot.py`
- Hero parity: `app/orchestrator/steps/generate_hero.py`
- Settings menu: `app/telegram/mass_menu.py::mass_settings_kb`,
  обработчики `mass:settings:`, `mass:tog:`, `mass:setnum:` в `app/telegram/bot.py`
