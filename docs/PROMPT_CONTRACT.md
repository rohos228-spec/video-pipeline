# Prompt contract — GPT ↔ DB (не Excel)

> Универсальный гайд для агентов и авторов промптов.  
> **SoT данных** = DB (`frames` / `prompt_versions` / apply-ops).  
> Excel (`project.xlsx`) = **export view**, не вход/выход GPT в runtime.

Читать вместе с [`DB_V2.md`](DB_V2.md) (§7 glossary полей) и
[`PROMPTS_BLOCKS.md`](PROMPTS_BLOCKS.md).

---

## 1. Источник правды

| Слой | Роль |
|------|------|
| **DB** | Писать и читать промты, закадр, связи, uuid кадров |
| **Excel** | Экспорт для человека; write-through после DB-правок |
| **R48 / колонки** | Legacy-зеркало; sync из DB, не контракт ответа GPT |

Запрещено учить модель: «приложи xlsx», «заполни лист план»,
`# Лист: …` TSV как основной ответ для `outputMode=project_file`.

---

## 2. Три пути GPT

| Путь | Когда | Контракт ответа |
|------|--------|-----------------|
| **Operator / apply-ops** | Правки кадров, anim_pr, scene_design, excel_gpt с `project_file` | JSON `{"ops":[…]}` (см. DB_V2) |
| **Artifact** | План/сценарий/текст без строк кадров | Текст / структурированный JSON артефакта |
| **Staging JSON** | scene_design агенты → staging cells | JSON в staging; в боевые таблицы пишет только assemble |

**Deprecated:** TSV `# Лист:` / xlsx download writeback для `project_file`.
Код отклоняет TSV и ретраит с apply-ops (`gpt_operator_client`).

Смысл `outputMode=project_file`: писать в **DB проекта**, не в файл Excel.
Имя историческое — не значит «пришли .xlsx».

---

## 3. IO-контракты (кратко)

### apply-ops (основной)

```json
{
  "ops": [
    {
      "frame_uuid": "<24 hex>",
      "fields": { "промт_видео": "…", "закадр": "…" }
    }
  ]
}
```

- Адресация по `frame_uuid` (не по номеру строки Excel).
- Алиасы полей — [`DB_V2.md`](DB_V2.md) §7.
- Пустой `ops: []` = отказ / нет работы → retry, не success.

### Artifact

Свободный текст или JSON без мутации строк кадров (план, music brief…).

### Staging (scene_design)

Агенты пишут в `scene_design_cells`; валидация при записи; assemble → apply-ops.

### Deprecated TSV

```
# Лист: план
...
```

Только legacy-импорт / диагностика. Новые промпты — **не** требовать TSV.

---

## 4. Per-step IO (карточки)

| Шаг | Вход | Выход |
|-----|------|--------|
| plan | topic + master | `general_plan` (текст/JSON), не xlsx |
| script | plan | `script_text` / voiceover |
| split / scene_design | VO + кадры | apply-ops / staging → assemble |
| img_pr / anim_pr | кадры + image_prompt | apply-ops → `prompt_versions` / Frame |
| excel_gpt enrich | инструкции + DB snapshot | apply-ops (`project_file`) |
| check / verdict | картинки + критерии | JSON verdict |

---

## 5. Safe vs legacy

| Safe (использовать) | Legacy (стоп-лист в новых промптах) |
|---------------------|-------------------------------------|
| apply-ops JSON | «приложи обновлённый xlsx» |
| `frame_uuid` + field aliases | «строка 48 / R45 / колонка C» как адрес |
| DB SoT + export Excel | `# Лист:` TSV как основной ответ |
| `outputMode=project_file` = DB | «скачай и перезапиши project.xlsx» |

Миграция `prompts/**` — отдельными PR по семьям. Этот гайд + code footers
сначала; masters/blocks не переписывать оптом.

---

## 6. Failure playbook

| Симптом | Действие |
|---------|----------|
| TSV вместо JSON при `project_file` | Reject + retry «только apply-ops JSON» |
| Empty `ops` | Retry / refusal, не commit |
| SSE cut mid-JSON | Salvage / retry (`gpt_api` / db_apply) |
| uuid near-miss | Match по short uuid / number fallback в apply; логировать |
| Sidecar заполнил anim, UI idle | NodeRun sync (не Project.status из sidecar) |

---

## 7. Code injection (не masters)

Инструкции, которые код дописывает к промпту:

- `ENRICH_DEFAULT_ACCOMPANYING_TEXT` → apply-ops, не xlsx
- `PLAN_XLSX_OUTPUT_FOOTER` → текст/JSON плана, не TSV/xlsx

При добавлении footer/accompanying — сверять с этим контрактом.
