# Контракт результатов действий R2 (моки WS4)

Версия: `ws2-r2.3` (`CONTRACT_VERSION` в `app/orchestrator/action_contracts/contracts.py`).
sha256 схемы: `d341d5b286af170c43b122fa0c5f57b1c3ce43040305443113622b7c13fd7d10`.

## Изменения r2.2 → r2.3

- Схемы живут в `app/orchestrator/action_contracts`, не в пакете реестра. Приёмка больше не пропускает реестр по `CONTRACT_VERSION`.
- Флаги 102 зафиксированы в `audit_metadata.json`. Markdown при импорте не читается.
- `partial=false` вместе с непустым `skipped` — ошибка, а не тихий `partial=true`.
- `reject_stale_edit`: и `base_version` из будущего, и устаревшая база дают `failed` / `conflict`.
- `create_node@new_group` — риск `high` и `destructive`. Метаданные можно только ужесточить.
- Проверка html — детектор `_SCRIPT_DETECTOR`, не санитайзер.

## Изменения r2.1 → r2.2

- `EditResult.version` строго больше `base_version`. Равные номера не проходят.
- `snapshot_id` и `archive_ref` не бывают пустой строкой или строкой из пробелов.
- Непустой `skipped` у `EditResult` и `SoftDeleteResult` даёт `partial: true`.

Это только схемы. Исполнителей, записи в node_store и HTTP здесь нет.
Имена взяты из плана `fable-context` §6 и из `docs/fable/actions_audit.md`.
К ним добавлены только восстановление мягкого удаления: `frame.restore`,
`group.restore`, `prompt.restore_master`.

Живой реестр аудита — 102 действия. Их флаги лежат в
`app/orchestrator/action_contracts/audit_metadata.json`, не читаются из
markdown при импорте. Пакет схем не регистрирует исполнителей.
`metadata_of` знает эти 102 и имена R2, чтобы имя вне списков не выглядело безопасным.

## Поля

### `ActionMetadata`

| Поле | Смысл |
|---|---|
| `name` | id действия |
| `risk` | шкала плана: `none` / `low` / `medium` / `high` |
| `read_only` | ярус `read` |
| `destructive` | в ярусе есть `destructive` |
| `paid` | в ярусе есть `paid` |

`needs_approval` — не поле провода. Это `paid or destructive`.

Имени нет ни в 102, ни в списке R2 ниже: `metadata_of` возвращает
`risk=high`, `read_only=false`, `paid=true`, `destructive=true`.
Такое действие нужно подтвердить.

Особые строки, не как сырая ячейка таблицы #344:

- `revert_node_to_git` — риск `medium`, ярус `destructive` (раздел аудита «Новые действия R2»). Деактивирует версию в БД, историю не стирает, git-файл не пишет. Подтверждение нужно.
- `prompt.create_master` — ярус таблицы `destructive`. Контракт держит `destructive=true` и не ослабляет его, даже если ячейка снова станет `local_safe`.
- `montage.suggest_scene_variants` — ярус `paid`. Если в таблице снова окажется `paid+paid`, контракт хранит `paid=true`, `destructive=false`.

### `EditResult`

Правка ноды, промта, параметров или отчёта.

| Поле | Смысл |
|---|---|
| `action` | id из реестра метаданных |
| `change_id` | только сервер, форма WS3 #358: `orch-appr-<task_id>-<n>` |
| `node_ref.project_id` | id проекта |
| `node_ref.node_id` | для `node` / `prompt` / `params` — `node_key`; для `report` — id отчёта строкой |
| `node_ref.kind` | `node` / `prompt` / `params` / `report` |
| `before` / `after` | значение до и после. Без `before` нужен `snapshot_id` |
| `snapshot_id` | снимок прежнего значения, если `before` не передан. Пустая строка и пробелы недействительны |
| `version` | номер версии после успешной правки, целое ≥ 1, строго больше `base_version` |
| `base_version` | версия, которую читал писатель, целое ≥ 1 |

