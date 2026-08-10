# Scene Design: хронология текста + динамика в многокадровых сценах

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перестроить `scene_design` так, чтобы сцены шли по смыслу и хронологии закадра, внутри сцены было несколько кадров с видимой динамикой развития, а camera/world/characters/style обслуживали этот план, а не ломали его.

**Architecture:** Action задаёт **сцены + фазы действия** (хронология текста). Camera отвечает **на каждую фазу** ракурсом/движением (не независимым 65-shot_plan). Assemble склеивает 1:1 фаза→кадр (или фаза→кластер SET только если фаза явно «establishing»). `camera_expand` дробит только по фазам action, а не по слепой лестнице SET.

**Tech Stack:** Python 3.11, `app/services/scene_design/*`, промпты `prompts/scene_design/*.md`, pytest, project #59 `doch-1-3` как регрессия.

## Global Constraints

- Сохранить модель: **сцена ≠ VO-ячейка**; к одной VO-ячейке может быть много кадров; сцена многокадровая, если тайминг = озвучке отрезка (см. `.cursor/rules/scene-design-model.mdc`).
- Текст VO **не резать** и не разбрасывать по шотам.
- Цель UX: динамика + хронологическое следование тексту; запрет «спины массовки / симметрия / пустой коридор» без сюжетной причины.
- Не пушить в `main` без явной команды пользователя на этом ПК, если `.env` = `main`; иначе ветка `ORCHESTRATOR_GIT_BRANCH`.
- Промпты в `prompts/` gitignored — дублировать шаблоны в `templates/` если нужны в git.

---

## Проблема (as-is на #59)

1. Camera — закон (`assemble.md`), action фактически игнор → пустые SET-лестницы.
2. Action 11 сцен vs camera 65 shot_plan → разные нарезки одного текста.
3. `camera_expand` делает VLS→MS→CU на каждую VO-ячейку → 3 скучных статичных «варианта», не фазы истории.
4. Цепь action накладывается не на свой VO → чужие действия/люди в кадре.
5. Рефы персонажей путаются (ГГ vs массовка, спины вместо лица в ключевых битах).

## Целевая модель (to-be)

```
VO текст (хронология)
    ↓
ACTION: scenes[] с start/end_words + цепь_действия[фаза…]  (закон СМЫСЛА)
    ↓
CAMERA: на каждую фазу → 1 shot (крупность/движение/композиция)  (закон ВИЗУАЛА фазы)
    ↓  world/characters/style — паспорта, не границы
ASSEMBLE: сцена = отрезок VO; кадры = фазы; id_scene/фаза согласованы
    ↓
camera_expand: только если shot явно SET с N фазами И фазы уже перечислены action
    ↓
img_pr / img: 1 герой в кадре по умолчанию; массовка только если фаза требует
```

**Динамика:** каждая следующая фаза меняет информацию (кто/что/где/состояние), не только крупность.  
**Хронология:** `start_words…end_words` сцен стык-в-стык покрывают весь закадр; фазы внутри сцены идут в порядке текста.

---

## File map

| Файл | Роль |
|------|------|
| `prompts/scene_design/action.md` | Сцены + фазы; запрет абстракций; плотность фаз ~2–4 с на фазу |
| `prompts/scene_design/camera.md` | Вход = scenes+фазы action; выход = shot на фазу; SET опционален |
| `prompts/scene_design/assemble.md` | Приоритет: VO → action → camera → world/style/characters |
| `prompts/scene_design/characters.md` / `world.md` / `style.md` | Явные слоты под фазы (кто в кадре, зона локации) |
| `app/services/scene_design/chronology.py` | Привязка фаз к VO-offset; валидация покрытия |
| `app/services/scene_design/camera_expand.py` | Expand по фазам action, не по слепой лестнице |
| `app/services/scene_design/assembler.py` | Валидация: фаза↔кадр, запрет orphan SET |
| `app/services/scene_design/cells.py` / `runner.py` | Контракт JSON фаз; порядок агентов (action до camera) |
| `app/orchestrator/steps/scene_design.py` | Порядок фаз пайплайна; ответные файлы |
| `app/services/img_pr_batches.py` / img prompts | Anti-crowd / 1 hero / face when beat needs face |
| `tests/test_scene_design_*.py` | Регрессии хронологии и expand |

