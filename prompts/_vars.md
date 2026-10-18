# Справочник переменных {{VAR:...}}

| Переменная | Описание | Дефолт |
|------------|----------|--------|
| `VIDEO_DURATION_SEC` | Длина ролика, сек | 60 |
| `VOICEOVER_MIN_CHARS` | Мин. знаков озвучки | 800 |
| `VOICEOVER_MAX_CHARS` | Макс. знаков озвучки | 900 |
| `BLOCK_LEN_MIN_CHARS` | Мин. длина блока разбивки | 45 |
| `BLOCK_LEN_MAX_CHARS` | Макс. длина блока разбивки | 100 |
| `ASPECT_RATIO_VIDEO` | Соотношение кадра видео | 9:16 |
| `ASPECT_RATIO_HERO` | Character sheet | 16:9 |
| `HERO_DESCRIPTION` | Описание героя из проекта | — |
| `PROJECT_TOPIC` | Тема ролика | — |
| `PROMPT_LEN_MIN` / `PROMPT_LEN_MAX` | Длина image-prompt | 500–4800 |
| `VIDEO_DURATION_MAX_SEC` | Макс. клип Veo | 8 |
| `FRAME_DURATION_MIN_SEC` / `FRAME_DURATION_MAX_SEC` | Длительность клипа после обрезки FFmpeg | 2–4 |
| `ITEM_STYLE_NOTE` | Доп. заметка о стиле для реф-картинок предметов (шаг 4b) | — |
| `ENRICH_1_TASK` … `ENRICH_5_TASK` | Задача для соотв. слота «Доп. работа с Excel» | — |
| `ENRICH_1_SHEET` … `ENRICH_5_SHEET` | Лист xlsx, с которым работает соотв. слот | «план» |

См. также `docs/PROMPTS_BLOCKS.md` — там же описан формат весов блоков
(`{{BLOCK:cat}}` может резолвиться в файл или в свой текст, с весом 0–1).
