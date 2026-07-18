# План производства классического сериала на базе video-pipeline

Документ — рабочая спецификация апгрейда.  
Текущий продукт: **вертикальные Shorts 60–75 сек** (закадровый VO, 1 слой, клипы ~8 сек).  
Цель: **вторая продуктовая линия `format=series`** рядом с Shorts, с общим движком генерации (ChatGPT / Outsee / ElevenLabs / FFmpeg).

---

## 1. Целевой продукт

### 1.1. Определение «классического сериала» (MVP → полный)

| Уровень | Формат | Длина эпизода | Эпизодов | Персонажи | Звук |
|--------|--------|---------------|----------|-----------|------|
| **MVP сериал** | 16:9 (или 9:16 mini) | 6–10 мин | 3–5 | 3–5 с диалогами | multi-voice + music |
| **Standard** | 16:9 | 12–22 мин | 6–10 | 5–8 | + ambience/SFX |
| **Full classic** | 16:9 | 22–45 мин | сезон 8–12 | полный cast | stems, ADR, recap, credits |

### 1.2. Главный принцип

Shorts сегодня: **тема → VO → кадры под VO**.  
Сериал: **bible → эпизод → teleplay → сцены → шоты → медиа → монтаж**.

Нельзя «растянуть» текущий `plan → script → split`. Нужен **другой граф** и **новые сущности**, а генераторы картинок/видео/аудио переиспользовать как исполнители.

### 1.3. Два профиля формата (обязательно с первого дня)

| | `shorts` (как сейчас) | `series` (новый) |
|--|----------------------|------------------|
| Единица | `Project` = 1 ролик | `Season` → `Episode` → `Scene` → `Shot` |
| Текст | `voiceover.txt` | teleplay (сцены + реплики) |
| Картинка | 1 кадр ≈ 1 VO-бит | coverage pack на сцену |
| Звук | 1 narrator + 1 BGM | голоса персонажей + beds |
| Граф | `default_graph()` | `default_series_graph()` |
| UI | линейный canvas | season board + episode timeline |

---

## 2. Иерархия производства и взаимосвязи

```text
SERIES / SEASON BIBLE
    │  (тон, мир, арки, табу, cast rules)
    ▼
EPISODE OUTLINE (E01…En)
    │  (acts, cold open, cliffhanger, B-plot)
    ▼
TELEPLAY (screenplay)
    │  (сцены, ремарки, реплики)
    ├──────────────┬────────────────┐
    ▼              ▼                ▼
SCENE BREAKDOWN  CAST/VOICE MAP   CONTINUITY LEDGER
    │              │                │
    ▼              │                │
SHOT LIST / CAMERA │                │
    │              │                │
    ▼              ▼                ▼
IMAGE PROMPTS ──► IMAGES ──► ANIM PROMPTS ──► VIDEOS
    │                                         │
    └──────── DIALOGUE AUDIO / SFX / MUSIC ◄──┘
                        │
                        ▼
                 EPISODE ASSEMBLE
                        │
                        ▼
              QA / SHOWRUNNER HITL
                        │
                        ▼
               RECAP + TITLES + PUBLISH
```

**Правило зависимостей:** вниз по стрелке можно идти только если верхний артефакт **locked** (или явно помечен draft для чернового прогона).  
Иначе continuity и диалоги разъедутся.

---

## 3. Модель данных — что конкретно сделать

### 3.1. Новые сущности (БД + API)

| Сущность | Для чего | Ключевые поля | Где в коде (сделать) |
|----------|----------|---------------|----------------------|
| `Series` | Зонтик франшизы | title, logline, format_profile | `app/models.py`, миграция Alembic |
| `Season` | Сезон / мини-сезон | number, bible_md, status, style_lock_id | то же |
| `Episode` | Единица выпуска | season_id, number, title, outline, teleplay, duration_target, status | то же; связь 1:1 или 1:N с `Project` на переходный период |
| `Scene` | Драматическая сцена | episode_id, idx, location_id, summary, objective, emotional_turn, day_night | новое |
| `Shot` | Съёмочный план | scene_id, idx, shot_type, camera, action, dialogue_line_ids, duration_est | новое; позже мапится на Frame/Artifact |
| `CharacterBible` | Сквозной персонаж сезона | name, bio, arc, appearance_lock, voice_id, relationships_json | расширить hero |
| `LocationBible` | Локация сезона | name, description, ref_artifact_ids, lighting_rules | новое |
| `ContinuityEntry` | Факт continuity | scope (season/ep/scene), key, value, established_at | новое |
| `DialogueLine` | Реплика | scene_id, character_id, text, emotion, timing_hints | новое |
| `FormatProfile` | shorts vs series | aspect, episode_length, graph_template | settings / JSON |

