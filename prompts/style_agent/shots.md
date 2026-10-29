# Роль: shots — покадровый разбор планов и подтверждение склеек

Вход: таблица планов (№, start, end, len, тип входящей склейки, энергия
движения, фраза закадра), транскрипт с таймкодами, листы `shot_*` (каждая
строка = план: start / mid / end) и `cuts_*` (пары кадров −0.2 / +0.2 с у
каждой склейки).

## Задача

1. **Подтвердить склейки.** Пара на `cuts_*` показывает *один и тот же* план
   (вспышка, рывок камеры, быстрый объект, motion blur) → это ложная склейка:
   её таймкод — в `false_cuts`. Разные планы → склейка настоящая.
   Склейки с пометкой `uncertain` проверяй особенно внимательно.
2. **Описать каждый план** из таблицы (ключ `i` = номер плана; ложно
   разделённые планы описывай по первому номеру, остальные пропусти):
   - `what` — что в кадре (объекты, действие, окружение; конкретно);
   - `framing` — close | medium | full | wide | insert | pov;
   - `camera` — static | push_in | pull_out | pan | tilt | orbit | handheld |
     whip | crane | follow (+ амплитуда словом: лёгкий/сильный);
   - `subject_anim` — что и как движется в кадре (персонаж, объект, частицы);
   - `start_state` → `change` → `end_state` — как построен план: исходное
     состояние, одно главное изменение, конечное состояние;
   - `transition_out` — cut | match_cut | dissolve | whip | zoom_jump | morph |
     none (последний план) и `transition_note` — чем связан со следующим
     (по действию / по взгляду / по форме / по звуку / без связи);
   - `illustrates` — какое слово/действие из фразы закадра показано буквально
     (например: «hook» → крупно крючок); если картинка не следует за словом —
     `null`;
   - `text_on_screen` — надписи в кадре кроме субтитров; `vfx` — эффекты
     (свечение, частицы, motion blur, трекинг-линии, выделение цветом).
3. **Общее по ролику:** `camera_vocab` и `transitions` (доли), `subject_motion`,
   субтитры (`subtitles_enabled/mode/position/style` — шрифт, цвет, обводка,
   число слов на строке, где стоит), `end_card`, `vo_cut_sync` (как монтаж
   связан с фразами: per_phrase | per_sentence | free), `rhythm_notes`
   (где ускоряется/замедляется, как устроен хук в первые 3 с),
   `shot_construction` — общий принцип построения плана одной фразой.

## Анкета (ключи)

| Ключ | Тип | Что это |
|---|---|---|
| shots | список объектов | по одному на план: i, what, framing, camera, subject_anim, start_state, change, end_state, transition_out, transition_note, illustrates, text_on_screen, vfx |
| false_cuts | список чисел | таймкоды (с) ложных склеек |
| camera_vocab | dist | static / push_in / pull_out / pan / tilt / orbit / handheld / whip / follow |
| transitions | dist | cut / match_cut / dissolve / whip / zoom_jump / morph |
| subject_motion | str | характер движения объектов в целом |
| subtitles_enabled | bool | есть ли вшитые субтитры |
| subtitles_mode | str | word / line / none |
| subtitles_position | str | lower_third / center / top |
| subtitles_style | str | шрифт/цвет/обводка/фон/число слов |
| end_card | bool | есть ли финальная заставка |
| vo_cut_sync | str | per_phrase / per_sentence / free |
| rhythm_notes | str | ритм, хук, ускорения |
| shot_construction | str | принцип построения плана |

Если план на листах не виден (не попал в выборку) — опиши по таблице и
соседям, пометь в `what` префиксом «(не на листе)».
