# Слой действий: каталог волны 5 (W5-4)

Шаги плана D08, D09, D11, D12. Все исполнители вызывают существующие эндпоинты через `ctx.call`
(кроме двух действий `history.*`, они работают с журналом слоя). Заглушек нет. Проверено тестами
`tests/w5_d08_*`, `w5_d09_*`, `w5_d11_*`, `w5_d12_*` на временной БД и на заменителе эндпоинта запуска.
Живого вызова на настоящем сервере не было: уровень доказательства U/D, не L.

Всего действий: 102

Распределение по уровням: read 21, local_safe 35, destructive 45, paid 1

(Число сверяется тестом `tests/w5_d09_catalog_test.py` с реестром: при добавлении действия его нужно
внести сюда. Строка в `docs/ACTION_LAYER.md` «84 действия» относится к состоянию до волны 5.)

Из 102 действий 101 это прежние 84 и 17 новых волны 5, а ещё одно, `publish.send` (шаг D15, `publish_action.py`),
подключено интегратором в `spec.py`. Это единственное действие публикации: уровень destructive, необратимое,
каждый план с ним требует одноразового подтверждения человека на ЭТУ публикацию, управляемый (агентский) режим
его не запускает (предусловие `not_managed`), а при `SOCIAL_PUBLISH_ENABLED=false` (по умолчанию) шаг ничего не
отправляет. Автозапуска нет и не добавлять.

## Новые действия (17)

| Действие | Уровень | Деньги | Эндпоинт |
|---|---|---|---|
| `step.get_run_cost_basis` | read | нет | чтение проекта и доски монтажа |
| `step.run_step` | destructive | да | POST /projects/{id}/steps/{step}/run |
| `hitl.list_pending` | read | нет | GET /hitl/project/{id} |
| `hitl.approve` | destructive | нет | POST /hitl/{id}/decision |
| `hitl.reject` | destructive | нет | POST /hitl/{id}/decision |
| `hitl.regenerate` | destructive | да (потолок) | POST /hitl/{id}/decision |
| `project.create` | local_safe | нет | POST /projects |
| `project.create_child` | local_safe | нет | POST /projects/{id}/child |
| `project.patch_settings` | local_safe | нет | PATCH /projects/{id} |
| `massfactory.start` | destructive | да (потолок) | POST /projects/{id}/mass-lanes/start |
| `kill_switch.engage` | local_safe | нет | POST /projects/{id}/observations/kill-switch |
| `project.upload_xlsx` | destructive | нет | POST /projects/{id}/xlsx/upload |
| `hero.replace_image` | destructive | нет | POST /projects/{id}/assets/hero/replace |
| `storage.upload_file` | local_safe | нет | POST /projects/{id}/storage/{node}/upload |
| `storage.clear_files` | destructive | нет | DELETE /projects/{id}/storage/{node}/files |
| `history.list_snapshots` | read | нет | журнал слоя |
| `history.rollback` | destructive | нет | журнал слоя + эндпоинт правки |

## Изменённые действия прежних волн (11, то же id)

| Действие | Что изменено |
|---|---|
| `project.resume`, `project.continue_pipeline` | необязательный `ceiling_credits`: с ним план можно подтвердить, без него по-прежнему нет |
| `montage.set_voiceover`, `montage.set_coverage`, `shot.edit_cell_voiceover`, `shot.edit_field`, `prompt.save_master` | перед правкой сохраняется снимок прежнего значения, откат действием history.rollback |
| `montage.set_anchors`, `montage.set_scene_chain` | прежнее состояние ячейки записывается в журнал, автоматического отката нет (структурная правка) |
| `montage.upload_voice`, `montage.upload_music` | прежний файл копируется в `DATA_DIR/action_layer_snapshots`, откат загружает копию обратно |

## Деньги

- `step.run_step`: шаги `img_pr`, `img`, `anim_pr`, `video`, `audio`, `music`, `assemble`. Картинки и видео
  оцениваются по каталогу Kie (число генераций по доске, 3 и 4 платные попытки, клип допущен 5 с), оценка не сверена с
  балансом провайдера. Outsee, озвучка, музыка и LLM-шаги по коду не оцениваются: запуск только с
  `ceiling_credits`. Сборка локальная, оценка 0. Публикации в шагах нет.
- Потолок и заявленные значения входят в параметры плана, поэтому подтверждение привязано к хэшу плана вместе с ними.
  Потолок ниже расчёта по коду отклоняется. Потолок резервируется в бюджете слоя; воркер его не знает, жёсткий
  предел расхода дают лимиты `spend_guard` и аварийный стоп.
- С потолком автопродвижение проекта должно быть выключено: иначе после шага воркер пойдёт по цепочке сам, и потолок
  её не покроет.

## Идемпотентность и атрибуция (D12)

- Записывающий вызов с `Idempotency-Key` (ключ шага плана) выполняется один раз: повтор с тем же запросом отдаёт ответ
  первой попытки (таблица `action_layer_calls`). Тот же ключ с другим запросом считается другим вызовом.
- Попытка, начатая и не записавшая исход, блокирует слепой повтор (409), если действие не идемпотентно.
  Ответ 2xx с отказом внутри (`rejected`, `ok=false`) не кэшируется.
- Заголовок `X-Action-Layer-Operator` очищается до `[0-9A-Za-z_.:@-]`, до 120 знаков.
- Серверная часть (интеграция): изменяющие ручки роутера слоя действий (`/plans/preview`, `/plans/from-text`,
  `/plans/from-macro`, `/plans/{id}/approve`, `/run`, `/stop`) разбирают `Idempotency-Key` (1-80 знаков `A-Za-z0-9_.:-`):
  повтор с тем же ключом и тем же запросом отдаёт ответ первой попытки и не исполняется второй раз, начатая и не
  завершённая попытка даёт 409 `idempotency_unknown`, отказ сервера (HTTPException) снимает ключ для повтора. Остальные
  эндпоинты приложения ключ не разбирают. `X-Action-Layer-Operator` попадает в журнал вызовов, в `operator_audit.actor`
  (`operator:<имя>`, только вместе с верным токеном оператора, иначе заголовок игнорируется) и в события
  `media-recovery` (`release-unknown`, `resume-poll`), везде после очистки до безопасных символов.
- Не сделано: оператор в событиях нативных шагов проекта (они запускаются воркером, а не этим заголовком).

## Осознанные исключения (контрол Студии без действия)

| Контрол | Причина |
|---|---|
| Публикация на площадки | остаётся действием человека; агентский режим запрещён |
| Снятие аварийного стопа | включённый стоп блокирует любые шаги слоя; слой умеет только включать |
| Удаление проекта | необратимо; планировщик держит его вне каталога |
| Воркфлоу и холст | переписывание графа целиком, гонка с открытым холстом |
| GPT-workspace, meta-agent, gpt-verdict | платный LLM вне проекта, цену код не определяет |
| Baza (правка кадров /db/*) | частично покрыта `shot.*` и `montage.*` |
| Outsee/Kie create-workspace, gen-assistant | автономная генерация вне проекта, тарифов Outsee нет |
| Fleet pull/push/sync | запуск на основном ПК; есть только флаг `montage.set_send_to_main_pc` |
| Дублирование проекта | эндпоинта нет; есть дочерний проект |
