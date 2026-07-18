# План производства классического сериала (индустриальная версия)

Спецификация апгрейда **video-pipeline** под формат `series`.  
Документ переработан по профессиональным источникам TV/кино (см. §0) и привязан к тому, что уже есть в репо (Shorts-граф, Outsee, ElevenLabs, FFmpeg).

**Текущий продукт:** вертикальные Shorts 60–75 сек, закадровый VO, 1 слой, клипы ~8 сек.  
**Цель:** вторая линия `format=series` с индустриальной последовательностью отделов, а не «растянутый short».

---

## 0. Источники (профессиональная база)

| Тема | Источники |
|------|-----------|
| Жизненный цикл сериала (dev → finance → production → distribution) | [Vitrina: TV Series Lifecycle](https://vitrina.ai/blog/the-tv-series-lifecycle-a-complete-guide-from-concept-to-screen-in-2025/), [TV Show Development 2025](https://vitrina.ai/blog/the-tv-show-development-process-a-2025-insiders-guide/) |
| Show bible (структура, уровни) | [Final Draft: 10 Steps to TV Show Bible](https://www.finaldraft.com/blog/10-easy-steps-to-developing-your-tv-show-bible), [AIScriptReader: Bible Format Template](https://aiscriptreader.com/blog/screenwriting/tv-show-bible-format-template-and-examples) |
| Writers’ room / showrunner | [Final Draft: Who’s in a TV Writers Room](https://www.finaldraft.com/blog/whos-in-a-tv-writers-room-roles-and-jobs-explained) |
| Структура эпизода (teaser / acts / tag, A/B/C) | [Final Draft: Structure a TV Pilot](https://www.finaldraft.com/blog/how-to-structure-a-tv-pilot), [ScreenWeaver: 60-min outline](https://www.screenweaver.ai/blog/outline-60-minute-tv-drama-pilot), [ScreenWeaver: Act breaks](https://www.screenweaver.ai/blog/broadcast-tv-act-breaks-teaser-tag-format) |
| Формат телепьесы | [BBC Writersroom: Screenplay Format for TV](https://downloads.bbc.co.uk/writersroom/scripts/screenplaytv.pdf), [StudioBinder: TV Script Format](https://www.studiobinder.com/blog/tv-script-format-examples/) |
| Script breakdown / stripboard | [StudioBinder: Breaking Down a Script](https://www.studiobinder.com/blog/free-script-breakdown-sheet/) |
| Coverage / shot list | [Tools for Film: Coverage](https://www.toolsforfilm.com/glossary/coverage), [StudioBinder: Shot List](https://www.studiobinder.com/blog/shot-list-template-free-download/), storyboard/coverage guides (shot/reverse, OTS, reaction) |
| Continuity / script supervisor | [Film Independent: Script Supervisor](https://www.filmindependent.org/blog/script-supervisor-tips-tricks-and-tools-for-better-continuity-and-careers/), [StudioBinder: Script Supervisor](https://www.studiobinder.com/blog/script-supervisor-forms-template/), [EP: Meet the Script Supervisor](https://www.ep.com/blog/meet-the-script-supervisor-rachel-connors-phillippe/) |
| Picture post (assembly → picture lock → online) | [Fastio: Post-Production Workflow](https://fast.io/resources/post-production-workflow/), offline/online editing practice |
| Audio post (spotting, dialogue, ADR, Foley, stems) | [Forte: Audio Post Workflow](https://www.forte-ai.com/blog/audio-post-production-workflow-from-picture-handoff-to-final-mix), [Hurricane Sound](https://hurricanesound.tv/2026/03/audio-post-production-for-tv-and-film-a-complete-guide-to-the-process/), [Post-Super: Spotting Sessions](https://post-super.com/blog/spotting-sessions) |

Индустрия снимает **out of order**; у нас генерация может идти по сценам/шотам — но **учёт continuity обязан быть как у script supervisor**, иначе сериал развалится так же, как на площадке без scripty.

---

## 1. Индустриальный lifecycle → наш пайплайн

По Vitrina и практике TV:

```text
DEVELOPMENT          PRE-PRODUCTION           «PRINCIPAL» (у нас gen)
concept → bible      breakdown → stripboard   coverage media
pilot package        cast/loc/look lock       (images/videos)

POST-PRODUCTION      DELIVERY
offline edit →       masters / stems / publish
picture lock →
audio post → online finish
```

| Индустриальная фаза | Что делают люди | Наш аналог в продукте |
|---------------------|-----------------|------------------------|
| Development | bible, pitch, pilot | F01–F03, pilot outline/teleplay |
| Writers’ room | break season → break episode → outline → draft → notes | F04–F07 + showrunner HITL |
| Pre-production | breakdown, stripboard, shot list, dept prep | F08–F11 |
| Production | coverage на площадке | F12–F14 (генерация пластин/клипов) |
| Script supervising | continuity log | F15 |
| Picture editorial | assembly → rough → fine → **picture lock** | F16–F17 |
| Audio post | spotting → DX → ADR → Foley/SFX → music → mix/stems | F18–F22 |
| Finish / delivery | online, titles, QC, deliverables | F23–F25 |

**Критический вывод индустрии:** audio post и finishing **после picture lock** (или near-lock).  
Нельзя финалить Foley/music mix, пока режут картинку — иначе всё поедет. В MVP допускаем iterative rough cut, но **финальный mix только после lock**.

---

## 2. Целевой продукт и format profiles

| Профиль | Близкий индустриальный формат | Длина | Структура страницы* |
|---------|--------------------------------|-------|---------------------|
| `series_half_hour` | half-hour drama/comedy | ~22–30 мин эфира / 6–12 мин MVP | Teaser + 2–3 acts + Tag |
| `series_hour` | one-hour drama | ~42–60 мин / 12–22 мин MVP | Teaser + 4–5 acts + Tag |
| `series_limited` | limited / mini | как hour, короткий сезон | Season = одна арка |
| `shorts` | текущий продукт | 60–75 сек | viral VO arc |

\*Правило «~1 страница ≈ 1 минута экрана» — ориентир индустрии (Courier screenplay), не закон. У AI-видео тайминг задаём отдельно (shot durations).

**MVP для video-pipeline (рекомендация):**  
`series_half_hour` урезанный — **6–10 мин**, 16:9, 3–5 эпизодов, Teaser + 3 acts + Tag, диалоговая драма, coverage `master + OTS pair + selective CU`.

---

## 3. Иерархия артефактов (как в комнате сценаристов + на площадке)

```text
SERIES BIBLE (living document)
  ├─ Season break (A/B/C arcs across episodes)
  ├─ Character bibles + relationships
  ├─ World / tone / comps
  └─ Episode rundowns (paragraph each)

EPISODE
  ├─ Beat sheet / outline (teaser, acts, act-outs, tag)
  ├─ Teleplay / shooting script (sluglines, action, dialogue)
  ├─ Script breakdown sheets (cast, props, wardrobe, FX…)
  ├─ Stripboard / shoot order (optional for AI; useful for cost)
  ├─ Shot list → Coverage
  ├─ Continuity log (story day, wardrobe state, props, eyelines)
  ├─ Picture: assembly → rough → fine → PICTURE LOCK
  ├─ Audio: spotting notes → DX/ADR → Foley/SFX → music → stems
  └─ Deliverables: master, M&E, subs, QC report
```

**Зависимости (жёсткие):**

1. Season break / bible lock → episode outline  
2. Outline lock (с чёткими **act-outs**) → teleplay  
3. Teleplay lock → breakdown  
4. Breakdown → shot list / coverage  
5. Coverage media → offline edit  
6. **Picture lock** → финальный audio post + online finish  
7. Cliffhanger / established facts → bible archive + next episode

---

## 4. Каталог функций (отделы), зачем / как / связи / вариации / что сделать

Каждая функция = нода/агент + артефакт. Имена даны в **индустриальных терминах**, в скобках — код ноды.

---

### F01. Series Bible — Development Blueprint  
**Нода:** `series_bible`

**Зачем (индустрия):** bible доказывает, что шоу живёт не один эпизод. Final Draft выделяет 3 уровня: development tool → pitch → writers’ room archive. AIScriptReader: обычно 15–30 стр.; блоки — logline, premise, world, tone, regulars, S1 arc, future seasons, pilot summary, episode rundowns.

**Вход → выход**  
- Вход: логлайн, жанр, comps («A meets B»), число эпизодов, каст-черновик.  
- Выход: структурированный bible + season episode map (1 абзац на эпизод) + список series regulars.

**Как применять**  
1. Заполнить бриф (title, logline, comps, engine: procedural / serialized / hybrid).  
2. Сгенерировать bible по секциям (не одним простынёй без заголовков).  
3. Showrunner HITL: утвердить tone + season map.  
4. Lock v1; дальше bible растёт как archive (Level 3).

**Связи:** корень для F02–F05; обновляется после каждого эп. (archive).

**Вариации:** pitch-bible (короткая) / room-bible (живая) / adaptation-bible (IP).

**Сделать:**  
- промпт-контракт секций bible (как в AIScriptReader template)  
- `Season.bible_md` + versioning  
- HITL `approve_bible`

---

### F02. Season Break (Writers’ Room: break the season)  
**Нода:** `season_break`

**Зачем:** в writers’ room сначала «ломают» сезон: где персонажи начинают/заканчивают, major turns, reveals — *до* письма отдельных серий ([Final Draft: Writers Room](https://www.finaldraft.com/blog/whos-in-a-tv-writers-room-roles-and-jobs-explained)).

**Вход → выход**  
- Вход: locked bible.  
- Выход: arc table (A/B/C per episode), premiere/midpoint/finale tentpoles, mythology rules.

**Как применять:** один раз на сезон; править только showrunner override.

**Связи:** ← F01; → F04 outlines; питает continuity plot threads.

**Вариации:** serialized drama / procedural+mythology / anthology (слабый season break).

**Сделать:** JSON/Markdown `season_arcs.json`; UI season board с арками.

---

### F03. Character Bible + Casting Lock (+ Voice Cast)  
**Нода:** `cast_bible` → reuse `hero`

**Зачем:** TV-персонажи живут сезонами; bible описывает personality, flaws, relationships, conflict drivers (Final Draft). На площадке внешность/костюм фиксируются; у нас + `voice_id` для DX.

**Как применять**  
1. Series regulars vs recurring vs guest.  
2. Текстовые карточки → visual turnaround (существующий hero).  
3. Voice casting map (ElevenLabs).  
4. Lock appearance для mains до pilot media.

**Связи:** dialogue voice, wardrobe continuity, image refs.

**Вариации:** ensemble / single protagonist / narrator+cast (hybrid docudrama).

**Сделать:** season-scoped CharacterBible; запрет смены lock без override.

---

### F04. Location / World Lookbook  
**Нода:** `location_bible`

**Зачем:** мир — storytelling engine (Final Draft: world section). Локации должны быть консистентны между эп.

**Как применять:** извлечь локации из bible/season break → plate refs (день/ночь) → привязка сцен к location_id.

**Связи:** breakdown, ambience beds, image prompts.

**Сделать:** LocationBible + refs в `data/series/.../locations/`.

---

### F05. Episode Break + Outline (Beat Sheet)  
**Нода:** `ep_outline`

**Зачем:** комната «ломает» эпизод: A/B/(C) story, emotional turns, **act-outs**. ScreenWeaver/Final Draft: teaser → acts → tag; act-out = decision/reveal/threat в одну строку. Без act-outs outline негоден.

**Контракт outline (обязательные поля):**

```text
SERIES QUESTION (для пилота) / EPISODE QUESTION
A STORY / B STORY / C STORY (если есть)
TEASER: what we see + question raised
ACT 1: action + emotional shift + ACT-OUT (1 line)
ACT 2: ...
ACT 3: ...
[ACT 4/5 if hour]
TAG: button / bridge to next episode
SCENE LIST (1 line each): INT/EXT, LOC, DAY/NIGHT, purpose
```

**Как применять**  
1. Подтянуть cliffhanger/ledger предыдущего эп.  
2. Сгенерировать outline.  
3. Проверка: каждый act-out читается как «лестница», не плато.  
4. HITL approve → lock.

**Связи:** ← F02; → teleplay; запрет писать диалоги до lock.

**Вариации структуры**

| Формат | Модули |
|--------|--------|
| Half-hour MVP | Teaser + Act1–3 + Tag |
| Hour drama | Teaser + Act1–4/5 + Tag |
| Streaming seamless | те же акты как *emotional modules*, без рекламных пауз |
| Bottle episode | мало локаций, упор на DX |
| Premiere / finale | усиленный teaser / payoff season break |

**Сделать:** нода + валидатор «act-out present»; статус `outline_locked`.

---

### F06. Teleplay / Screenplay Draft  
**Нода:** `teleplay`

**Зачем:** производство читает **screenplay**, не VO. BBC Writersroom: FADE IN; slugline `INT./EXT. LOCATION - DAY/NIGHT`; action только то, что на экране; character + dialogue; parentheticals; (V.O.)/(O.S.); teaser/acts на странице. StudioBinder: TV жёстче feature по act labels.

**Вход → выход**  
- Вход: locked outline + character speech profiles + continuity facts.  
- Выход: teleplay.md (+ нормализованные Scene/DialogueLine).

**Правила применения (из BBC):**  
- action ≠ мысли персонажа;  
- абзац action ≈ beat;  
- новый slugline = новая сцена (для breakdown);  
- не нумеровать сцены в раннем draft; номера — в shooting script после breakdown.

**Как применять**  
1. Draft 1 от outline (можно skeleton: sluglines+action, потом DX pass).  
2. Showrunner notes (HITL).  
3. Polish (F07).  
4. Lock teleplay → «blue pages» логика: правки версионировать.

**Связи:** → breakdown; запрет использовать `voiceover_author` как default series writer.

**Вариации:** single-camera drama format (default) / multi-cam sitcom (другой формат — later) / hybrid VO+DX.

**Сделать:** `make_teleplay.py`; промпты `ser_teleplay`; парсер slugline→Scene; HITL `approve_teleplay`.

---

### F07. Dialogue Pass / Character Voice Notes  
**Нода:** `dialogue_polish`

**Зачем:** room notes + showrunner pass, чтобы regulars не звучали одним голосом; parentheticals не злоупотреблять.

**Как применять:** после Draft 1; чеклист: длина реплик, subtext, info-dump, отличимость.

**Связи:** ← F03 speech profiles; → DX performance tags для TTS.

**Сделать:** `check_teleplay` + polish agent; опционально rewrite одной сцены.

---

### F08. Script Breakdown  
**Нода:** `script_breakdown`

**Зачем:** StudioBinder: breakdown помечает элементы сцены для отделов — cast, props, wardrobe, makeup, vehicles, stunts, VFX, sound… Это вход в schedule/stripboard и бюджет.

**Вход → выход**  
- Вход: locked teleplay (consistent sluglines/names!).  
- Выход: breakdown sheet per scene + element tags.

**Как применять**  
1. Авто-tag из teleplay.  
2. HITL поправить героев-реквизит.  
3. Сверить имена локаций/персонажей (consistency — требование breakdown).

**Связи:** → stripboard, wardrobe/props prep, shot list inputs, sound spotting candidates.

**Элементы MVP (обязательный минимум):** Cast, Props, Wardrobe state, Day/Night, INT/EXT, Special (VFX/weapons), Sound notes.  
**Full later:** полный StudioBinder element set.

**Сделать:** `breakdown_scenes.py`; UI scene cards с тегами; не путать с VO-`split`.

---

### F09. Stripboard / Schedule (опционально для AI, важно для стоимости)  
**Нода:** `stripboard`

**Зачем:** в live-action 1st AD группирует сцены для съёмки (локация, день/ночь, актёры). У AI «съёмка» = генерация; stripboard = **порядок генерации для экономии** (сначала все шоты одной локации/одного героя).

**Как применять:** auto-group by location → estimate gen cost → optional reorder.

**Связи:** ← breakdown; → production queue.

**Вариации:** story order (проще continuity) vs location batch (дешевле/быстрее).

**Сделать:** фаза 1 можно story-order only; stripboard — фаза 2.

---

### F10. Shot List + Coverage Design (Director/DP)  
**Нода:** `shot_list` / `coverage`

**Зачем:** Coverage = набор ракурсов, дающий editor options ([Tools for Film](https://www.toolsforfilm.com/glossary/coverage)). Shot list = план coverage до «съёмки» ([StudioBinder](https://www.studiobinder.com/blog/shot-list-template-free-download/)).  
Shot list ≠ coverage: list = намерение, coverage = результат.

**Стандартный dialogue coverage (индустрия):**  
1. **Master / wide** — география, целиком сцена  
2. **OTS / dirty single A**  
3. **Reverse OTS B**  
4. **Clean CU** на эмоциональные пики (selectively)  
5. **Reaction** слушающего (ритм монтажа!)  
6. **Insert/cutaway** по необходимости  

Правила: **180° line**, matching eyelines, matching shot sizes в shot/reverse, не over-cut каждую реплику.

**Поля shot list (минимум как StudioBinder):** scene #, shot #, size/type, movement, description, linked dialogue beat, duration est, setup group.

**Как применять**  
1. Выбрать профиль coverage на сцену (таблица ниже).  
2. Сгенерировать shot list.  
3. HITL: вырезать лишние setups (экономия Veo/Nano).  
4. Lock → image/anim prompts.  
5. Учитывать лимит клипа **≤8s**: длинная сцена = много шотов, не один «бесконечный» промпт.

**Профили coverage**

| Условие сцены | Рекомендуемый pack | Можно скипнуть |
|---------------|-------------------|----------------|
| 2 pers., конфликт | two-shot → dirty OTS pair → selective CU | clean singles (если не нужна изоляция) |
| Допрос / власть | clean singles + angle differential | two-shot |
| 3+ pers. | wide master + singles + consistent eyelines | все возможные two-shots |
| Walk-and-talk | moving master + CU pickups | static two-shots |
| Montage / timejump | inserts sequence | dialogue coverage |
| Directed minimal | один master | всё остальное (высокий риск) |

**Связи:** заменяет смысл shorts `split`; питает F12–F14 и editor options в F16.

**Сделать:** ноды + blocks `coverage_dialogue`, `coverage_action`, …; UI shot table; валидатор 180°/eyeline warnings.

---

### F11. Department Look Dev (Costume / Props / Makeup states)  
**Нода:** `look_continuity_prep`

**Зачем:** script supervisor + wardrobe/makeup трекают *story day* и деградацию (синяк, грязь, порванная одежда) ([Film Independent](https://www.filmindependent.org/blog/script-supervisor-tips-tricks-and-tools-for-better-continuity-and-careers/), EP interview).

**Как применять:** из breakdown построить state machine: Character × StoryDay → wardrobe/makeup/props state; прокинуть в image prompts.

**Связи:** F08, F15, F12.

**Сделать:** ContinuityState таблица; фаза 2 обязательна для «классики», в MVP — ручные notes.

---

### F12. Image Prompts + Plates (Camera department stills)  
**Нода:** series-mode `image_prompts` / `images`

**Зачем:** keyplate/keyframe на каждый shot list item + refs cast/location/look state.

**Как применять:** как текущий img_pr/img, но источник = Shot; всегда multi-ref attach; HITL gallery по сценам.

**Связи:** ← F10/F03/F04/F11; → video; visual continuity check.

**Сделать:** adapter существующих Outsee steps; batch by scene_id.

---

### F13. Animation Prompts + Picture Production (Veo clips)  
**Нода:** series-mode `anim_pr` / `video`

**Зачем:** «principal photography» клипов по coverage; движение камеры из shot list (не глобальный slow push-in).

**Как применять**  
- CU dialogue: минимальное движение, performance  
- master: мотивированный dolly/pan  
- reaction: hold / micro move  
- несколько клипов на сцену → editor выбирает (coverage options)

**Связи:** soft retry как сейчас; Shot.video_artifact_id.

**Сделать:** `ser_animation` templates; запрет default push-in everywhere.

---

### F14. Production Sound / Temp DX (guide track)  
**Нода:** `temp_dialogue` (может совпадать с F19 early)

**Зачем:** в live-action есть production sound; у нас TTS guide track нужен уже на offline edit (чтобы резать по репликам), даже до финального ADR-pass.

**Как применять:** после teleplay lock можно параллельно с F12–F13; на rough cut кладём temp DX.

**Связи:** F16 assembly; позже F19 replaces/refines.

---

### F15. Continuity / Script Supervisor Log  
**Нода:** `continuity_steward`

**Зачем:** scripty — source of truth: dialogue as written, action as written, wardrobe/props/eyeline/emotional entrance ([StudioBinder](https://www.studiobinder.com/blog/script-supervisor-forms-template/), EP). На TV критично из‑за out-of-order; у нас — из‑за раздельной генерации шотов/эпизодов.

**Типы continuity (логировать отдельно):**  
1. **Story** — факты, knowledge, arcs  
2. **Visual** — wardrobe, props hands, injuries, set dressing  
3. **Performance** — emotional state entering scene  
4. **Screen direction** — 180°, exits/entrances  

**Когда гонять:** после teleplay; после images; перед picture lock; перед стартом E+1.

**Как применять:** report block/warn; critical → стоп генерации.

**Сделать:** ContinuityEntry ledger; checks `story|visual|performance|axis`; экспорт «scripty notes» в UI.

---

### F16. Offline Picture Editorial (Assembly → Rough → Fine)  
**Нода:** `assemble_offline`

**Зачем:** offline = storytelling cut на proxies/доступных клипах ([Wikipedia Offline editing](https://en.wikipedia.org/wiki/Offline_editing), Fastio workflow). Стадии: **assembly** (все сцены по порядку) → **rough cut** → **fine cut**.

**Как применять**  
1. Assembly: склеить masters по teleplay order (даже без полной coverage).  
2. Rough: врезать OTS/CU/reactions; подложить temp DX.  
3. Fine: ритм, L/J-cut упрощённо, убрать лишнее.  
4. HITL director/showrunner cut.

**Связи:** нужен cut list из Shot; это **не** текущий VO-align assemble.

**Сделать:** `assemble_episode.py` режимы `assembly|rough|fine`; timeline JSON.

---

### F17. Picture Lock  
**Нода:** `picture_lock` (milestone, не «креатив»)

**Зачем:** точка, после которой **не двигают тайминг шотов**; handoff в sound/music/online ([Fastio](https://fast.io/resources/post-production-workflow/), online prep guides). Без lock audio post бессмысленно переделывать.

**Как применять:** явный HITL «Lock picture»; после lock — запрет менять cut без unlock+re-spot.

**Связи:** триггер F18 spotting и финальных F19–F22.

**Сделать:** Episode.status=`picture_locked`; API guard на timeline mutate.

---

### F18. Spotting Sessions (Sound + Music + VFX notes)  
**Нода:** `spotting`

**Зачем:** до audio work команда смотрит cut и ставит cues ([Post-Super](https://post-super.com/blog/spotting-sessions)): где DX чинить/ADR, где Foley, где music enter/exit, где VFX. Отдельно sound spotting и music spotting.

**Выход:** spotting notes sheet: timecode/scene, type (DX/SFX/Foley/Music/VFX), intent.

**Как применять:** только на near-lock/lock; HITL composer/sound (или один showrunner в соло-режиме).

**Связи:** ← F17; → F19–F22, VFX list.

**Сделать:** нода генерит черновик notes из teleplay+cut; UI edit cues.

---

### F19. Dialogue Edit + ADR  
**Нода:** `dialogue_edit` / `adr`

**Зачем:** в TV **dialogue first** в миксе (industry audio practice). ADR — пересъём негодных/несуществующих линий под picture ([Hurricane Sound](https://hurricanesound.tv/2026/03/audio-post-production-for-tv-and-film-a-complete-guide-to-the-process/)).

**Как применять у нас**  
1. Multi-voice TTS по DialogueLine + emotion tags (= production DX / guide).  
2. Align к picture lock timeline.  
3. ADR-lite: retake одной линии.  
4. Dialogue premix levels.

**Связи:** приоритет над music/SFX в балансе; subs из DX script.

**Сделать:** расширить `generate_audio.py`; line retake UI; series не пишет всё в `voiceover.txt`.

---

### F20. Sound Design + Foley + Ambience  
**Нода:** `sound_design`

**Зачем:** ambience «сажает» DX в локацию; Foley — синхронные бытовые звуки; SFX — story effects. Spotting определяет объём.

**MVP:** location ambience bed + ключевые SFX.  
**Full:** Foley pass (фаза 3).

**Как применять:** после spotting; не глушить DX.

**Сделать:** нода + библиотека beds; manual upload path.

---

### F21. Music Editorial (cues, не одна BGM)  
**Нода:** `music_spotting` / `music_edit`

**Зачем:** music spotting задаёт enter/exit и эмоцию cue; music editor кладёт музыку в picture; stems для микса ([Forte](https://www.forte-ai.com/blog/audio-post-production-workflow-from-picture-handoff-to-final-mix)).

**Как применять:** 4–10 cues на 8–10 мин; тишина — валидный choice; ducking под DX.

**Связи:** ← spotting; существующий Suno/Outsee как renderer кусков.

**Сделать:** расширить `generate_music.py` → cue list + stems-ish files.

---

### F22. Re-recording Mix + Stems + M&E  
**Нода:** `final_mix`

**Зачем:** баланс DX/Music/FX; print master; **stems** (Dialogue / Music / Effects); **M&E** без диалога для локализации ([Forte](https://www.forte-ai.com/blog/audio-post-production-workflow-from-picture-handoff-to-final-mix)).

**Как применять:** только после picture lock + готовых DX/SFX/music; QC loudness (ориентир broadcast dialnorm — упростить для MVP).

**Сделать:** FFmpeg stem buses; export master+M&E; фаза 2–3.

---

### F23. Online Finish (conform, color, titles, VFX insert)  
**Нода:** `online_finish`

**Зачем:** после offline lock — полный quality pass: color, titles/recap/credits, VFX inserts ([offline→online](https://thestudiobridge.com/offline-online-editing-workflow/)).

**Как применять:** packaging «Previously on» / main title / end credits; color LUT season lock.

**Связи:** F17 lock; deliverables.

**Сделать:** `ep_packaging` + optional color preset; recap из prior episode stills.

---

### F24. QC / Showrunner Acceptance  
**Нода:** `episode_qc`

**Зачем:** приёмка: структура (teaser/acts/tag), cliffhanger, DX clarity, continuity report, runtime, loudness, credits.

**Не использовать** shorts `check_plan` (30s hook) как критерий серии.

**Сделать:** `check_episode` + checklist UI.

---

### F25. Delivery + Publish + Bible Archive Update  
**Нода:** `deliver` / `publish`

**Зачем:** distribution lifecycle (Vitrina stage 4): masters, subs, metadata; обновить writers’ room bible archive (что установили в эп.).

**Как применять:** publish episode; записать ContinuityEntry + season archive notes → разблок F05 для E+1.

**Сделать:** publish adapter; `season_bible` append «aired facts».

---

## 5. Сводный граф (правильный порядок)

```text
DEVELOPMENT
  brief → series_bible → HITL
       → season_break
       → cast_bible → hero_refs → voice_cast → HITL
       → location_bible → location_refs → HITL

PER EPISODE — WRITERS
  ep_outline → HITL
  teleplay → dialogue_polish → HITL
  continuity_story

PRE-PROD
  script_breakdown → (stripboard)
  look_continuity_prep
  shot_list/coverage → HITL

PRODUCTION (GEN)
  image_prompts → images → HITL → continuity_visual
  anim_pr → videos → HITL
  temp_dialogue (parallel after teleplay lock)

POST — PICTURE
  assemble assembly → rough → fine → HITL → PICTURE_LOCK

POST — SOUND / FINISH
  spotting
  dialogue_edit/ADR
  sound_design
  music_edit
  final_mix (stems/M&E)
  online_finish (color/titles/recap)
  episode_qc → HITL_final → deliver/publish
  bible_archive_update
```

---

## 6. Операционный runbook

### 6.1. Сезон (один раз)
1. Format profile + series/season records  
2. Bible → HITL lock  
3. Season break (A/B/C map)  
4. Cast + voices + visual lock  
5. Locations + plates  
6. Init continuity ledger + story calendar (story days)

### 6.2. Эпизод
1. Outline с act-outs → lock  
2. Teleplay → polish → lock  
3. Story continuity gate  
4. Breakdown (+ stripboard optional)  
5. Coverage shot list → trim → lock  
6. Plates/images → visual continuity  
7. Videos per shot  
8. Temp DX (если ещё нет)  
9. Offline assembly→rough→fine → **picture lock**  
10. Spotting  
11. DX final / ADR retakes  
12. Ambience/SFX (+ Foley later)  
13. Music cues  
14. Mix + stems  
15. Titles/recap/color  
16. QC → final HITL → publish  
17. Archive facts → next outline

### 6.3. Запреты (из практики площадки/поста)
1. Диалоги до outline lock  
2. Финальный mix до picture lock  
3. Генерация «одного клипа на сцену» вместо coverage  
4. Игнорировать reaction shots (редактор теряет ритм)  
5. Ломать 180° / eyeline без решения режиссёра  
6. Считать mass/batch сезонным storytelling  
7. Одна BGM на весь эп. без spotting  

---

## 7. Маппинг на текущий video-pipeline

| Сейчас | Индустриальная замена | Действие |
|--------|----------------------|----------|
| `plan` (viral 60s) | F01 bible + F02 season break + F05 outline | новые ноды |
| `script` VO | F06 teleplay + F07 polish | новый writer |
| `split` | F08 breakdown + F10 coverage | не VO-chunking |
| `hero`/`items` | F03/F04/F11 | season-scoped |
| `enrich_*` | optional tools | не ядро |
| `img_pr`/`img` | F12 | Shot-driven |
| `anim_pr`/`video` | F13 | coverage-driven |
| `audio` | F14/F19 | multi-voice DX/ADR |
| `music` | F18/F21 | cues after spotting |
| `assemble` | F16→F17→F22→F23 | offline/lock/mix/online |
| `publish` | F25 | + archive |
| HITL short kinds | showrunner gates | новые kinds |
| `BatchProject` | Season board | не использовать как season |

Якоря кода: `default_graph.py`, `node_registry.py`, `orchestrator/steps/*`, `models.py`, `assembly.py`, `prompts/blocks/camera_*`.

---

## 8. Фазы внедрения (что конкретно сделать)

### Фаза 0 — Foundation
- [ ] `FormatProfile` + flag series  
- [ ] Models: Series, Season, Episode, Scene, Shot, DialogueLine, CharacterBible, LocationBible, ContinuityEntry, SpottingCue, CutVersion  
- [ ] Alembic + `/api/series/...`  
- [ ] Season board UI  
- [ ] `default_series_graph()` stubs  
- [ ] Feature flag; shorts regression safe  

### Фаза 1 — Writers’ room + Coverage MVP
- [ ] F01 bible (секции по Final Draft/AIScriptReader)  
- [ ] F02 season break  
- [ ] F03 cast+voices+hero  
- [ ] F05 outline с обязательными act-outs  
- [ ] F06–F07 teleplay+polish+parser (BBC-like sluglines)  
- [ ] F08 breakdown (min elements)  
- [ ] F10 coverage profiles `dialogue_2shot`  
- [ ] F12–F13 adapters Outsee  
- [ ] F15 story continuity  
- [ ] F16 assembly/rough/fine + F17 picture lock  
- [ ] F19 multi-voice DX + retake  
- [ ] F21 simple music cues + ducking  
- [ ] F24 QC checklist  
- [ ] Tests: teleplay parse, act-out validator, cut order, shorts graph intact  

**Приёмка фазы 1:** 3 эп. × 6–10 мин, 16:9, teaser/acts/tag, слышные диалоги, coverage ≥3 угла на диалоговую сцену, picture lock перед финальным mix, cliffhanger → E02.

### Фаза 2 — Pre-prod depth + Sound
- [ ] F04 location lookbook day/night  
- [ ] F09 stripboard cost routing  
- [ ] F11 wardrobe/story-day states  
- [ ] F15 visual/axis continuity  
- [ ] F18 spotting UI  
- [ ] F20 ambience/SFX  
- [ ] F22 stems + M&E  
- [ ] F23 titles/recap/color  
- [ ] `reset_scene` / `reset_shot` with backup  

### Фаза 3 — Full classic
- [ ] Foley pass, полноценный ADR UX  
- [ ] Online color pipeline  
- [ ] A/B/C tracker dashboard  
- [ ] Delivery package (loudness, captions, thumbs)  
- [ ] Multi-cam sitcom format (optional)  
- [ ] Writers’ room archive automation after each ep  

---

## 9. Вариации шоу (выбор до старта)

| Вариант | Индустриальный аналог | Акцент функций |
|---------|----------------------|----------------|
| V1 Serialized drama | cable/streamer drama | F02, F06, F10 dialogue coverage, F19 |
| V2 Procedural | case-of-week | F05 A-plot engine, lighter season mythology |
| V3 Limited series | mini | сильный F02 tentpoles, короткий season |
| V4 Hybrid docu | VO+interviews | старый VO writer + partial DX |
| V5 Vertical mini | Shorts-series | тот же граф, 9:16, урезанный coverage |

Зафиксировать **V1** для первой реализации.

---

## 10. Риски и где индустрия « Holдит»

| Риск | Почему больно | Митигация |
|------|----------------|-----------|
| Нет picture lock | audio/music всегда устаревают | жёсткий milestone F17 |
| Нет act-outs | «простыня» вместо эпизода | валидатор outline |
| Нет coverage | editor не режет ритм | min pack + reaction shots |
| Один голос | не сериал | voice cast lock |
| Continuity только «на глаз» | сезон разваливается | scripty ledger |
| Путать mass factory с season | нет арок | отдельные сущности |

Самый жёсткий **креативный** узел: teleplay + character voice (F06/F07).  
Самый жёсткий **технический** узел: offline cut + DX align + picture lock (F16/F17/F19).

---

## 11. Шпаргалка оператора (E01)

1. Lock bible + season break + cast/voices + locations  
2. Outline с act-outs → approve  
3. Teleplay (BBC-style) → polish → approve  
4. Continuity story gate  
5. Breakdown  
6. Coverage shot list (master+OTS+reaction) → trim  
7. Images → visual continuity  
8. Videos per shot  
9. Temp/final DX voices  
10. Assembly→rough→fine → **LOCK PICTURE**  
11. Spotting → music cues → ambience  
12. Mix → titles → QC → publish  
13. Archive cliffhanger facts → E02 outline  

---

## 12. Решение для старта (зафиксировать)

- Format: **16:9**, **6–10 мин**, **5 эпизодов**  
- Structure: **Teaser + 3 Acts + Tag**  
- Variation: **V1 serialized drama**  
- Coverage: **master + OTS pair + selective CU/reaction**  
- Audio MVP: **multi-voice DX + music cues**, Foley later  
- Post rule: **no final mix before picture lock**  
- Shorts pipeline: **не ломать**, только feature flag  

Реализация: **§8 Фаза 0 → Фаза 1**, порядок **§5–§6**.

---

## 13. Следующий артефакт после утверждения

1. RFC схем таблиц БД (поля ContinuityEntry, SpottingCue, CutVersion)  
2. JSON-контракты: `bible`, `season_break`, `outline`, `teleplay`, `breakdown`, `shot_list`, `cut_timeline`  
3. Черновик `default_series_graph()` в коде  

---

*Версия документа: 2.0 — индустриальная переработка на основе профессиональных TV/film источников (§0). Заменяет внутреннюю v1 «agent wishlist»-структуру.*
