# Blueprint: как собрать цепочку нод Studio, воспроизводящую стиль ref1/ref2

Документ — практическая инструкция «что в какую ноду подать», выведенная из
разбора [`style-analysis-example.md`](style-analysis-example.md) (стиль
«Zack D. Films»: стилизованный 3D, один герой в футболке, одна среда,
склейка на каждой фразе, подсветка предметов, вставки-карточки). Все числа —
измеренные; где число зависит от жанра ролика (экшен-история / объяснялка),
даны оба значения.

Та же цепочка в машинном виде лежит в паспорте v2: `passport.blueprint.chain`
(`app/services/style_agent/blueprint.py::default_chain`) и шаблоны
`passport.blueprint.prompt_templates`; агент `blueprint` уточняет их по
паспорту. Нода `hitl_style` → «Утвердить» материализует блоки промптов
(`prompts/blocks/*/<style_id>.md`), пресет `prompts/styles/<style_id>.json`,
style-lock `prompts/frame_styles/style_<style_id>.md` и патч
`node_step_params` (см. `app/services/style_agent/apply.py`).

## 0. Два режима одного стиля

| режим | когда | cuts/min | медиана плана | sync | пример |
|---|---|---|---|---|---|
| `action` | история с героем и событиями | 45 | 1.0 с (p10 0.6, p90 1.8) | `each_phrase_new_shot` (≈ 1 план на строку субтитра) | ref1 |
| `explainer` | задача/правила/сценарии, второе лицо | 25 | 1.4 с (p10 0.7, p90 2.9) | `shot_spans_phrases` (план на 2–3 строки, вставки по правилам) | ref2 |

Режим выбирается в `plan` (`params.pace`) и передаётся вниз по цепочке.

## 1. Цепочка нод

```
topic ─ plan ─ script ─ split ─ [NEW] shot_plan ─ hero ─ items ─ image_prompts ─ images ─ hitl_images
      ─ animation_prompts ─ videos ─ hitl_videos ─ audio ─ [NEW] overlay_assets ─ music ─ sfx_plan ─ sfx_gen
      ─ assemble / [NEW] vo_sync_edit ─ [NEW] style_check ─ hitl_final ─ publish
```

Существующие ноды используются как есть; четыре `NEW` описаны в §3 с тем,
чем их заменить **сейчас** (пока нод нет).

### 1.1. `topic` → `plan`

| | |
|---|---|
| вход | тема/бриф (1–2 предложения), `pace: action \| explainer` |
| выход | `plan`: длительность, список битов (5–8), тип хука, обращение |
| параметры | `duration_seconds`: action 40–45, explainer 60–75; `beats`: action = hook + 3 эскалации + кризис + контекст + payoff; explainer = task + rules(2) + scenario×3 + moral; `address`: action = third_person, explainer = second_person; `cta: none`, `end_card: false` |
| промпт | блок «narrative_structure» из паспорта + §4 отчёта: «Структура action: hook-в-действии → эскалация ×3 → кризис → поздний контекст → число-рекорд → развязка. Структура explainer: задача „тебе“ → 2 правила → провал A → провал B → решение → мораль-афоризм. Без приветствия и CTA.» |

### 1.2. `script` (сценарист)

| | |
|---|---|
| вход | `plan` |
| выход | текст по предложениям (EN или RU), затем тот же текст, разбитый на фразы по 2–4 слова (1 фраза = 1 строка) |
| параметры | `pace_wpm: 170`; слов всего ≈ `duration × 2.85`; предложений ≈ 12–15/мин; медиана 10–14 слов в предложении; строк ≈ 1/с; `words_per_line: 3`; `use_blocks_v2: true`, `style_profile: <style_id>` |
| промпт | шаблон `script_writer` (§4.1) |

### 1.3. `split`

| | |
|---|---|
| вход | текст сценария |
| выход | ячейки = фразы = будущие планы (в v8-таблице: 1 ячейка закадра) |
| параметры | `cell_min_chars: 12`, `cell_max_chars: 27` (≈ 3 слова; action), для explainer допускается до 2 строк в ячейке (`cell_max_chars: 54`), но субтитр всё равно режется по 3 слова |
| правило | ячейка не рвёт существительное от глагола («the biggest stalk» — одна ячейка) |

### 1.4. `shot_plan` [NEW] (сейчас: `sd_agent` camera + action → `sd_assemble`)