**Переходный хак (не как финал):** `Episode` → внутренний `Project` для переиспользования Outsee/TTS.  
**Цель:** Shot/Dialogue стали first-class, а не «Frame с VO».

### 3.2. Артефакты на диске (предложение)

```text
data/series/<series_slug>/
  season_01/
    bible.md
    characters/
    locations/
    continuity.json
    episodes/
      e01/
        outline.md
        teleplay.md
        scenes/
          s01/
            shots/
            images/
            videos/
            audio/
        mix/
        final/
```

### 3.3. Конкретные задачи по данным

1. Добавить модели + Alembic migration.  
2. API: CRUD series/season/episode/scene/shot.  
3. Studio UI: season board (список эпизодов + статусы).  
4. Не ломать текущий shorts `Project` — feature flag `FORMAT_SERIES_ENABLED`.

---

## 4. Каталог функций (агентов): зачем, как применять, связи, вариации

Ниже каждая функция = **нода/агент** в series-графе.  
Формат карточки:

- **Зачем**  
- **Вход → выход**  
- **Когда запускать**  
- **Как применять (оператору)**  
- **Связи**  
- **Вариации**  
- **Что сделать в продукте**

---

### F01. Series / Season Bible Agent

**Зачем:** единый «закон мира». Без bible каждый эпизод будет как отдельный short с новыми правилами.

**Вход → выход**  
- Вход: логлайн, жанр, тон, число эпизодов, каст (имена), табу, референсы.  
- Выход: `season.bible_md` + черновики Character/Location list + arc map (A/B/C).

**Когда:** один раз в начале сезона; правки только через showrunner HITL.

**Как применять**  
1. Создать Series + Season.  
2. Заполнить бриф (форма в Studio).  
3. Запустить F01 → получить bible.  
4. HITL: утвердить / править / lock.

**Связи:** блокирует F02–F04; питает continuity (F12), style lock, dialogue voice.

**Вариации**

| Вариант | Когда |
|---------|--------|
| Mini-bible | 3 эпизода, 1 страница |
| Full bible | сезон 8+, отделы: мир / cast / tone / episode map |
| Adaptation bible | из книги/сценария пользователя |

**Сделать:**  
- нода `series_bible`, step_code `ser_bible`  
- промпт `prompts/steps/ser_01_bible/template.md` + blocks  
- HITL `approve_bible`  
- запись в `Season.bible_md`

---

### F02. Character Bible + Cast Lock

**Зачем:** стабильные лица, характеры, голоса; диалоги звучат «по-разному».

**Вход → выход**  
- Вход: bible, список ролей.  
- Выход: CharacterBible × N + задания на hero-refs (переиспользовать `hero`).

**Когда:** сразу после F01; до teleplay желательно lock внешности главных.

**Как применять**  
1. Утвердить список персонажей (main / supporting / guest).  
2. Сгенерировать текстовые карточки.  
3. Прогнать генерацию референсов (существующий `hero` / turnaround).  
4. Назначить `voice_id` (ElevenLabs) на каждого говорящего.  
5. Lock главных перед E01.

**Связи:** → dialogue (F05), images, continuity wardrobe; ← bible.

**Вариации:** 1 протагонист + narrator; ансамбль; «голос за кадром + диалоги».

**Сделать:**  
- расширить hero workflow до season-scoped characters  
- UI: voice casting map  
- запрет смены appearance_lock без showrunner override

---

### F03. Location Bible

**Зачем:** одни и те же места не «плывут» между эпизодами.

**Вход → выход**  
- Вход: bible + outline намёки на локации.  
- Выход: LocationBible + ref images (через items/hero-like image gen).

**Когда:** после F01; уточняется по мере outline эпизодов.

**Как применять**  
1. Извлечь локации из bible/outline.  
2. Сгенерировать plate-рефы (день/ночь при необходимости).  
3. Привязать сцены эпизода к `location_id`.

