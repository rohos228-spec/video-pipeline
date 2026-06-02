# Спецификация: отказы, очередь, GPT-проверки, музыка

Документ фиксирует требования и план внедрения. Статус: **реализовано (2026-05)**.

---

## 1. Отказы по нодам (не «полный провал»)

### Требование

| Событие | Действие |
|---------|----------|
| Ошибка шага (1–2 раза) | Лог + TG, **повтор того же running-статуса** |
| 3 ошибки подряд на одном шаге | **Обнуление шага** (`reset_step`), **сон 30 мин**, счётчик «циклов восстановления» +1 |
| После сна | Снова тот же шаг с нуля |
| 3 цикла «3 отказа → сон» | **Завершить проект** (paused/abandoned), **следующий в gen_queue** |

**Глобальный счётчик:** до **9 reset-попыток** на шаг (3 fail × 3 цикла).  
Каждый fail → reset; fail #3 и #6 → + sleep 30 min; fail #9 → abandon.

**Не использовать** `ProjectStatus.failed` (блокирует меню).

### Уже есть

- `app/main.py`: `MAX_FAIL=3`, откат на **предыдущий** ready-статус (не совпадает с ТЗ).
- `app/services/reset_step.py`: обнуление артефактов шага.

### Реализация

- `app/services/step_failure_policy.py` — политика из таблицы выше, meta: `fail_count`, `recovery_cycles`, `sleep_until`.
- Подключить в `main.py` вместо отката на prerequisite.
- Маппинг running → step_code для reset: через `step_by_running_status`.

---

## 2. Проверка после ноды (файлы + Excel)

### Требование (в конце шага)

| Нода | Проверки | При fail |
|------|----------|----------|
| **Видео** | Лист «план»: число кадров, нет дубликатов, каждый кадр имеет клип | Переген **конкретного** кадра |
| **Картинки** | То же по scene_image | Переген кадров без файла |
| **Музыка** | Файл в `music/` | Переген музыки |
| **Аудио** | voiceover, subs в папках | Переген аудио |

### Уже есть

- `app/services/step_data_guard.py` — вход на running (не post-check).
- `artifact_recovery.py` — восстановление с диска.

### Реализация

- `app/services/post_step_validate.py`:
  - `validate_after_videos` / `images` / `music` / `audio`
  - сверка с `read_v8_active_frame_count`, список missing frame_ids
  - вызов точечной перегенерации (Outsee/ElevenLabs) или повтор running только для missing
- Вызов в конце `generate_videos.py`, `generate_images.py`, `generate_music.py`, `generate_audio.py` **после** основного цикла.

---

## 3. Непрерывная очередь проектов

### Требование

- Следующий проект **обязан** стартовать, **простой недопустим**.
- Каждый новый проект в очереди сайдбара попадает в генерацию.

### Уже есть

- `app/services/gen_queue.py`, `gen_queue_tick`, очередь в sidebar API.

### Доработки

- После `abandon_project` в failure policy → `on_project_timeline_maybe_advance_queue` / `gen_queue_tick`.
- Если текущий в `*_ready` и `auto_mode` — `auto_advance` без ожидания TG (уже частично).
- Worker: если очередь не пуста и никто не busy — форсировать tick (интервал 5 с в `main.py`).

---

## 4. GPT-проверки (формат «Вердикт»)

### Общий UX (Studio)

На каждой ноде с проверкой показывать как в других нодах:

- прикрепляемые **файлы** (Excel / voiceover / референсы);
- **текст промта** в поле ввода;
- ответ GPT с парсингом:

```
Вердикт: Одобрено (все хорошо)
Вердикт: Не одобрено: <текст>
```

При «Не одобрено» → новый чат: исходный файл + «исправь … согласно требованиям: <текст вердикта>» → повторная проверка.

### По нодам

| Нода | В GPT | Повтор при отказе |
|------|-------|-------------------|
| Сценарий | Excel + промт | исправь Excel |
| Закадровый | voiceover + промт | исправь файл |
| Разбивка | Excel + промт | исправь Excel |
| Персонажи | промт → ответ → 5 ref + id/описание/промт → id/вердикт | перепиши промт по вердикту |
| Предметы | как персонажи | то же |
| Промты картинок | Excel + промт | исправь Excel |
| Картинки | сначала **все** материалы, потом переген только fail | как персонажи |
| Промты анимации | Excel + промт | исправь Excel |
| Озвучка, Музыка, Сборка | **без GPT-проверки** | — |

### Уже есть

- `app/services/auto_review.py` — JSON-ревью для auto_mode (другой формат).
- `web/src/components/studio/gpt-text-panel.tsx` — UI GPT-текста.

### Реализация

- `app/services/gpt_verdict_review.py` — парсер «Вердикт:», цикл fix+recheck.
- `prompts/check_*` — дополнить шаблонами с форматом вердикта.
- Studio: вкладка «Проверка GPT» на нодах (переиспользовать `gpt-text-panel` + preview attachments).
- Связать с HITL / auto_advance: `regen` → reset шага + fix prompt.

---

## 5. Нода «Музыка»

### Уже есть (частично)

- `app/orchestrator/steps/generate_music.py` — GPT → Outsee Suno.
- **Нет** в `ProjectStatus`, `ArtifactKind`, `pipeline.py`, web `NODE_CATALOG`.

### Реализация (фаза 1)

1. `models.py`: `generating_music`, `music_ready`, `ArtifactKind.music`.
2. `pipeline.py` + `main.py` active + `gen_queue` busy + `auto_advance` TRANSITIONS (audio → music → assemble или по графу).
3. `telegram/menu.py`: step `music`.
4. Web: `node-catalog.ts`, `node-step-map.ts`, `node-prompts.ts` (voiceover + gpt_text).
5. `scripts/verify-music-node.ps1` — зелёный прогон.

---

## Порядок работ (рекомендуемый)

1. **step_failure_policy** + очередь при abandon  
2. **Музыка** — модели + pipeline + UI  
3. **post_step_validate** — video/images/music/audio  
4. **gpt_verdict_review** + Studio UI  
5. E2E + `run-final-qa.py` расширить  

---

## Файлы

| Файл | Назначение |
|------|------------|
| `app/services/step_failure_policy.py` | отказы, сон, abandon |
| `app/services/post_step_validate.py` | post-check кадров/файлов |
| `app/services/gpt_verdict_review.py` | вердикт-текст GPT |
| `docs/SPEC-RELIABILITY-QUEUE-GPT-MUSIC.md` | этот документ |
