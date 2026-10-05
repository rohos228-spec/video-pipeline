# Extending the existing bounded runtime

Local developer contract, 2026-09-30. This is an integration checklist for the
existing code, not a new node language, executor or plugin runtime.

## Current accepted boundary

`excel_gpt` structured execution and `images` are accepted in the documented
synthetic scenarios. The original six-node text group, a separate image graph,
and genuine exhausted QC → one recovery → verification → image have worker
evidence. This does not accept every arbitrary Excel contract or media family.
Other registry entries do not acquire managed permission by existing in a list.

`videos`, `audio`, `assemble`, and `publish` remain `managed=False` in
`app/services/node_capabilities.py`. Legacy plan/script/split/scene-design,
characters/items and sidecar paths require their own executor acceptance.
Publication stays preparation/dry-run until separate authorization. Production
configuration and real media transport remain disabled.

## Declarative node data (onboarding without if-chains)

`app/orchestrator/node_spec.py` is built. A node's orchestrator knowledge is
data plus named callables, resolved by lookup:

| Item | Where | Notes |
|---|---|---|
| Capability | `CapabilitySpec` (or `NodeSpec` via `register_node`) | `result_kind`, executor text, phases, effects, actions, `managed`, `profile_compatible`, `native_json_retry`, `recovery_continuation`, `volatile_attachment_names`; `capability_for()` reads the registry |
| Acceptance | `CapabilitySpec.acceptance` | name of the accepted-evidence scenario; `managed` is forced false without it |
| Verifier | `verifier` name in the verifier registry | `VerifierSpec(observe='module:function' or callable, managed_only, success_errors, on_error, flags)`; run by `observe_attempt` |
| Contract | `contract` name in the contract registry | builder `async (ctx) -> dict`; default derived from `result_kind`; unknown name fails closed |
| Successor | `successor` name | data only today; the managed continuation check in `actions_v1` still names `images` |
| Package manifest | `capability_from_manifest(dict)` | validates `nodeapi: 1` + `orchestrator:` block; unknown keys/actions rejected |

Adding a node by data: register a `CapabilitySpec` (or `NodeSpec`), optionally
a verifier and a contract builder, and nothing in `observation.py`,
`decision_v1.py` or `node_capabilities.capability_for` changes. The read-only
`Decision` allow-list is `capability.actions` intersected with the execution's
allowed actions, so a new node is reflected without edits.
`tests/observe_v1/test_node_conformance.py` runs consistency rules over every
registered spec (managed needs acceptance; retry actions need
`profile_compatible`; media nodes never expose retry; verifier and contract
names must resolve) and a synthetic text node onboarded by data.
`tests/fixtures/node_capabilities_snapshot.json` is the pre-refactor
`asdict()` of every existing node and must stay equal.

Still outside this layer: the `ProjectStatus` pair, the step executor, the
`pipeline.py::advance()` branch, `input_revision`/`graph_version` composition,
and per-family recorders (`record_*_input/parse`) that executors call.

## Developer integration points

| Responsibility | Existing integration | Required evidence before managed=True |
|---|---|---|
| Runtime identity and FSM | `app/orchestrator/node_registry.py`, original pipeline/worker | Correct step/status mapping; original worker starts it once |
| Graph and prerequisites | `app/orchestrator/graph/planner.py`, `managed_launch.validate_scope` | Immutable graph revision, valid predecessors/gates, scope boundary |
| Capability and strategies | `app/services/node_capabilities.py` | Accepted input/result/effects, supported read/actions, no arbitrary code |
| Structured write contract | `app/services/node_write_contract.py`, `db_apply.py` | Allowed fields and UUID coverage; original application applies once |
| Provider lifecycle | `media_jobs.py`, `media_transport.py`, original provider client | Durable request/job identity, submit/poll/download accounting, uncertain stop |
| Result verification | `artifact_verifier.py`, `artifact_recovery.py`, `actions_recovery.py` | Actual file/probe or data proof; partial/unknown cannot become done |
| Observability/diagnosis | `observation.py`, `diagnosis.py`, `decision_v1.py` | Input/body hashes, attempts, transactions, typed facts and unresolved unknowns |
| UI and ownership | Existing observations router and panel | One scoped launch; understandable waiting/review/progress; other projects unchanged |

For a new family the developer must define actual input fields and sources,
output schema/artifacts, allowed database effects, domain checks, idempotent
application, compatible profiles, and continuation prerequisites. Implement
these in the existing family code and contracts. Do not add generic shell/SQL or
turn a guessed verifier into a successful status.

## Configuration variants and agent roles