---

### Task 1: Контракт данных «сцена → фазы → шоты»

**Files:**
- Modify: `app/services/scene_design/cells.py`
- Modify: `app/services/scene_design/agents.py` (порядок/зависимости)
- Create/Modify: `tests/test_scene_design_phase_contract.py`

**Interfaces:**
- Produces: нормализованная структура фазы  
  `{scene_id, phase_index, action_text, vo_quote?, duration_hint_sec?}`  
  и shot `{scene_id, phase_index, крупность, движение, композиция, угол, набор?}`

- [ ] **Step 1:** Зафиксировать JSON-схему в docstring/`cells.py` (поля фазы обязательны).
- [ ] **Step 2:** Тест: slice action без `цепь_действия` → reject; camera shot без `phase_index` → reject.
- [ ] **Step 3:** Реализовать валидацию при записи ячеек.
- [ ] **Step 4:** Commit: `fix(scene_design): контракт фаз action↔camera`.

---

### Task 2: Промпт ACTION — хронология + динамические фазы

**Files:**
- Modify: `prompts/scene_design/action.md` (+ template copy если нужно)
- Test: ручная проверка на фрагменте VO #59 (первые ~30 сек) через существующий GPT-path или фикстуру

- [ ] **Step 1:** Переписать правила:
  - сцены стык-в-стык по всему закадру (не 11 «толстых» сцен на весь ролик без покрытия — либо меньше сцен но полное покрытие, либо больше мелких);
  - фаза = видимое изменение (запрет повторов «идут спиной» 3 раза);
  - число фаз ≈ `время_сек_сцены / 2.5` (clamp 2–6);
  - явно: кто субъект фазы (id персонажа / предмет / пустое пространство).
- [ ] **Step 2:** Добавить anti-patterns: симметричная массовка, спины без причины, поза «посередине лестницы» без бита.
- [ ] **Step 3:** Прогнать на куске VO #59 offline (скрипт/один вызов) — сохранить эталон JSON в `tests/fixtures/scene_design/`.
- [ ] **Step 4:** Commit промпт/template.

---

### Task 3: Промпт CAMERA — обслуживает фазы, не режет текст заново

**Files:**
- Modify: `prompts/scene_design/camera.md`
- Modify: `app/services/scene_design/context_builder.py` (класть scenes_chrono+фазы во вход camera)

- [ ] **Step 1:** Camera **не** строит свой независимый shot_plan по цитатам с нуля; вход — `scenes`+`цепь_действия` от action.
- [ ] **Step 2:** 1 фаза → 1 shot; SET только если фаза marked `establishing` и action дал подфазы.
- [ ] **Step 3:** Обязательные поля динамики: движение ≠ `static` минимум на 40% фаз сцены (или явный `hold` с причиной).
- [ ] **Step 4:** Запрет «три спины + ГГ» / равные интервалы фигур без action-фазы «группа».
- [ ] **Step 5:** Тест context_builder: camera prompt содержит phase ids.
- [ ] **Step 6:** Commit.

---

### Task 4: Перевернуть приоритет ASSEMBLE

**Files:**
- Modify: `prompts/scene_design/assemble.md`
- Modify: `app/services/scene_design/assembler.py` (validate_payload)

**Новый приоритет:**
1. Дословность VO (start/end)
2. **Action scenes + фазы** (смысл и хронология)
3. Camera shots, привязанные к `phase_index`
4. World / style / characters

- [ ] **Step 1:** Переписать § приоритет в `assemble.md` (убрать «camera закон»).
- [ ] **Step 2:** Валидатор: каждый op имеет `id_scene` + `phase_index`; число ops в сцене = числу фаз (после expand — см. Task 5).
- [ ] **Step 3:** Тест на фикстуре рассинхрона action/camera → assemble fail с понятной ошибкой.
- [ ] **Step 4:** Commit.

---

### Task 5: `camera_expand` — только по фазам

**Files:**
- Modify: `app/services/scene_design/camera_expand.py`
- Modify: `tests/test_camera_expand*.py` (или создать)