| | |
|---|---|
| вход | ячейки с таймкодами (после `audio` — точные; до — оценка по 170 wpm), `pace` |
| выход | шот-лист: `i, phrase_ids, what, framing, camera, start_state, change, end_state, transition_out, illustrates, highlight_color, overlay, gag, len_s` |
| параметры | `shot_len_s`: action 1.0 (0.6–1.8), explainer 1.4 (0.7–2.9); `min_shot_s: 0.4` (союз-биты); `max_shot_s`: action 2.7, explainer 7.5 (только непрерывное действие); `framing_mix`: close 30 / medium 45 / full-wide 25; `camera_vocab`: handheld_drift 55 %, static 30 %, slow_push_in 12 %, crane/drone 3 %; `transitions`: cut 95 %, zoom_jump 5 % (только на повторе фразы) |
| промпт | шаблон `storyboard` (§4.2) |
| обязательные правила | (1) `illustrates` — существительное/глагол фразы, показанный буквально; (2) при повторе фразы — тот же план + `transition_out: zoom_jump`; (3) союз-фраза («But», «So», «With it») → план 0.4–0.6 с, статичный; (4) `highlight_color` по цвет-коду: yellow = полезный предмет, green = правильный выбор, orange = опасность, pink/red = удар/ошибка; (5) `overlay` ∈ {none, card, thought_bubble, marker, crown, lines}; (6) `gag` — преувеличенная реакция героя, не чаще 1 на 10 планов |

### 1.5. `hero`, `items`

| | |
|---|---|
| вход | шот-лист (список персонажей и предметов из `illustrates`) |
| выход | референсы: 1 герой, 5–10 предметов |
| параметры | `style_profile`, `frame_style: style_<style_id>`; герой: «one adult man, short dark hair, light stubble, plain white (or grey) crew-neck tee, blue jeans, cartoon-realistic 3D» |
| промпт | шаблон `image_prompt` с `[SHOT] character turnaround, neutral pose` |

### 1.6. `image_prompts` → `images` → `hitl_images`

| | |
|---|---|
| вход | шот-лист, референсы |
| выход | промт и PNG **стартового состояния** каждого плана (`start_state`), 9:16 |
| параметры | `aspect: 9:16`, `frame_style: style_<style_id>`; композиция: субъект по центру, нижняя треть свободна под субтитры; одна среда на ролик (море/поле/…), задаётся один раз в `world` |
| промпт | шаблон `image_prompt` (§4.3) |

### 1.7. `animation_prompts` → `videos` → `hitl_videos`

| | |
|---|---|
| вход | шот-лист (`start_state → change → end_state`, `camera`), PNG |
| выход | промт движения и клип на план |
| параметры | `clip_len_s = len_s + 0.3` (запас под cut), min 1.0, max 5.0; разрешено: slight handheld drift, slow push-in, follow, crane up (один раз на ролик); запрещено: dissolve, morph, speed ramp, whip pan, текст в кадре |
| промпт | шаблон `video_prompt` (§4.4) |
| заметка | jump-zoom **не** генерируется вторым клипом — делается на монтаже из того же клипа (scale 1.2) |

### 1.8. `audio` (TTS)

| | |
|---|---|
| вход | фразы (ячейки) |
| выход | VO + **таймкоды слов** (нужны `vo_sync_edit` и подсветкам) |
| параметры | голос мужской, ровный, низкая экспрессия; `pace_wpm: 170`; паузы между предложениями ≤ 0.15 с; stability 0.6, similarity 0.8; язык — по сценарию |
| промпт | шаблон `vo_tts` (§4.5) |

### 1.9. `overlay_assets` [NEW] (сейчас: `items` + ручная сборка в `assemble`)

| | |
|---|---|
| вход | шот-лист (`overlay`, `highlight_color`, `illustrates`), таймкоды слов |
| выход | PNG-карточки (блокнот/календарь/мерки), пузыри, маркеры, маски подсветки + json с таймкодами появления |
| параметры | card: pop-in 6 кадров, page-flip 8 кадров, якорь над головой героя; highlight: glow/tint по маске объекта, вход 4 кадра, держим 0.3–0.6 с, выход 8 кадров; старт = таймкод существительного |
| промпт | «flat paper notebook page, hand-drawn icon of {object}, 1–2 words „{label}“, white paper, spiral binding, isolated on transparent background» |

### 1.10. `music`, `sfx_plan`, `sfx_gen`

| | |
|---|---|
| вход | plan / шот-лист |
| выход | подложка; SFX на действие |
| параметры | music: instrumental bed, без вокала, ровная (LRA ≤ 3), **−5.4 dB к голосу**, без пауз, не акцентирует склейки; SFX: по `change` плана (всплеск, удар, шелест, перелистывание, pop вставки), 1 SFX на 2–3 плана, −10 dB к голосу |
| промпт | шаблон `music_sfx` (§4.8) |