Profiles are exact catalog entries, not free model instructions. Current fields:
`model_id`, `provider`, `kind`, `channel`, `reasoning_effort`,
`max_output_tokens`, `prompt_variant`, `synthetic_only`.
`decision_config.validate_profile` validates compatibility and token/reasoning
bounds; `profiles_v1.active_catalog` respects an explicit owner catalog.

Registered prompt variants live in `prompt_library.APPROVED_PROFILE_PROMPTS`:
original contract, formatting-only JSON repair, and required apply operations.
They preserve source roles/facts/identifiers and original schema/validators.
No variant can change write permissions, graph, budget or network. New role or
behavior mechanics require developer code/review and a specific test contract;
they cannot be injected by the Decision model. Artistic/semantic quality remains
unknown unless separately verified with an authorized, accounted evaluator.

The unified launch persists the selected profile IDs, exact candidate snapshot
and catalog hash before initial runtime dispatch. A catalog/config/input/graph
change blocks automatic reuse. Legacy executions expose only manually approved
immutable overlays, never an inferred catalog permission.

Decision selection creates the existing one-attempt `ProfileOverlay` only after
a claimed acting decision and validated persisted proposal. Overlay history
records the decision and proposal hash. The original recovery executor checks
input, graph, source body, attempt and budget again. `retry_same` and
`retry_profile` share one additional recovery, not separate allowances. A
restart or new incident cannot supply a second recovery.

## Owner policy, checkpoints and unknown outcomes

Default request ceiling remains 100. A local owner may explicitly configure
`budget_policy={policy_id,max_requests}`; an execution request cannot set this
policy or exceed its ceiling. `ManagedRunAllowance` binds one owner execution to
the project/workflow run; closing it does not reset consumption. Worker,
Decision and media operations charge the existing atomic common counter.

The synthetic scale test reserves 1000 operations, reloads after 500, and blocks
1001. It proves counter durability/atomicity, not a 1000-node factory run,
provider token limits, monetary savings or a hard dollar cap. Unknown prices and
usage are never zero. Changing policy does not refund requests already sent.

Persist action/attempt identity and immutable revision before execution. Verify
using matching runtime events and application data. An unknown submit outcome
means hold/review; a known media job is observed without another submit. Native
active retry is not duplicated. A complete model terminal or known HTTP
rejection is required to settle a request; a mere terminal/error row is not proof.

Configuration cancellation restores configuration permission only. It does not
roll back database data, files or paid work. Partial apply or audit/commit
uncertainty requires human review; any data rollback needs a specific reviewed
application transaction and checkpoint. The model cannot issue SQL to repair it.

## Minimum acceptance for each family

1. Existing API/FSM → original worker → parse/validate → apply → verify → next node.
2. Full bodies/files are local; shared IDs and hashes link logs to the attempt.
3. Failed payload/result remains failed/unknown; native retry and supervisor
   recovery are distinguished; no successful business status manufactured.
4. Active request, user stop, audit gap, stale revision, foreign scope and spent
   budget prevent a new request. Lost/repeated delivery and restart do not submit twice.
5. Business inputs are retained, IDs covered, and apply occurs once. Disk-file
   existence alone does not authorize managed success.
6. Ordinary mode and another project remain unchanged; legacy/sidecar entrances
   cannot bypass managed scope or the common counter.
7. Real files and actual tools are used for FFmpeg/ASR/subtitles/assembly. Missing
   executable/package/weights block that family; no fake output substitutes it.
8. Current loaded backend/frontend hashes, tests and UI evidence are saved.

All acceptance currently uses separate SQLite/DATA_DIR and network none, with
only external provider transport synthetic. Real provider verification and
production readiness remain separate acceptance levels.

## Ревизии: структура графа и конфигурация ноды (схема 2)

Раньше `graph_version` был хешем всего снимка графа (включая подписи, позиции и
настройки нод), а `input_revision` включал все `prompts/**/*.md`, весь объект
настроек и шесть файлов кода. Любая правка любого промпта, настройки `watchdog_*`
или подписи ноды аннулировала разрешения и билеты восстановления. Теперь это
разделено в модуле `app/services/revisions.py`.

### Что чем решается

