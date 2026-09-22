# Формат Veo

Сформируй один готовый промт для текущего кадра под Veo. Не выдавай варианты, списки решений или рассуждения.

Промт должен содержать:
- краткое описание исходного кадра;
- длительность до `{{VAR:VIDEO_DURATION_MAX_SEC}}` сек;
- видимое действие на `{{VAR:FRAME_DURATION_MIN_SEC}}–{{VAR:FRAME_DURATION_MAX_SEC}}` сек;
- движение камеры;
- движение переднего, среднего и дальнего плана;
- атмосферу/свет;
- эмоциональный тон;
- запреты в конце через `--no`.

Формат:

```
[номер]. [цельное описание анимации одним абзацем; NO VOICE; single continuous take; only existing props]
--no voice, speech, dialogue, lip-sync, narration, text, subtitles, logos, watermarks, abrupt cuts, shot changes, scene changes, montage, multi-shot, style shift, added characters, new objects, new props, camera shake
```

Не используй markdown-таблицы. Не объясняй, почему выбрал движение. Результат должен быть готов для копирования в генератор.
Жёстко: NO VOICE; без новых предметов; без смены кадров (один continuous take).