### 1.11. `assemble` → (`vo_sync_edit` [NEW]) → `style_check` [NEW] → `hitl_final` → `publish`

| | |
|---|---|
| вход | клипы, VO + таймкоды слов, музыка, SFX, оверлеи, фразы |
| выход | mp4 9:16 |
| параметры assemble | `subtitles_enabled: true`, `subtitles_mode: line`, `subtitles_position: lower_third` (y = 72–86 %), стиль: белый полужирный sans, лёгкая тень, без подложки, 3 слова в строке, 1 строка; `cut_offset_ms: +40`; `bgm_level_db: −5.4`; хвост после последней фразы 0.6 с; end card нет |
| vo_sync_edit | cut_time(i) = phrase_start(i) + 0.04 с; на повторе фразы — jump-zoom (scale 1.2) вместо новой склейки; союз-фраза получает план 0.4–0.6 с |
| style_check | прогнать `extract.analyze_video` по готовому mp4 и сравнить с паспортом: cuts/min в коридоре режима ±30 %, медиана плана ±30 %, `cut_on_phrase_ratio ≥ 0.6`, `words_per_line = 3`, музыка −4…−7 dB, статичных планов ≤ 10 %, хвост ≤ 1 с; при провале — список планов для перегенерации |

## 2. Таблица «нода → паспорт v2» (откуда берутся значения)

| нода | поле паспорта |
|---|---|
| plan | `format.duration_s`, `narrative.structure`, `narrative.hook_type`, `narrative.address` |
| script | `voice.pace_wpm`, `script.words_per_sentence_median`, `script.sentences_per_min`, `script.beats_per_sec`, `narrative.sentence_templates`, `narrative.rhetoric_devices`, `narrative.forbidden_phrases_seed`, `narrative.example_hook/payoff` |
| split | `linkage.words_per_line_median` |
| shot_plan | `motion.shot_len_s/p10/p90`, `linkage.pattern`, `linkage.cut_offset_ms_median`, `linkage.visual_illustrates_noun_ratio`, `motion.camera_vocab`, `motion.transitions`, `motion.shot_construction`, `patterns[]` |
| image_prompts | `mapping.visual_core_en`, `mapping.lighting_en`, `mapping.composition_en`, `mapping.negative_en`, `visual.palette_hex`, `visual.framing_mix` |
| animation_prompts | `motion.camera_vocab`, `motion.subject_motion`, `mapping.anim_negative_en` |
| audio | `voice.*`, `voice.pause_median_s` |
| overlay_assets | `patterns[kind in (vfx, overlay)]` |
| music / sfx | `music.level_vs_voice_db`, `mapping.music_suno_core_en`, `sfx.kinds`, `sfx.density` |
| assemble | `motion.subtitles_*`, `linkage.cut_offset_ms_median`, `script.tail_silence_s` |
| style_check | всё `linkage.*`, `motion.*`, `music.level_vs_voice_db` |

## 3. Новые ноды: что делать, пока их нет

| NEW | минимальная замена сегодня | что теряем |
|---|---|---|
| `shot_plan` | `sd_agent` (camera + action) → `sd_assemble` c промптом `storyboard` из §4.2; поля `illustrates/highlight_color/overlay/gag` писать в заметки кадра | автоматическая проверка «1 фраза = 1 план» и длины планов |
| `overlay_assets` | карточки/пузыри генерировать нодой `items` как PNG с прозрачным фоном; накладывать в `assemble` вручную | автоматический тайминг по словам |
| `vo_sync_edit` | `assemble` режет по ячейкам (фразам) — это уже 70 % эффекта; `cut_offset_ms` задать в `node_step_params.assemble` | jump-zoom на повторе, союз-биты |
| `style_check` | запустить `style_extract` на готовом mp4 как на референсе и сравнить отчёты | автоматический список планов на перегенерацию |

## 4. Готовые шаблоны промптов

Плейсхолдеры: `{topic}`, `{pace}`, `{duration}`, `{script}`, `{sentence}`,
`{shot}` (строка шот-листа), `{style}` (style lock), `{world}` (среда ролика).

### 4.1. Сценарист (`script`)