**Связи:** scene breakdown, image prompts, ambience beds (F14).

**Вариации:** 2–3 локации (chamber drama) vs travel series.

**Сделать:** модель LocationBible + нода `location_bible` + хранение рефов в `data/series/.../locations/`.

---

### F04. Episode Outline Agent

**Зачем:** структура эпизода до диалогов. Иначе teleplay расползается.

**Вход → выход**  
- Вход: locked bible, номер эпизода, целевая длина, статус предыдущего cliffhanger.  
- Выход: `episode.outline` — cold open, акты, A/B plot, mid-point, climax, tag/cliffhanger, список сцен (кратко).

**Когда:** для каждого эпизода отдельно; E(n) после lock финала E(n-1) или хотя бы его cliffhanger.

**Как применять**  
1. Выбрать эпизод на season board.  
2. Запустить outline.  
3. Проверить: есть ли поворот, цена, связка с аркой сезона.  
4. HITL approve outline → lock.

**Связи:** ← bible, prev cliffhanger; → teleplay, scene list seed.

**Вариации**

| Структура | Применение |
|-----------|------------|
| 3 акта | MVP 6–10 мин |
| Teaser + 4 acts + tag | 20–22 мин |
| Bottle episode | мало локаций, упор на диалог |
| Premiere / finale | усиленный cold open / payoff |

**Сделать:**  
- нода `ep_outline`  
- промпт с жестким JSON/Markdown контрактом сцен  
- поле `Episode.outline` + статус `outline_ready`

---

### F05. Teleplay + Dialogue Agent *(ключевой пробел)*

**Зачем:** классический сериал = сцены и реплики, не закадровый VO.

**Вход → выход**  
- Вход: locked outline, character bibles (голос/манера), continuity ledger.  
- Выход: `teleplay.md` + нормализованные `DialogueLine` + scene headers.

**Формат выхода (контракт):**

```text
SCENE 3 - INT. КУХНЯ - НОЧЬ
Цель сцены: ...
Поворот: ...

МАША
Ты снова врёшь.
(пауза, тише)
Я видела сообщение.

ИВАН
(отступает к двери)
Это не то, что ты думаешь.
```

**Когда:** только после approve outline. Не писать диалоги «из головы» без структуры.

**Как применять**  
1. Lock outline.  
2. Запустить teleplay (можно в 2 прохода: skeleton scenes → dialogue pass).  
3. HITL: читать вслух темп; править info-dump.  
4. Парсер кладёт реплики в БД.  
5. Lock teleplay перед breakdown.

**Связи:** → scene/shot breakdown, multi-voice audio, subs; ← cast, continuity.

**Вариации**

| Режим | Когда |
|-------|--------|
| Dialogue-heavy | chamber / drama |
| Visual-heavy | экшен: короткие реплики, больше action lines |
| Hybrid | VO-narrator + диалоги (док-драма) |
| Comedy timing | отдельные блоки пауз/бит |

**Сделать:**  
- **новый** step вместо/рядом с `make_script.py`: `make_teleplay.py`  
- промпты `ser_02_teleplay`, `ser_03_dialogue_polish`  
- парсер teleplay → `Scene` + `DialogueLine`  
- **не** переиспользовать `voiceover_author` как default  
- HITL `approve_teleplay`

---

### F06. Dialogue Polish / Character Voice Pass

**Зачем:** чтобы все персонажи не говорили одним GPT-голосом.

**Вход → выход**  
- Вход: teleplay draft + character speech profiles.  
- Выход: teleplay v2 (отличимые лексика/ритм/длина реплик).

**Когда:** сразу после F05, до breakdown.

**Как применять:** авто-пасс + HITL на 2–3 ключевых сцены.

**Связи:** часть F05 или отдельная нода; влияет на TTS emotion tags.

**Вариации:** soft polish / hard rewrite одной сцены / «убрать exposition».

**Сделать:** нода `dialogue_polish` или режим в teleplay; чеклист в `check_teleplay`.

---

### F07. Scene Breakdown Agent

**Зачем:** превратить сценарий в производственные сцены с целью и участниками.

**Вход → выход**  
- Вход: locked teleplay.  
- Выход: записи `Scene` (location, cast, objective, turn, estimated duration).

**Когда:** после lock teleplay.

**Как применять**  
1. Авторазбор.  
2. Склеить/разрезать слишком длинные сцены.  
3. Проставить day/night и continuity tags.