| Значение | Что входит | Что не входит |
|---|---|---|
| `structure_hash(nodes, edges)` = `graph_version` | id и типы нод, рёбра (источник, цель, вид), `groupId`, `slotIndex`, `slotOverflow`, маркер scene-агента, флаг `disabled` | подписи, описания, позиции, id и ручки рёбер, любая конфигурация ноды |
| `node_config_revision(node, meta)` | `data` самой ноды без косметики и структуры, прочие поля ноды, `meta.excel_gpt_nodes[node]`, `meta.prompt_slot_variants[node]` | другие ноды, ключи `label`/`description`/`title`/`color` и подобные |
| `input_revision` для восстановления | данные, которые читает нода (поля проекта и таблицы как раньше); структура канваса и `disabled_nodes`; конфигурация ноды; содержимое выбранного промпт-файла этой ноды и scoped-переопределения (`prompt_overrides`, `gpt_text_overrides`, `node_step_params` только по её шагу); данные способности (`capability` и `CapabilitySpec`); настройки маршрута модели; ограниченный список модулей кода | остальные файлы `prompts/`, остальные настройки, посторонний код |

Разрешение (`ExecutionPermission`, `ActionExecutionControl.graph_version`) теперь
привязано к структуре. Билет восстановления привязан к структуре и к
`input_revision`, в котором уже учтена конфигурация именно этой ноды; её
ревизия отдельно записана в билете (`preconditions.node_config_revision`) и в
политике запуска (`policy.node_config_revisions`).

### Список кода, который аннулирует билет

Файлы берутся из данных способности, а не из общего списка: модули из строки
`executor` (например `make_plan`, `xlsx_step_runners`), модуль верификатора
(`verifier`), общие модули применения (`db_apply.py`, `node_write_contract.py`)
и, по типу результата, модули запроса к модели (`gpt_client.py`,
`gpt_text_builder.py`) либо медиа (`media_jobs.py`, `artifact_verifier.py`).
Нода без принятой способности (`managed=False`) в этом списке не участвует:
восстановление для неё запрещено самой способностью.

### Промпты

Учитывается файл, который реально выберет `resolve_project_prompt_with_source`
для шага этой ноды (для `excel_gpt` с `node_key` и слотом `main`, включая
шаблоны группы). Если проект собирает промпт из блоков (`use_blocks_v2` или
`blocks`), состав не перечислим, и входом считаются каталоги `blocks`, `steps`,
`styles`, `step-presets` целиком, но только в этом режиме. Для проверочной ноды
(`checkMode`/роль review, gate, compare) входом являются промпты исходных нод по
входящим стрелкам; при `checkPromptSource=agent` добавляются каталоги промптов и
шаблонов целиком и загруженный файл агента.

### Fail-closed

Любая часть, которую не удалось определить (нет ноды, не читается файл,
исключение при разборе промпта или настроек), заменяется структурой со случайным
`nonce`. Две подряд вычисленные ревизии тогда никогда не совпадают, билет не
может быть принят или подтверждён. Отсутствующий файл промпта это отдельное
устойчивое состояние (`missing`), а не ошибка.

### Версия схемы и старые записи

В записанные части ревизии добавлены `revision_scheme` (сейчас 2) и
`node_config_revision`. Хеш графа схемы 1 распознаётся через
`legacy_graph_version` и отклоняется с причиной.

Поведение для уже существующих записей:

- `ActionExecutionControl.graph_version` и `ExecutionControl.graph_version`
  схемы 1: запуск, восстановление и `charge_request` отклоняются с причиной
  `graph revision recorded under legacy scheme 1`; нужен новый запуск (старый
  execution не мигрируется молча, бюджет и разрешение не пересоздаются).
- Билет, у которого `preconditions.input_parts` без `revision_scheme`:
  `claim` отклоняет его до потребления (`recovery ticket rejected before claim:
  ... legacy scheme 1`), статус билета не меняется.
- `node_start` исходной попытки с `recovery_revision_parts` без
  `revision_scheme`: `recover_node` отклоняет восстановление с той же причиной,
  ничего не резервируется.
- Записи без частей (только значение): значение схемы 1 никогда не совпадёт с
  значением схемы 2 (схема входит в хеш), сообщение указывает на возможную
  старую схему.
- Профили (`ProfileOverlay.base_version`, `graph_version`) схемы 1 отклоняются
  штатными проверками `stale config base version` / `stale graph revision`.

Хеш плана слоя действий (`plan_hash` в `action_layer/plan.py`) не зависит от
этих ревизий и не изменился.

### Остаточные риски

- Список конфигурационных ключей ноды в `meta` задан явно
  (`PER_NODE_META_KEYS`). Новый per-node ключ, хранящийся вне `data` и этих
  двух корзин, в ревизию не попадёт, пока его не добавят в список.
- Косметические ключи заданы запретным списком (`COSMETIC_*`): новый ключ в
  `data` по умолчанию считается конфигурацией и аннулирует билет (безопасная
  сторона).
- Изменение файлов, загруженных пользователем как вход (`inputSource=upload`),
  по-прежнему ловится только сравнением тела первого запроса.