```text
Ты — сценарист коротких вертикальных роликов в стиле «один герой, одна среда,
картинка показывает каждое слово». Тема: {topic}. Режим: {pace}.

Длительность {duration} с → ≈ {duration}×2.85 слов при темпе 170 слов/мин.
Предложений 12–15 в минуту, медиана 10–14 слов, максимум 25.
Каждое предложение дробится на фразы по 2–4 слова (это субтитры и планы:
1 фраза ≈ 1 с). Фраза не разрывает «существительное + глагол».

Структура action: хук-в-действии (герой делает странное/конкретное) →
эскалация ×3 «малое → большее» → кризис и ответ в одном такте («…it attacked
him. So he grabbed…») → поздний контекст (кто он и почему) → число-рекорд →
развязка одной фразой. Обращение: третье лицо.
Структура explainer: «Your goal is to {задача}» → «But there are two rules:
you can only {A}. And you can only {B}» → «In the first scenario… you realize
{ошибка}» → «In the second scenario… {противоположная ошибка}» → «In the third
scenario… {метод}» → мораль-афоризм «The goal isn't {X}, it's just {Y} and
{Z}». Обращение: второе лицо.

Шаблоны предложений: «He {действие} {предмет} and {действие} it into {новый
предмет}»; «With it he {результат} which he used to {больший результат}»;
«This attracted {X} which he {сделал} too»; «But as he {действие}, it
{угроза}. So he {ответ}»; «By {приём} he {рекорд с числом} until {финал}».
≈ 40 % предложений начинаются с But / So / Now / Then.

Правило картинки: в каждой фразе есть конкретное существительное или действие,
которое можно показать буквально одним планом. Повтор ключевой фразы допустим
1 раз в ролике (под jump-zoom).

Запрещено: приветствия, «в этом видео», «давайте разберёмся», «представьте»,
«удивительно, но», «на самом деле», «итак», «в заключение», любые призывы
подписаться, вопросы к зрителю, шутки в тексте (юмор — в картинке).

Выход: 1) текст по предложениям без ремарок; 2) те же слова, разбитые на
фразы, по одной в строке.
```

### 4.2. Раскадровка (`shot_plan` / `sd_agent` camera+action)

```text
Ты — раскадровщик. Вход: фразы закадра с номерами и таймкодами:
{script}
Режим {pace}: action — почти каждая фраза = новый план (медиана 1.0 с, 0.6–1.8);
explainer — план держится 2–3 фразы (медиана 1.4 с, 0.7–2.9), длинный план
(до 7 с) только для непрерывного действия с перечислением.
Склейка ставится на старте фразы +40 мс. Союз-фраза («But», «So», «With it»)
получает отдельный статичный план 0.4–0.6 с.

Для каждого плана: what (конкретно: герой + предмет + среда {world}),
framing (close 30 % / medium 45 % / full-wide 25 %; aerial 1–2 на ролик),
camera (handheld_drift | static | slow_push_in | follow | crane_up — один
раз), start_state → change → end_state (одно изменение на план),
transition_out (cut; zoom_jump только при повторе фразы),
illustrates (существительное/глагол фразы, показанный буквально — обязательно),
highlight_color (yellow = полезный предмет, green = правильный выбор,
orange = опасность, pink = удар/ошибка, none),
overlay (none | card «{label}» | thought_bubble «{что внутри}» | marker | crown |
lines), gag (преувеличенная реакция героя; ≤ 1 на 10 планов), len_s.

Герой один и тот же во всех планах; второй персонаж — предмет. Нижняя треть
кадра свободна под субтитры. Вставки-карточки появляются на слове-правиле и
перелистываются по следующим фразам.
Верни JSON-массив планов: {i, phrase_ids, what, framing, camera, start_state,
change, end_state, transition_out, illustrates, highlight_color, overlay, gag,
len_s}.
```

### 4.3. Промт картинки (`image_prompts`, `hero`, `items`)

```text
[STYLE] stylized 3D render, cartoon-realistic proportions, clean smooth
shading, realistic materials (wood, water, fabric, wheat), soft natural
daylight, shallow depth of field, slight film grain, vertical 9:16
[CHARACTER] one adult man, short dark hair, light stubble, plain white
crew-neck tee, blue jeans; expressive but simple face
[WORLD] {world} — single consistent environment for the whole video
[LIGHT] soft overcast-sun key from upper left, gentle rim, no harsh shadows
[COMPOSITION] subject centered or slight rule-of-thirds, readable
silhouette, bottom third of frame empty (subtitles), horizon in upper half
[SHOT] {shot.what}; {shot.framing}; {shot.start_state}; the object
"{shot.illustrates}" clearly visible and large
[PALETTE] action/sea: deep navy #1F2957, sky blue #8EC8F2, pale #CFE3F1,
warm wood #856E86; explainer/field: wheat #B18F6B, soil #5C492F, haze
#9EADB1, pale sky #C6CDD0
[NEGATIVE] text, caption, watermark, logo, extra people, photoreal skin
pores, anime, flat 2D, neon, dutch angle, motion blur
```