`update_report` — тот же `EditResult`, поэтому версия и база обязательны и у отчёта.
Если `base_version` не равна текущей (и прошлое, и будущее), данные не меняются:
конверт `failed`, `error` равен `conflict`, HTTP 409. Это не повтор `change_id`.
| `diff` | ровно одна форма: `unified` (текст) или `fields` (ключ → `{op, before, after}`). `op=removed` — ключ исчез, `op=set` и `after=null` — значение стало null |
| `open_url` | куда чат открывает результат |
| `reversible` | откат тем же change_control возможен |

### `ReportResult`

`html` проходит только детектор явного script / `javascript:` / `on*=`.
Это не санитайзер: `<svg/onload=…>`, `<img/onerror=…>` и `&#106;avascript:` он не видит.

| Поле | Смысл |
|---|---|
| `report_id` | id отчёта |
| `html` | санитизированный фрагмент, без скриптов |
| `version` | номер версии |
| `edit_action` | имя правки отчёта через чат: `update_report` (план §6.3) |

Кнопка «Править» в отчёте шлёт `edit_action` сообщением в чат.
DOM отчёта на месте не правится.

### `ApprovalRequired`

Запрос на одобрение, не карточка. Поля `change_id` здесь нет.
Лишнее поле, включая `change_id`, схема отвергает.

Карточку в UI даёт ядро WS3 событиями `approval_required` и `approval_decided`
(ws3-r3.4). `ActionResult` со `status=approval_required` только сообщает ядру,
что одобрение нужно. Из этого конверта карточку не собирают.

Первый ответ, если `metadata.paid` или `metadata.destructive`
(в том числе имя вне реестра).

| Поле | Смысл |
|---|---|
| `action` | id действия |
| `metadata` | `ActionMetadata` этого действия |
| `reason` | почему нужно подтверждение |
| `preview.change` | что изменится |
| `preview.estimate` | `null` — оценки нет, `0` — бесплатно. У платного действия либо число > 0, либо `null` |

Риск `medium` сам по себе этот статус не включает.
`create_node` в уже существующую группу и `create_report` не платные и не деструктивные: конверт сразу `ok`.
`create_node@new_group` — риск `high`, `destructive`: сначала подтверждение.
`edit_node_prompt`, `edit_node_params`, `update_report`, `revert_node_to_git`
деструктивные: сначала `approval_required`.

### `SoftDeleteResult`

Пометка вместо физического удаления. Так аудит оставляет
`group.delete`, `montage.delete_child_frame`, `montage.delete_frame`,
`prompt.delete_master`.

| Поле | Смысл |
|---|---|
| `change_id` | серверный id пометки |
| `marked` | при `status=ok` только `true`: строка и файл на месте. `false` при `ok` — ошибка валидации |
| `action` | id мягкого удаления |
| `restore_action` | действие, которое возвращает именно этот kind |
| `snapshot_id` | id снимка, из которого снимают пометку |
| `archive_ref` | путь `old/…`, если снимка-id нет |

Нужно хотя бы одно из `snapshot_id` и `archive_ref`. Пустая строка и строка из пробелов не считаются ссылкой.

`history.rollback` эти kind не возвращает. Пара фиксирована:

| Удаление | Восстановление |
|---|---|
| `montage.delete_frame` | `frame.restore` |
| `montage.delete_child_frame` | `frame.restore` |
| `group.delete` | `group.restore` |
| `prompt.delete_master` | `prompt.restore_master` |

Восстановление само `destructive`: снова через подтверждение.

### `ActionResult`

| Поле | Смысл |
|---|---|
| `status` | `ok` / `approval_required` / `failed` |
| `result` | тело успеха или подтверждения; у `failed` — `null` |
| `error` | текст при `failed`; иначе `null` |
| `warnings` | список строк, по умолчанию пустой. Частичный успех не молчит |

У `EditResult` и `SoftDeleteResult` есть `skipped` (список id) и `partial`.
Непустой `skipped` при `partial: false` — ошибка. `partial: true` без `skipped` тоже.

