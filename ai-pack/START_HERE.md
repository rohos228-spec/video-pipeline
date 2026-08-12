# С чего начать (для ИИ и человека)

Ты работаешь с программой **video-pipeline** — Studio для коротких вертикальных роликов  
(план → сценарий → кадры → картинки → видеопромты → видео → монтаж).

Эта папка `ai-pack/` — **пакет знаний**. Прикрепи её (или хотя бы этот файл + нужные подпапки) к чату.

---

## 1. Прочитай по порядку (обязательно)

| # | Файл | Зачем |
|---|------|--------|
| 1 | этот `START_HERE.md` | правила игры |
| 2 | `00_core/AGENTS.md` | запуск, GPT API, git, провайдеры |
| 3 | `00_core/AGENT_MAP.md` | карта: куда смотреть в коде |
| 4 | `00_core/video-pipeline-ops.md` | ветки ПК, Studio, anim_pr, диагностика |
| 5 | `01_data_prompts/PROMPT_CONTRACT.md` | как писать/править промпты (DB, не Excel) |
| 6 | `01_data_prompts/DB_V2.md` | кадры, uuid, apply-ops, кнопка «База» |
| 7 | `01_data_prompts/NODE_SYSTEM.md` | ноды оркестратора |
| 8 | `02_ops/OPERATOR_BIBLE.md` | шпаргалка оператора |

Дальше по задаче: `PROMPTS_BLOCKS.md`, `MASS_CREATION.md`, `03_config/env.example`.

---

## 2. Железобетонные правила

1. **Данные:** источник правды = **DB** (`frames`, `prompt_versions`, apply-ops).  
   Excel (`project.xlsx`) = **экспорт / вид**, не ответ GPT.
2. **Ответ GPT для правок кадров:** JSON apply-ops  
   `{"ops":[{"frame_uuid":"…","fields":{…}}]}`  
   Не учить модель «приложи xlsx» / `# Лист:` (это legacy).
3. **Статусы:** sidebar = `Project.status`, canvas = `NodeRun`.  
   Не путать; sidecar anim_pr пишет промты, но **не** двигает Project.status на ready.
4. **Порядок media:** images → **animation_prompts** → videos.  
   Не прыгать в video без video prompts.
5. **Git:** ветка из `.env` → `ORCHESTRATOR_GIT_BRANCH` (`housepc` / `tompc` / … / `main`).  
   После фикса: commit + **push в эту ветку**, в ответе: `git HEAD: <sha> уже в <ветка>`.
6. **Секреты:** никогда не просить и не выдумывать ключи из `.env`.  
   В паке только `03_config/env.example` (пустые плейсхолдеры).

---

## 3. Как чинить баги

1. Открой `00_core/AGENT_MAP.md` → таблица «where is X».
2. Смотри код по ссылкам из карты (в репо рядом с этой папкой).
3. Логи: `data/backend.log`, `data/backend-*.log`.
4. Типовые тесты:  
   `pytest tests/test_status_sequencing_gates.py tests/test_animation_prompt_gpt.py tests/test_chatgpt_attachment_guard.py -q`
5. Studio: `STUDIO.cmd` → `[1]` → http://127.0.0.1:8765  
   Починка: `[6]`, диагностика: `[7]`.

---

## 4. Как работать с промптами

- Контракт IO → `01_data_prompts/PROMPT_CONTRACT.md`
- Blocks / steps / vars → `01_data_prompts/PROMPTS_BLOCKS.md`
- Локальные тексты промптов лежат в `prompts/` **в репо** (часто не в git) — не дублируются сюда.
- Code footers (что код дописывает к GPT): `gpt_text_builder.py`, `chatgpt_xlsx.py` — только apply-ops / текст плана.

---

## 5. Чего в пакете НЕТ (и не должно быть)

- `.env` с ключами  
- `data/state.db`, ролики, PNG/MP4  
- полная копия `prompts/` и `data/library/`  
- секреты Yandex / Telegram / Outsee / Grsai  

Ключи берутся у оператора из локального `.env` на ПК.

---

## 6. Обновить пакет после правок в репо

Из корня video-pipeline:

```text
python scripts/build_ai_pack.py
```

Перезапишет копии в `00_*`…`03_*` и `MANIFEST.txt`.  
`START_HERE.md` и `README.md` скрипт **не** затирает.

---

## 7. Где это лежит

```text
video-pipeline/
  ai-pack/          ← ВОТ ЭТА ПАПКА (кидай ИИ)
  docs/             ← оригиналы (SoT в git)
  app/              ← код
  STUDIO.cmd        ← запуск
```

В Cursor: открой репо целиком — правила из `.cursor/rules` уже подключены;  
этот `ai-pack` удобен, когда нужно **прикрепить файлы во внешний чат**.