### 4.4. Промт анимации (`animation_prompts`)

```text
{shot.start_state} → {shot.change} → {shot.end_state}. Duration {shot.len_s + 0.3}s.
Camera: {shot.camera} (handheld drift ≈ 2 % frame, or slow push-in ≤ 10 %,
or static). Subject motion: one clear action, physical, slightly exaggerated
(cartoon timing: anticipation 3 frames, action 6, settle 6).
Keep the character identity and environment identical to the start frame.
Lighting constant. If overlay = thought_bubble/card — leave headroom above
the character.
No: dissolve, morph, speed ramp, whip pan, camera shake, text changes,
extra limbs, flicker, new objects entering unless named in change.
```

### 4.5. Озвучка (`audio`)

```text
Голос: мужской, 25–35, ровный тембр, «спокойный рассказчик», без
эмоциональных пиков и смеха. Темп 170 слов/мин (±5 %). Паузы между
предложениями ≤ 0.15 с, внутри предложения пауз нет. Ударение на
существительных и числах («133 days», «two rules»). Язык: {language}.
TTS: stability 0.6, similarity 0.8, style 0.2. Выход: wav + таймкоды слов
(обязательно — по ним режется монтаж и запускаются подсветки).
```

### 4.6. Субтитры (`assemble`)

```text
Одна строка, 2–4 слова (медиана 3), смена строки = смена фразы (≈ 1 строка/с),
не караоке по словам. Позиция: нижняя треть, центр, базовая линия ≈ 80 %
высоты (полоса 72–86 %). Шрифт: белый, полужирный sans (Montserrat
SemiBold/Inter Bold), размер ≈ 4.5 % высоты кадра, лёгкая тень 2 px, без
подложки и без обводки-«капсулы». Старт/конец строки по таймкодам слов TTS;
строка не переживает склейку более чем на 0.3 с. Логотипы/надписи на одежде
в полосу субтитров не ставить.
```

### 4.7. Монтаж (`assemble` / `vo_sync_edit`)

```text
Монтаж по фразам: склейка на старте фразы +40 мс (не раньше начала слова).
Режим action: cuts/min ≈ 45, медиана плана 1.0 с; explainer: ≈ 25 и 1.4 с.
Переходы: только hard cut. Jump-zoom (scale 1.2, без перехода) только на
повторе фразы или усилении числа. Союз-фраза → план 0.4–0.6 с.
Подсветка предмета (glow/tint по цвет-коду) стартует на таймкоде
существительного, держится 0.3–0.6 с. Вставки-карточки: pop-in 6 кадров на
слове-правиле, перелистывание 8 кадров на следующей фразе-правиле.
Музыка −5.4 dB к голосу, ровная; SFX на change плана, −10 dB.
Хвост после последней фразы 0.6 с, без end card, без CTA.
```

### 4.8. Музыка и SFX (`music`, `sfx_plan`)

```text
Suno: instrumental cinematic-ambient bed, light pulse 90–100 bpm, no vocals,
no drops, steady dynamics (LRA ≤ 3), 70–80 s, loopable. Mix −5.4 dB under
voice, constant across the video (no ducking swells).
SFX: physical foley by shot change — splash, line reel, knife cut, wing
flap, crate thud, wheat rustle, paper pop/flip, soft "pop" for bubbles and
highlights; one SFX per 2–3 shots, −10 dB under voice, no whooshes on cuts.
```

## 5. Приёмный контроль (что проверяет `style_check` / оператор в `hitl_final`)

| проверка | допуск | источник |
|---|---|---|
| cuts/min | action 32–58, explainer 18–33 | `motion.cuts_per_min` ±30 % |
| медиана плана | action 0.7–1.4 с, explainer 1.0–1.9 с | `motion.shot_len_s` |
| склейка на старте фразы | ≥ 0.6 | `linkage.cut_on_phrase_ratio` |
| смещение склейки | +0…+100 мс (не отрицательное) | `linkage.cut_offset_ms_median` |
| слов в строке субтитра | 3 (2–4) | `linkage.words_per_line_median` |
| статичных планов | ≤ 10 % | `motion.static_ratio` |
| музыка к голосу | −4…−7 dB | `music.level_vs_voice_db` |
| речь занимает | ≥ 95 % | `speech.speech_ratio` |
| хвост после речи | ≤ 1 с | `script.tail_silence_s` |
| «существительное в кадре» | ≥ 0.7 планов | vision-проверка агентом `shots` по готовому ролику |
| одна среда, один герой | да | overview-лист |