**Связи:** → shot list; ← locations; обновляет continuity candidates.

**Вариации:** coarse (сцена = локация) vs fine (сцена = смена цели).

**Сделать:** `breakdown_scenes.py` + API списка сцен в UI episode.

---

### F08. Shot List / Coverage / Camera Agent *(логика камеры)*

**Зачем:** классическая съёмка = набор ракурсов, а не один slow push-in на всю сцену.

**Вход → выход**  
- Вход: Scene + dialogue lines + location ref + style lock.  
- Выход: список `Shot`: type, framing, movement, action, linked lines, duration_est.

**Минимальный coverage pack на диалоговую сцену (MVP):**

1. Master / wide establishing  
2. OTS или medium на говорящего A  
3. Reverse / reaction B  
4. Insert (руки/предмет) — опционально  

**Когда:** по каждой сцене после breakdown; можно батчом на эпизод.

**Как применять**  
1. Для сцены выбрать профиль coverage: `dialogue_2shot` / `montage` / `action` / `establishing_only`.  
2. Сгенерировать shot list.  
3. HITL: убрать лишние шоты (экономия генераций).  
4. Lock shot list → image/anim prompts.

**Связи:** заменяет смысл текущего `split` (VO-split); питает img_pr/anim_pr; задаёт assemble order.

**Вариации**

| Профиль | Шоты |
|---------|------|
| `dialogue_2shot` | master + OTS A + OTS B + reaction |
| `single_performer` | wide + CU + insert |
| `action` | wide geography + medium action + impact insert |
| `montage_timejump` | 3–7 inserts без диалога |
| `cold_open_hook` | 1–3 шока/образа + smash to titles |

**Правила камеры (зашить в промпт/валидатор):**  
- движение камеры мотивировано (взгляд, угроза, тайна), не «для красоты»  
- 180° / eye-line для диалогов  
- не менять линзу хаотично внутри бита  
- Veo ≤8s → длинная сцена = несколько шотов/клипов, не один «длинный» промпт-обман

**Сделать:**  
- нода `shot_list` (`cam_cov`)  
- blocks: `camera_coverage_dialogue`, `camera_coverage_action`, …  
- расширить/заменить узкий `camera_motion/slow_push_in` для series  
- UI: shot table внутри сцены  
- маппинг Shot → генерация (см. F09–F11)

---

### F09. Image Prompt Agent (series-aware)

**Зачем:** кадр из shot list + refs персонажей/локации + continuity.

**Вход → выход**  
- Вход: Shot + Character/Location locks + prev shot context.  
- Выход: image prompt на шот (аналог R45, но per-shot).

**Когда:** после lock shot list (можно scene-by-scene).

**Как применять:** как сейчас img_pr, но источник — Shot, не VO-frame; всегда прикладывать hero/location refs.

**Связи:** ← F02/F03/F08; → images; continuity wardrobe/props.

**Вариации:** still plate / keyframe for video / concept only.

**Сделать:** адаптер `generate_image_prompts` series-mode; промпт `ser_06_image_prompts`; писать в Shot.attrs / sheet.

---

### F10. Images generation

**Зачем:** визуальный ключ шота (уже есть движок Outsee).

**Применение:** существующий `images` step, вход = series image prompts + multi-ref attach.

**Связи:** HITL per scene или per episode pack; backup при reset как сейчас.

**Сделать:** batch по `scene_id`; UI галерея сцен; не смешивать shorts Frame API без адаптера.

---

### F11. Animation Prompt + Videos (series-aware)

**Зачем:** движение и «игра» в шоте по camera plan, не универсальный push-in.

**Вход → выход**  
- Вход: approved image + Shot.camera + action (+ lip-sync policy later).  
- Выход: anim prompt → Veo clip ≤8s.

**Когда:** после approve images сцены (или эпизода).

**Как применять**  
1. Для диалоговых CU — минимум движения камеры, акцент на performance.  
2. Для master — медленный dolly/pan по плану.  
3. Склеивать несколько клипов в сцену на assemble, не пытаться уместить 60с диалога в 1 клип.

**Связи:** ← shot list; → assemble; soft retry как `video`/`anim_pr`.

**Вариации:** lock-off talking head; insert motion; establishing drone/pan; silent reaction hold.