`change_id` клиент не придумывает. Повтор того же id даёт 409,
чужой id (другая задача или сессия) и выдуманный id дают 404.
Так же у WS3 в #358: id хранит сервер, повторное решение по нему не
исполняется второй раз.

Платное или деструктивное действие сначала отдаёт `approval_required`.
Карточку рисует ядро. `change_id` в последующем `ok` (правка или пометка)
по-прежнему выдаёт только сервер.

## Имена R2 в метаданных

Из плана §6, плюс `revert_node_to_git` из аудита. В 102 их нет.

| Действие | risk | read_only | destructive | paid |
|---|---|---|---|---|
| `read_pipeline_code` | none | true | false | false |
| `rag_search` | none | true | false | false |
| `run_validators` | none | true | false | false |
| `stop_node` | medium | false | false | false |
| `resume_node` | high | false | true | true |
| `create_node` | medium | false | false | false |
| `create_node@new_group` | high | false | true | false |
| `edit_node_prompt` | medium | false | true | false |
| `edit_node_params` | medium | false | true | false |
| `create_report` | low | false | false | false |
| `update_report` | low | false | true | false |
| `export_nodes_snapshot` | low | true | false | false |
| `revert_node_to_git` | medium | false | true | false |
| `frame.restore` | high | false | true | false |
| `group.restore` | high | false | true | false |
| `prompt.restore_master` | high | false | true | false |

`resume_node` не слабее `project.resume`: риск `high`, `destructive` и `paid`.
`export_nodes_snapshot` по плану read-only относительно рантайма нод.

Ниже — по одному конверту на действие, для которого WS4 рисует результат.
Все блоки `json` — значение `ActionResult`.

## `create_node`

Создание ноды. `kind` = `node`. Группа в примере уже есть, риск остаётся `medium`.

```json
{
  "status": "ok",
  "result": {
    "action": "create_node",
    "change_id": "orch-appr-13-1",
    "node_ref": {"project_id": 13, "node_id": "n_scene_writer", "kind": "node"},
    "before": null,
    "snapshot_id": "snap-13-1",
    "version": 2,
    "base_version": 1,
    "after": {"title": "Сцены", "group": "script_frames_qc"},
    "diff": {"fields": {"title": {"before": null, "after": "Сцены"}}},
    "open_url": "/api/projects/13",
    "reversible": true
  },
  "error": null
}
```

## `edit_node_prompt`

Правка промта. `kind` = `prompt`. Git-файл не пишется: новая версия в БД (D3).

```json
{
  "status": "ok",
  "result": {
    "action": "edit_node_prompt",
    "change_id": "orch-appr-13-2",
    "node_ref": {"project_id": 13, "node_id": "n_scene_writer", "kind": "prompt"},
    "before": {"prompt_template": "было"},
    "version": 2,
    "base_version": 1,
    "after": {"prompt_template": "стало"},
    "diff": {"unified": "--- a/prompt\n+++ b/prompt\n@@\n-было\n+стало\n"},
    "open_url": "/api/projects/13",
    "reversible": true
  },
  "error": null
}
```

## `edit_node_params`

Правка параметров ноды. `kind` = `params`.

```json
{
  "status": "ok",
  "result": {
    "action": "edit_node_params",
    "change_id": "orch-appr-13-3",
    "node_ref": {"project_id": 13, "node_id": "n_scene_writer", "kind": "params"},
    "before": {"temperature": 0.2},
    "version": 3,
    "base_version": 2,
    "after": {"temperature": 0.4},
    "diff": {"fields": {"temperature": {"before": 0.2, "after": 0.4}}},
    "open_url": "/api/projects/13",
    "reversible": true
  },
  "error": null
}
```

## `create_report`

Отчёт для чата. Кнопка «Править» берёт `edit_action` и шлёт его в чат.

```json
{
  "status": "ok",
  "result": {
    "report_id": 7,
    "html": "<h1>Сводка</h1><p>Кадры на месте.</p>",
    "version": 1,
    "edit_action": "update_report"
  },
  "error": null
}
```