- [ ] **Step 1:** Убрать/отключить слепое «VLS→MS→CU из SET → всегда 3 кадра».
- [ ] **Step 2:** Expand: для VO-родителя создать N child frames = N фаз сцены, покрывающей этот VO (или фазы, чьи vo_quote попадают в VO-ячейку).
- [ ] **Step 3:** VO-текст остаётся только у родителя; детям — `shot01_action` = текст фазы, camera attrs с фазы.
- [ ] **Step 4:** Тест: 1 VO + 4 фазы → 1 parent + 3 children (или 4 frames total per текущей модели insert) с правильными actions.
- [ ] **Step 5:** Commit.

---

### Task 6: Characters / world — слоты в фазах

**Files:**
- Modify: `prompts/scene_design/characters.md`, `world.md`
- Modify: `app/services/scene_design/assembler.py` / apply attrs
- Modify: footer/`img_pr` constraints (1 hero, anti-clone)

- [ ] **Step 1:** У фазы поле `subjects: [c01]|[prop]|[]`; assemble пишет в attrs кадра.
- [ ] **Step 2:** img_pr: если subjects=[c01] — ровно 1 реф героя; запрет «4 фигуры по краям».
- [ ] **Step 3:** Лицо ГГ обязательно на фазах с эмоцией/решением (MCU+); спина — только если фаза `orientation: away` и есть сюжетная причина.
- [ ] **Step 4:** Тест attrs + unit на prompt footer.
- [ ] **Step 5:** Commit.

---

### Task 7: Порядок раннера и хронология

**Files:**
- Modify: `app/services/scene_design/runner.py`
- Modify: `app/services/scene_design/chronology.py`
- Modify: `app/orchestrator/steps/scene_design.py`

- [ ] **Step 1:** Гарантировать: characters/world/style ∥ ; **action → camera** (camera ждёт action cells); assemble последним.
- [ ] **Step 2:** `chronology.attach`: фазы получают vo_offset; проверка покрытия 100% закадра.
- [ ] **Step 3:** Лог/отчёт `ops/sd_chrono_report.json`: сцена → фазы → кадры (для Studio/агента).
- [ ] **Step 4:** Тесты chronology coverage.
- [ ] **Step 5:** Commit.

---

### Task 8: Регрессия на #59 (первые 5 сцен) + критерии приёмки

**Files:**
- Create: `scripts/scene_design_smoke_report.py` (или переиспользовать dump)
- Manual: Studio re-run scene_design на клоне/#59 после бэкапа

**Acceptance (первые ~15 кадров / 5 сцен):**
- [ ] Сцены по VO идут подряд без прыжка смысла (action scene_N соответствует цитате camera/кадров).
- [ ] Внутри сцены фазы разные: не 3× «идут спиной».
- [ ] ГГ узнаваем и не путается с массовкой; реф один на фазу с героем.
- [ ] Есть динамика (движение камеры или смена действия между соседними кадрами).
- [ ] Нет обязательной скучной лестницы SET без сюжетной нужды.
- [ ] Отчёт `sd_chrono_report.json` читаем человеком.

- [ ] **Step 1:** Backup `project.xlsx` + meta scene_registry.
- [ ] **Step 2:** Reset/re-run scene_design → img_pr → точечно img на первые сцены.
- [ ] **Step 3:** Человеческий просмотр; список дефектов → hotfix промптов.
- [ ] **Step 4:** Commit итогового фикса + короткая запись в `docs/AGENT_MAP.md` (1 абзац pointer).

---

## Порядок внедрения

1 → 2 → 3 → 4 → 5 → 6 → 7 → 8  

Блоки 2–3 можно параллелить после контракта (Task 1).  
Медиа (#59 img) не трогать массово, пока smoke первых сцен не зелёный.

## Вне скоупа (отдельно)

- Полный re-gen всех 180 картинок/видео #59.
- Vision-check нода после images (дизайн был раньше, не в этом плане).
- Смена провайдера Outsee/модели.

## Риски

| Риск | Митигация |
|------|-----------|
| Больше фаз → больше кадров → дороже img/video | clamp фаз; сначала smoke на 5 сценах |
| Старые проекты со старым registry | версия контракта в meta; migrate или re-run scene_design |
| Промпты gitignored | дубли в `templates/excel_gpt_agents/` / `templates/scene_design/` |