**Сделать:**  
- series шаблоны `ser_07_animation`  
- запрет дефолта «slow push-in everywhere»  
- поле Shot.video_artifact_id

---

### F12. Continuity Steward

**Зачем:** сериал убивает деталь (синяк пропал, пистолет в другой руке, герой «забыл» факт).

**Вход → выход**  
- Вход: bible + teleplay + prior ContinuityEntry + новые сцены.  
- Выход: обновлённый ledger + список нарушений (block/warn).

**Когда:**  
- после teleplay (story continuity)  
- после shot/images (visual continuity)  
- перед assemble (final gate)

**Как применять**  
1. Автоскан → report.  
2. Critical → блок генерации.  
3. Soft → предупреждение HITL.

**Связи:** читает/пишет ContinuityEntry; связан с costume/day-night.

**Вариации:** story-only / visual-only / full.

**Сделать:**  
- `continuity_steward.py`  
- JSON ledger на сезон  
- check-ноды `check_continuity_story`, `check_continuity_visual`

---

### F13. Multi-voice Dialogue Audio

**Зачем:** без этого диалоги на экране = субтитры под музыкой.

**Вход → выход**  
- Вход: DialogueLine + character.voice_id + emotion.  
- Выход: audio segments per line (+ silence gaps).

**Когда:** после lock teleplay; можно параллельно с image gen, **до** финального assemble.

**Как применять**  
1. Проверить casting map.  
2. Сгенерировать реплики пакетом.  
3. HITL прослушать сцену.  
4. Retake одной линии (ADR-lite).

**Связи:** ← F05/F02; → mix/assemble; субтитры из DialogueLine (не только Whisper).

**Вариации:** full cast TTS; 1 narrator + 2 voices; silent film + music (исключение).

**Сделать:**  
- расширить `generate_audio.py` series-mode  
- timeline реплик (`start_ms` estimate из shot list)  
- UI retake line  
- **не** писать весь эпизод в один `voiceover.txt`

---

### F14. Sound Design (Ambience + SFX)

**Зачем:** ощущение места и действия; иначе «видеозвонок в пустоте».

**Вход → выход**  
- Вход: Scene.location + action tags из shot list.  
- Выход: ambience bed + SFX cues (файлы/метки таймлайна).

**Когда:** после breakdown; финальный mix перед/внутри assemble.

**Как применять (MVP):** 1 ambience на локацию + 3–10 SFX на эпизод.  
**Full:** spotting sheet по сценам.

**Связи:** location bible; assemble stems.

**Вариации:** silence-driven drama (мало SFX); action-heavy.

**Сделать:** нода `sound_design` (фаза 2); каталог beds; пока можно manual upload.

---

### F15. Music Spotting (не одна BGM на всё)

**Зачем:** сериальная музыка = тема/напряжение/тишина, не бесконечный луп.

**Вход → выход**  
- Вход: outline + teleplay emotional map.  
- Выход: cues: `t_start–t_end`, mood, reference; генерация кусков (Suno/Outsee) или библиотека.

**Когда:** после outline (черновик) и уточнение после teleplay; mix в assemble.

**Как применять:** отметить 4–8 cues на 8–10 мин эпизод; не заливать музыку под весь диалог.

**Связи:** ← F04/F05; → assemble ducking под диалог.

**Вариации:** theme+variations; diegetic source; almost silent.

**Сделать:** расширить `generate_music.py` → cues list; series assemble делает ducking.

---

### F16. Episode Assemble (scene timeline)

**Зачем:** собрать эпизод как фильм, не `concat` VO-frames.

**Вход → выход**  
- Вход: videos по shots + dialogue audio + music cues + SFX + titles.  
- Выход: `episode_final.mp4` (+ stems optional).

**Когда:** когда медиа эпизода ready (или scene-complete iterative).

**Как применять**  
1. Сборка сцены (шоты по cut list).  
2. Наложение диалогов (J/L-cut упрощённо в MVP можно без).  
3. Music ducking.  
4. Subs из DialogueLine.  
5. Titles / end card.  
6. HITL final.

**Связи:** зависит от F08–F15; отличается от `assemble.py` shorts.

**Вариации:** rough cut (только picture+dialog) → fine cut (+sound).

**Сделать:**  
- `assemble_episode.py` (новый)  
- cut list из Shot order  
- не использовать VO alignment mapper как единственный тайминг  
- format profile 16:9

