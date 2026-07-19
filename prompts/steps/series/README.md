# Сериальные агенты (полные промпты)

Шаблон Excel: `templates/project_template_series_v1.xlsx`  
Шпаргалка оператора: `docs/SERIES_OPERATOR_CHEATSHEET.md`  
Камера: `docs/SERIES_CAMERA_ANGLES.md`  
Карта листов: `docs/SERIES_XLSX_WORKBOOK.md`

## Важно

Рабочий файл этапа — **`agent.txt`** в каждой папке (обычно 18–35 тыс. символов).  
Файл `template.md` — только указатель. В ChatGPT/Studio вставляй именно `agent.txt`.

Формат как у промышленных XLSX-агентов: роль, связь с другими агентами, что читать/писать, запреты строк, порядок работы, примеры, самопроверка.

## Порядок и потоки данных

```text
ser_01_bible          → Сезон (библия)
ser_02_season_map     → Сезон (карта серий, арки)
ser_03_episode_outline→ Общий план
ser_04_teleplay       → Сценарий          ※ не R49
ser_05_dialogue_polish→ Сценарий (речь)
ser_06_continuity     → Непрерывность
ser_07_characters     → Персонажи
ser_08_locations      → Фоны
ser_09_items          → Предметы
ser_10_breakdown      → план (сцены)
ser_11_coverage       → план (кадр1/2/3, R60–63) + привязка в Сценарий
ser_12_image_prompts  → план R45–47
ser_images            → файлы + R66
ser_13_anim_prompts   → план R48/R64/R65
ser_videos            → файлы + R66
ser_14_dialogue_audio → Звук (речь)
ser_15_music_sfx      → Звук (музыка/шумы)
ser_16_assemble       → Монтаж
ser_17_picture_lock   → замок
ser_18_final_mix      → финальный звук
ser_20_qc             → отчёт приёмки
ser_19_deliver        → выпуск + канон
```

Стоп-краны человека: после 01, 02, 03, 04, 06(если БЛОК), 07/08, 11, картинок, видео, 14, 16, 17, 18, 20, 19.

## Папки

| Папка | Агент |
|-------|--------|
| ser_01_bible … ser_20_qc | см. `agent.txt` |
| ser_images / ser_videos | операционные runbook-агенты генерации |

Проверка размера: каждый `agent.txt` ≥ 10000 байт.