## `update_report`

Правка отчёта тем же `edit_action`. Ответ — diff, `kind` = `report`.
Новый HTML в `after` уже без скриптов. DOM на месте не меняется.

```json
{
  "status": "ok",
  "result": {
    "action": "update_report",
    "change_id": "orch-appr-13-4",
    "node_ref": {"project_id": 13, "node_id": "7", "kind": "report"},
    "before": {"html": "<h1>Сводка</h1><p>Кадры на месте.</p>"},
    "version": 2,
    "base_version": 1,
    "after": {"html": "<h1>Сводка</h1><p>Кадры сверены.</p>"},
    "diff": {"unified": "--- a/report\n+++ b/report\n@@\n-<p>Кадры на месте.</p>\n+<p>Кадры сверены.</p>\n"},
    "open_url": "/api/orchestrator/reports/7",
    "reversible": true
  },
  "error": null
}
```

## `revert_node_to_git`

Риск `medium`, ярус `destructive`. Сначала подтверждение.
Следующий `resolve_node` видит `source=git`. Откат — снова активировать ту же версию в БД.

```json
{
  "status": "ok",
  "result": {
    "action": "revert_node_to_git",
    "change_id": "orch-appr-13-5",
    "node_ref": {"project_id": 13, "node_id": "n_scene_writer", "kind": "node"},
    "before": {"source": "db", "db_version": 4},
    "version": 5,
    "base_version": 4,
    "after": {"source": "git", "db_version": null},
    "diff": {"fields": {"source": {"op": "set", "before": "db", "after": "git"}, "db_version": {"op": "removed", "before": 4}}},
    "open_url": "/api/projects/13",
    "reversible": true
  },
  "error": null
}
```

## Мягкое удаление

`prompt.delete_master` деструктивный, поэтому сначала подтверждение.
Тот же порядок у `group.delete`, `montage.delete_child_frame` и
`montage.delete_frame`: физически ничего не удаляется, в `ok` приходит пометка.

```json
{
  "status": "approval_required",
  "result": {
    "action": "prompt.delete_master",
    "metadata": {
      "name": "prompt.delete_master",
      "risk": "high",
      "read_only": false,
      "destructive": true,
      "paid": false
    },
    "reason": "Удаление общего промта необратимо без снимка",
    "preview": {
      "change": "Пометить вариант промта удалённым, файл не стирать",
      "estimate": 0
    }
  },
  "error": null
}
```

```json
{
  "status": "ok",
  "result": {
    "change_id": "orch-appr-13-6",
    "action": "prompt.delete_master",
    "marked": true,
    "restore_action": "prompt.restore_master",
    "snapshot_id": "snap-13-6"
  },
  "error": null
}
```

Повторный `change_id` `orch-appr-13-6` даёт 409. Чужой id даёт 404.

## Платное и запись общего файла

`montage.suggest_scene_variants` — `paid`, не `paid+paid`. Сначала подтверждение.

```json
{
  "status": "approval_required",
  "result": {
    "action": "montage.suggest_scene_variants",
    "metadata": {
      "name": "montage.suggest_scene_variants",
      "risk": "high",
      "read_only": false,
      "destructive": false,
      "paid": true
    },
    "reason": "Платный вызов LLM",
    "preview": {
      "change": "Один вызов LLM за вариантами сцены",
      "estimate": null
    }
  },
  "error": null
}
```

`prompt.create_master` в контракте деструктивный: сначала подтверждение.

```json
{
  "status": "approval_required",
  "result": {
    "action": "prompt.create_master",
    "metadata": {
      "name": "prompt.create_master",
      "risk": "medium",
      "read_only": false,
      "destructive": true,
      "paid": false
    },
    "reason": "Общий промт пишется для всех проектов",
    "preview": {
      "change": "Новая версия общего промта в БД, файл git не создавать",
      "estimate": 0
    }
  },
  "error": null
}
```

## Сбой

```json
{
  "status": "failed",
  "result": null,
  "error": "нода n_scene_writer уже есть"
}
```