---

### F17. Recap / Titles / Credits

**Зачем:** сериальная упаковка («ранее» / заставка / титры).

**Когда:** после rough cut или параллельно с lock телеplay (текст титров).

**Вариации:** cold open → smash titles; titles first; no recap for E01.

**Сделать:** нода `ep_packaging`; шаблоны длительностей; генерация 1–3 recap шотов из прошлых эпизодов.

---

### F18. Showrunner QA Agent

**Зачем:** приёмка эпизода по чеклисту классики.

**Проверяет:** структура, cliffhanger, длина, отличимость голосов, continuity report, громкость, «есть ли цель у каждой сцены».

**Когда:** перед publish / перед стартом следующего outline.

**Сделать:** `check_episode` по аналогии с `check_plan`/`check_script`, но series-критерии (не 30s hook).

---

### F19. Season Board / Episode Queue (продюсерский контур)

**Зачем:** видеть сезон как производство, а не пачку shorts.

**Применение:** статусы `bible → outline → teleplay → breakdown → media → cut → done`.

**Сделать:** Studio pages + API; переиспользовать HITL kinds новыми типами.

---

### F20. Rerun / Lock policy

**Зачем:** перегенерить 1 сцену, не сжигать сезон.

**Правила:**  
- lock bible/teleplay по умолчанию  
- reset scene = backup медиа сцены (как `old/scenes` для img)  
- soft retry CDP на shot-level  

**Сделать:** `reset_scene` / `reset_shot` API; запрет wipe сезона.

---

## 5. Порядок использования (операционный runbook)

### 5.1. Старт сезона (один раз)

| Шаг | Функция | HITL | Результат |
|-----|---------|------|-----------|
| 1 | Создать Series/Season + format profile | — | контейнер |
| 2 | F01 Bible | approve_bible | locked bible |
| 3 | F02 Characters + hero refs + voices | approve_cast | cast lock |
| 4 | F03 Locations + plates | approve_locations | location lock |
| 5 | Init continuity ledger | — | пустой/базовый ledger |

### 5.2. Каждый эпизод (E01…En)

| Шаг | Функция | Можно параллелить | Стоп-кран |
|-----|---------|-------------------|-----------|
| 1 | F04 Outline | нет | без approve нет teleplay |
| 2 | F05 Teleplay | нет | |
| 3 | F06 Polish | нет | |
| 4 | F12 story continuity | нет | critical blockers |
| 5 | F07 Scene breakdown | нет | |
| 6 | F08 Shot list (все сцены) | сцены между собой да | урезать лишние шоты |
| 7 | F09 Image prompts | по сценам да | |
| 8 | F10 Images | по сценам да | HITL images |
| 9 | F12 visual continuity | после images | |
| 10 | F11 Anim + Videos | по ready-сценам да | soft retry |
| 11 | F13 Dialogue audio | || с 7–10 после teleplay lock | HITL audio |
| 12 | F15 Music cues | после outline/teleplay | |
| 13 | F14 SFX/ambience | после breakdown | (фаза 2) |
| 14 | F16 Assemble rough → fine | нет | |
| 15 | F17 Packaging | частично || | |
| 16 | F18 QA | нет | |
| 17 | HITL final + publish | — | |
| 18 | Export cliffhanger facts → ledger для E+1 | — | |

### 5.3. Чего никогда не делать

1. Писать диалоги до outline.  
2. Генерить картинки до cast/location lock (кроме концепт-тестов).  
3. Один 8s клип на длинную диалоговую сцену.  
4. Одна BGM на весь эпизод без ducking.  
5. Считать mass/batch «сезоном».  
6. Использовать shorts `check_plan` 30s-критерии для серии.

---

## 6. Series-граф (конкретная схема нод)

Предлагаемый `default_series_graph()` (упрощённо):

```text
topic/brief
  → series_bible → HITL_bible
  → cast_bible → hero_refs → voice_cast → HITL_cast
  → location_bible → location_refs → HITL_loc
  → ep_outline → HITL_outline
  → teleplay → dialogue_polish → HITL_teleplay
  → continuity_story
  → scene_breakdown
  → shot_list
  → image_prompts → images → HITL_images
  → continuity_visual
  → animation_prompts → videos → HITL_videos
  → dialogue_audio → HITL_audio
  → music_spotting
  → sound_design          # phase 2
  → assemble_episode → packaging → QA → HITL_final → publish
```

