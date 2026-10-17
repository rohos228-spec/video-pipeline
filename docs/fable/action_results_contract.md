# Контракт результатов действий R2 (моки WS4)

Версия: `ws2-r2.0` (`CONTRACT_VERSION` в `app/orchestrator/actions/contracts.py`).

Это только схемы. Исполнителей, записи в node_store и HTTP здесь нет.
Имена взяты из плана `fable-context` §6 и из `docs/fable/actions_audit.md`.
Новых id сверх этих списков нет.

Живой реестр аудита — 102 действия. Пакет `app.orchestrator.actions` их не
регистрирует как исполнителей. `metadata_of` знает эти 102 и имена R2 из
плана, чтобы имя вне обоих списков не выглядело безопасным.

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

- `revert_node_to_git` — риск `medium` (раздел аудита «Новые действия R2»). Не платное и не деструктивное: деактивирует версию в БД, историю не стирает, git-файл не пишет.
- `prompt.create_master` — в таблице ярус `local_safe`, в контракте `destructive=true`: действие пишет общий файл промта.
- `montage.suggest_scene_variants` — в коде ярус `paid`. В таблице #344 опечатка `paid+paid`. Контракт хранит `paid=true`, `destructive=false` и документ аудита не переписывает.

### `EditResult`

Правка ноды, промта, параметров или отчёта.

| Поле | Смысл |
|---|---|
| `action` | id из реестра метаданных |
| `change_id` | только сервер, форма WS3 #358: `orch-appr-<task_id>-<n>` |
| `node_ref.project_id` | id проекта |
| `node_ref.node_id` | для `node` / `prompt` / `params` — `node_key`; для `report` — id отчёта строкой |
| `node_ref.kind` | `node` / `prompt` / `params` / `report` |
| `before` / `after` | значение до и после; у создания `before` равен `null` |
| `diff` | ровно одна форма: `unified` (текст) или `fields` (ключ → `{before, after}`) |
| `open_url` | куда чат открывает результат |
| `reversible` | откат тем же change_control возможен |

### `ReportResult`

HTML, который сервер уже отдал санитайзеру. Скрипт в `html` модель не принимает.

| Поле | Смысл |
|---|---|
| `report_id` | id отчёта |
| `html` | санитизированный фрагмент, без скриптов |
| `version` | номер версии |
| `edit_action` | имя правки отчёта через чат: `update_report` (план §6.3) |

Кнопка «Править» в отчёте шлёт `edit_action` сообщением в чат.
DOM отчёта на месте не правится.

### `ApprovalRequired`

Первый ответ, если `metadata.paid` или `metadata.destructive`
(в том числе имя вне реестра).

| Поле | Смысл |
|---|---|
| `change_id` | серверный id этого подтверждения |
| `metadata` | `ActionMetadata` действия |
| `summary` | что произойдёт после подтверждения |
| `estimate` | оценка расхода, число ≥ 0 |

Риск `medium` сам по себе этот статус не включает.
`create_node`, `edit_node_prompt`, `edit_node_params`, `create_report`,
`update_report`, `revert_node_to_git` не платные и не деструктивные:
конверт сразу `ok`.

### `SoftDeleteResult`

Пометка вместо физического удаления. Так аудит оставляет
`group.delete`, `montage.delete_child_frame`, `montage.delete_frame`,
`prompt.delete_master`.

| Поле | Смысл |
|---|---|
| `change_id` | серверный id пометки |
| `marked` | при `status=ok` только `true`: строка и файл на месте. `false` при `ok` — ошибка валидации |
| `restore_action` | id из реестра, которым чат снимает пометку |
| `snapshot_id` | id снимка, из которого снимают пометку |
| `archive_ref` | путь `old/…`, если снимка-id нет |

Нужно хотя бы одно из `snapshot_id` и `archive_ref`. Без ссылки на снимок или архив результат невалиден.

Отдельного id «восстановить» план не вводит. В моках поле равно
`history.rollback`: это действие уже есть в реестре и возвращает прежнее
значение из снимка.

### `ActionResult`

| Поле | Смысл |
|---|---|
| `status` | `ok` / `approval_required` / `failed` |
| `result` | тело успеха или подтверждения; у `failed` — `null` |
| `error` | текст при `failed`; иначе `null` |

`change_id` клиент не придумывает. Повтор того же id даёт 409,
чужой id (другая задача или сессия) и выдуманный id дают 404.
Так же у WS3 в #358: id хранит сервер, повторное решение по нему не
исполняется второй раз.

Платное или деструктивное действие сначала отдаёт `approval_required`.
После подтверждения тот же серверный `change_id` приходит уже в `ok`.

## Имена R2 в метаданных

Из плана §6, плюс `revert_node_to_git` из аудита. В 102 их нет.

| Действие | risk | read_only | destructive | paid |
|---|---|---|---|---|
| `read_pipeline_code` | none | true | false | false |
| `rag_search` | none | true | false | false |
| `run_validators` | none | true | false | false |
| `stop_node` | medium | false | false | false |
| `resume_node` | medium | false | false | true |
| `create_node` | medium | false | false | false |
| `edit_node_prompt` | medium | false | false | false |
| `edit_node_params` | medium | false | false | false |
| `create_report` | low | false | false | false |
| `update_report` | low | false | false | false |
| `export_nodes_snapshot` | low | true | false | false |
| `revert_node_to_git` | medium | false | false | false |

`resume_node` платный: план §6.2 вешает на него paid-retry.
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
    "after": {"html": "<h1>Сводка</h1><p>Кадры сверены.</p>"},
    "diff": {"unified": "--- a/report\n+++ b/report\n@@\n-<p>Кадры на месте.</p>\n+<p>Кадры сверены.</p>\n"},
    "open_url": "/api/orchestrator/reports/7",
    "reversible": true
  },
  "error": null
}
```

## `revert_node_to_git`

Риск `medium`. Подтверждение не требуется: действие не платное и не деструктивное.
Следующий `resolve_node` видит `source=git`. Откат — снова активировать ту же версию в БД.

```json
{
  "status": "ok",
  "result": {
    "action": "revert_node_to_git",
    "change_id": "orch-appr-13-5",
    "node_ref": {"project_id": 13, "node_id": "n_scene_writer", "kind": "node"},
    "before": {"source": "db", "db_version": 4},
    "after": {"source": "git", "db_version": null},
    "diff": {"fields": {"source": {"before": "db", "after": "git"}}},
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
    "change_id": "orch-appr-13-6",
    "metadata": {
      "name": "prompt.delete_master",
      "risk": "high",
      "read_only": false,
      "destructive": true,
      "paid": false
    },
    "summary": "Пометить вариант промта удалённым, файл не стирать",
    "estimate": 0
  },
  "error": null
}
```

```json
{
  "status": "ok",
  "result": {
    "change_id": "orch-appr-13-6",
    "marked": true,
    "restore_action": "history.rollback",
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
    "change_id": "orch-appr-13-7",
    "metadata": {
      "name": "montage.suggest_scene_variants",
      "risk": "high",
      "read_only": false,
      "destructive": false,
      "paid": true
    },
    "summary": "Один вызов LLM за вариантами сцены",
    "estimate": 0
  },
  "error": null
}
```

`prompt.create_master` в контракте деструктивный: сначала подтверждение.

```json
{
  "status": "approval_required",
  "result": {
    "change_id": "orch-appr-13-8",
    "metadata": {
      "name": "prompt.create_master",
      "risk": "medium",
      "read_only": false,
      "destructive": true,
      "paid": false
    },
    "summary": "Новая версия общего промта в БД, файл git не создавать",
    "estimate": 0
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