Shorts-граф **не удалять** — переключение по `FormatProfile`.

---

## 7. Фазы внедрения — что конкретно сделать в репо

### Фаза 0 — Каркас (фундамент)

**Сделать:**  
1. `FormatProfile` + флаг `series` в settings/project meta.  
2. Модели Season/Episode/Scene/Shot/DialogueLine/CharacterBible/LocationBible/ContinuityEntry.  
3. Миграции БД.  
4. API CRUD + пустой Season Board в Studio.  
5. `default_series_graph()` stub (ноды-заглушки).  
6. Документ контрактов артефактов (этот файл = v1).

**Критерий:** можно создать сезон и 3 эпизода-карточки без генерации.

### Фаза 1 — Series MVP (уже «мини-сериал»)

**Сделать:**  
1. Промпты+ноды: F01 bible, F04 outline, F05 teleplay, F06 polish.  
2. Парсер teleplay → scenes/lines.  
3. F07 breakdown + F08 shot list (профиль `dialogue_2shot`).  
4. Адаптеры F09–F11 на существующие Outsee steps.  
5. F13 multi-voice audio (минимум 2–3 голоса).  
6. F16 `assemble_episode` (picture + dialogue + 1–N music cues, 16:9).  
7. HITL: bible/outline/teleplay/images/videos/audio/final.  
8. F12 story continuity (упрощённый ledger).  
9. Тесты контрактов парсера и graph validate.

**Критерий приёмки MVP:**  
3 эпизода × 6–8 мин, 3 персонажа, диалоги слышны, cliffhanger E01→E02, одни и те же лица/локации.

**Осознанно НЕ в MVP:** полный Foley, ADR studio, 45-мин эпизоды, сложный VFX, writers room.

### Фаза 2 — Режиссура и звук

**Сделать:**  
1. Расширить coverage профили (action/montage).  
2. F03 location plates day/night.  
3. F14 ambience/SFX + ducking.  
4. F15 music spotting полноценно.  
5. F17 recap/titles.  
6. Visual continuity checks.  
7. `reset_scene` / `reset_shot`.

**Критерий:** эпизод звучит как «шоу», не как озвученный слайдшоу.

### Фаза 3 — Full classic

**Сделать:**  
1. A/B/C plot tracker на сезон.  
2. Costume/day tracker.  
3. ADR retake UX.  
4. Stem export, loudness norms.  
5. Guest cast workflow.  
6. Delivery package (описания, thumbs, captions).  
7. Опционально: control-plane вынос промптов (если нужна защита IP).

---

## 8. Матрица «старый шаг → series»

| Сейчас (shorts) | В series | Действие |
|-----------------|----------|----------|
| `plan` | F01 + F04 | заменить смыслом; не использовать viral_60s |
| `script` | F05 teleplay | новый writer; VO только как variation |
| `split` | F07 + F08 | scene/shot вместо VO split |
| `hero` / `items` | F02 / props | season-scoped |
| `enrich_*` | optional excel tools | не ядро series |
| `img_pr` / `img` | F09 / F10 | вход Shot |
| `anim_pr` / `video` | F11 | camera from shot list |
| `audio` | F13 | multi-voice |
| `music` | F15 | cues |
| `assemble` | F16 | новый timeline |
| `publish` | publish episode | почти как есть |
| mass/batch | season board | не использовать как замену |

---

## 9. Вариации продукта (как выбирать режим)

### V1. Диалоговая драма (приоритет для «классики»)
F05+F06+F08(`dialogue_2shot`)+F13 обязательны; мало экшена.

### V2. Визуальная сага / фэнтези
Усилить F03/F08(`action`)/F11; диалоги короче; больше establishing.

### V3. Док-сериал / true crime
Hybrid: narrator VO (старый skill) + редкие «интервью»-диалоги; проще переход с shorts.

### V4. Вертикальный мини-сериал (Shorts-series)
9:16, 3–5 мин, те же F01–F16 но урезанный coverage (master+CU).

### V5. Антология
Слабый season arc, сильный episode bible каждый раз; continuity ledger тонкий.

**Правило выбора:** сначала зафиксировать **V1 или V3** — от этого зависят промпты фазы 1.

---

## 10. Оценка сложности (техническая, не календарь)

| Блок | Иinvasiveness | Риск |
|------|---------------|------|
| Модели + API + board | средний | низкий |
| Teleplay/dialogue prompts + parser | средний | средний (качество текста) |
| Shot list → генерация | высокий | средний (стоимость генераций) |
| Multi-voice sync assemble | высокий | высокий (тайминг) |
| Full sound design | высокий | средний |
| Не ломать shorts | дисциплина feature flag | регрессии |

Самый жёсткий технический узел: **F16 + F13 (тайминг диалогов к шотам)**.  
Самый жёсткий креативный узел: **F05/F06 (качество диалогов и голосов персонажей)**.

---

## 11. Чеклист первой конкретной реализации (фаза 0→1)

### Backend
- [ ] Модели Season/Episode/Scene/Shot/DialogueLine/…  
- [ ] Миграция Alembic  
- [ ] Routers `/api/series/...`  
- [ ] `make_series_bible.py`, `make_episode_outline.py`, `make_teleplay.py`  
- [ ] `breakdown_scenes.py`, `make_shot_list.py`  
- [ ] Series adapters для image/anim/video/audio  
- [ ] `assemble_episode.py`  
- [ ] HITL kinds + soft retry на shot-level  
- [ ] `default_series_graph()` + validate  

### Prompts
- [ ] `prompts/steps/ser_01_bible/...`  
- [ ] `ser_02_outline`  
- [ ] `ser_03_teleplay`  
- [ ] `ser_04_dialogue_polish`  
- [ ] `ser_05_shot_list` (+ coverage blocks)  
- [ ] `ser_06_image_prompts`, `ser_07_animation`  
- [ ] `check_teleplay`, `check_episode`, `check_continuity_*`  

### Web
- [ ] Season board  
- [ ] Episode page: outline / teleplay editor / scenes / shots  
- [ ] Casting map (voice)  
- [ ] HITL кнопки series  
- [ ] Переключатель format shorts|series  
- [ ] bump Studio version при UI  

### Tests
- [ ] parser teleplay → lines  
- [ ] graph series acyclic + required edges  
- [ ] assemble cut list ordering  
- [ ] regression: shorts `default_graph` не сломан  

---

## 12. Пример одного «правильного» прогона E01 (шпаргалка оператора)

1. Lock bible + cast + locations.  
2. Outline E01 → approve.  
3. Teleplay → polish → approve.  
4. Continuity story (должны появиться факты эпизода).  
5. Breakdown → shot list (`dialogue_2shot`).  
6. Выкинуть лишние inserts вручную.  
7. Image prompts → images → approve.  
8. Videos по шотам.  
9. Начитать диалоги голосами персонажей → approve.  
10. Music cues (4–6).  
11. Assemble rough → поправить тайминги → fine.  
12. Titles → QA → final approve.  
13. Записать cliffhanger в ledger → Outline E02.

---

## 13. Решение для старта (рекомендация)

Зафиксировать продукт фазы 1 так:

- **Format:** 16:9, 6–8 минут, 5 эпизодов  
- **Variation:** V1 диалоговая драма  
- **Coverage:** master + 2 OTS/reaction  
- **Audio:** 3 голоса + music cues, без Foley  
- **Не трогать** shorts pipeline кроме feature flag  

Дальше реализация идёт строго по **§7 Фаза 0 → Фаза 1** и runbook **§5**.

---

## 14. Связь с текущим репо (якоря)

| Тема | Где сейчас |
|------|------------|
| Shorts graph | `app/orchestrator/default_graph.py` |
| Node registry | `app/orchestrator/node_registry.py` |
| Steps | `app/orchestrator/steps/*.py` |
| Models | `app/models.py` |
| VO script | `make_script.py`, `prompts/steps/02_script` |
| Camera blocks (shallow) | `prompts/blocks/camera_*` |
| Anim ≤8s | `prompts/_vars.md`, anim templates |
| Assemble VO | `app/services/assembly.py` |
| Mass ≠ series | `docs/MASS_CREATION.md`, `BatchProject` |
| HITL | `app/models.py` HITLKind, web/telegram routers |

---

*Версия документа: 1.0 — план апгрейда series. Следующий артефакт после утверждения: RFC по схемам таблиц + JSON-контракты outline/teleplay/shot_list.*
