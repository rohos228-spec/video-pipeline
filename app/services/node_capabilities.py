"""Capabilities of existing registry nodes, not a new executor or workflow."""
from dataclasses import asdict, dataclass


READ_ACTIONS = ("read_events", "read_contract", "wait", "human_review")


@dataclass(frozen=True)
class NodeCapability:
    node_type: str
    result_kind: str
    executor: str
    phases: tuple[str, ...]
    effects: tuple[str, ...]
    actions: tuple[str, ...]
    managed: bool = False
    approval: tuple[str, ...] = ("scope", "budget", "provider_profile")
    # Recovery-readiness data. Defaults are the conservative ones; a node opts in
    # only after its native executor/verifier path has been shown to be sound.
    # profile_compatible: an approved provider profile (effective_body overlay on the
    #   original single text Responses request) may be applied to this node's attempt.
    # native_json_retry: the native executor emits a `json_retry` fact before it fails
    #   (excel_gpt). Nodes without such an inner retry are proven exhausted by the
    #   runtime failure counter alone (step_failure total_fails/abandoned_at).
    # recovery_continuation: what the supervisor may do after a verified recovery.
    #   'excel_gpt_chain' = original excel_gpt chain continuation; 'none' = nothing,
    #   the original runtime (and any mandatory HITL) continues by itself.
    # volatile_attachment_names: the request text embeds wall-clock-stamped attachment file
    #   names, so the recovery's first-body identity check masks exactly those stamps.
    profile_compatible: bool = False
    volatile_attachment_names: bool = False
    native_json_retry: bool = True
    recovery_continuation: str = "excel_gpt_chain"

    def contract(self):
        return {**asdict(self), "semantic_quality_verified": False,
                "write_authority": "existing worker only",
                "arbitrary_shell_code_paths": False}


_JOB_PHASES = ("reserved", "submitted", "queued", "running", "completed",
               "downloading", "verifying", "verified", "failed", "cancelled", "unknown")
# Declarative capability data, one CapabilitySpec per node type (see
# app/orchestrator/node_spec.py). NodeCapability payloads (asdict/contract) are
# unchanged: acceptance/verifier/contract/successor live only in the spec.
# `acceptance` names the accepted-evidence scenario; managed is forced False
# without it. `verifier` / `contract` are NAMES in the registries populated
# below / in decision_v1.py.
_CAPABILITIES: dict[str, NodeCapability] = {}


def register_capability_spec(spec, *, replace: bool = False) -> None:
    """Add one node's capability data to the registry (and the derived
    NodeCapability view used by capability_for)."""
    from app.orchestrator import node_spec
    if spec.node_type in node_spec._CAPABILITY_SPECS and not replace:
        raise ValueError(f"capability already registered: {spec.node_type!r}")
    node_spec._CAPABILITY_SPECS[spec.node_type] = spec
    _CAPABILITIES[spec.node_type] = spec.to_capability()


def unregister_capability_spec(node_type: str) -> None:
    from app.orchestrator import node_spec
    node_spec._CAPABILITY_SPECS.pop(node_type, None)
    _CAPABILITIES.pop(node_type, None)


def _register_builtin_capabilities() -> None:
    from app.orchestrator.node_spec import CapabilitySpec as C
    text_phases = ('running', 'parse', 'validate', 'apply', 'commit', 'human_approval')
    for spec in (
        C('plan', 'project_text', 'make_plan.run → xlsx_step_runners.run_plan_xlsx',
            text_phases, ('project_database', 'local_xlsx', 'hitl_request'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True,
            profile_compatible=True, volatile_attachment_names=True, native_json_retry=False,
            recovery_continuation='none', acceptance='native_project_text',
            verifier='project_text_result', contract='project_text',
            verifies=('Общий план не пустой и проходит проверку содержательности (is_meaningful_general_plan)',
                      'Результат перечитывается из БД проекта после выполнения'),
            limits=('Один повтор на ноду за запуск, только после исчерпания штатных повторов',
                    'Обязательное утверждение человеком не снимается и не подменяется'),
            recovery_cap=1),
        C('script', 'project_text', 'make_script.run → xlsx_step_runners.run_script_xlsx',
            text_phases, ('project_database', 'local_xlsx', 'hitl_request'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True,
            profile_compatible=True, volatile_attachment_names=True, native_json_retry=False,
            recovery_continuation='none', acceptance='native_project_text',
            verifier='project_text_result', contract='project_text',
            verifies=('Текст закадра не короче 200 символов',
                      'Результат перечитывается из БД проекта после выполнения'),
            limits=('Один повтор на ноду за запуск, только после исчерпания штатных повторов',
                    'Обязательное утверждение человеком не снимается и не подменяется'),
            recovery_cap=1),
        C('split', 'frame_set', 'split_frames.run → xlsx_step_runners.run_split_xlsx',
            ('running', 'parse', 'apply', 'commit', 'verify'), ('project_database', 'local_xlsx'),
            READ_ACTIONS, True, acceptance='native_split',
            verifier='split_result', contract='frame_rows',
            verifies=('Кадров не меньше двух, у каждого свой uuid и подряд идущий номер',
                      'Закадровый текст сохранён целиком и по порядку',
                      'При провале проверки запуск удерживается до решения человека'),
            forbidden_code='destructive_rewrite',
            forbidden_reason='Нода целиком заменяет кадры проекта: повтор стёр бы прежний результат, '
                             'поэтому при провале проверки запуск удерживается, а решает человек.'),
        C('image_prompts', 'frame_prompts',
            'generate_image_prompts.run → xlsx_step_runners.run_img_pr_xlsx',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_xlsx', 'local_checkpoint'), READ_ACTIONS, True,
            acceptance='native_image_prompts', verifier='image_prompts_result',
            contract='frame_rows',
            verifies=('Набор кадров, номера и закадр не изменились',
                      'У каждого кадра-родителя основной промпт не короче порога',
                      'Причина удержания объясняется человеку по записанной проверке'),
            forbidden_code='native_skips_filled_rows',
            forbidden_reason='Нативный запуск не перезапрашивает уже непустой промпт, поэтому повтор '
                             'ничего не изменит; удержание после провала проверки снимает только человек.'),
        C('excel_gpt', 'structured', 'enrich_xlsx.run',
            ('running', 'parse', 'validate', 'apply', 'commit'), ('project_database',),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True, profile_compatible=True,
            acceptance='excel_gpt_group', contract='structured',
            verifies=('Операции применяются только через общий контракт записи (db_apply)',
                      'Покрытие кадров и проверки группы script_frames_qc'),
            limits=('Один повтор на ноду за запуск',
                    'Повтор запрещён после любого применённого пакета (частичное применение)'),
            recovery_cap=1),
        C('images', 'image', 'generate_images.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'), READ_ACTIONS, True,
            acceptance='synthetic_image_scenario', contract='media_jobs',
            successor='continue_managed_image',
            verifies=('Файл картинки существует и читается (проверка артефакта)',
                      'Журнал платных задач: неизвестный исход заново не отправляется'),
            forbidden_code='paid_uncertain_submit',
            forbidden_reason='Управляемый путь пока только синтетический; платная отправка с неизвестным '
                             'исходом могла быть списана, поэтому повторную отправку делает только человек.'),
        C('videos', 'video', 'generate_videos.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='media_resume_dry_run', recovery_only=True,
            verifier='media_result', contract='media_jobs',
            verifies=('Для каждого кадра с промтом анимации есть клип: файл существует, не пустой, читается '
                      '(ffprobe: видеодорожка, длительность), лежит внутри папки проекта',
                      'Журнал платных задач: у клипа Kie есть устойчивый task id; у Outsee id нет, это записывается',
                      'Прошлый submit без известного исхода (Kie или Outsee) выводится отдельным инцидентом',
                      'После возобновления файл клипа перечитывается независимо (ffprobe), число задач в журнале не выросло'),
            limits=('«Повтор» надзора у медиа = только возобновление по task id (опрос и скачивание) внутри режима '
                    '«только возобновление»: ни одного нового платного запроса',
                    'Один билет на ноду за запуск; решает оценка media_recovery.assess (resume_poll или resume_local)',
                    'Повторная платная отправка по провалу надзором не допущена: её делает нативный слой или человек',
                    'Outsee без идентификатора задачи и submit без известного исхода не возобновляются: решает человек',
                    'Надзор ноду не запускает и не перезапускает: после возобновления её продолжает человек обычным запуском',
                    'Нативная лестница до семи платных отправок на клип не меняется'),
            recovery_cap=1),
        C('items', 'image', 'generate_items.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='media_resume_dry_run', recovery_only=True,
            verifier='media_result', contract='media_jobs',
            verifies=('Для каждого описания предмета есть файл картинки: существует, декодируется, внутри проекта',
                      'Журнал платных задач: task id Kie или отсутствие id у Outsee',
                      'После возобновления файл перечитывается независимо, число задач в журнале не выросло'),
            limits=('«Повтор» надзора у предметов = только возобновление по task id: ни одного нового платного запроса',
                    'Один билет на ноду за запуск; решает оценка media_recovery.assess',
                    'Повторная платная отправка по провалу надзором не допущена',
                    'У Outsee нет task id: неизвестный исход блокирует повтор, решает человек',
                    'Надзор ноду не запускает и не перезапускает'),
            recovery_cap=1),
        C('music', 'audio', 'generate_music.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='media_resume_dry_run', recovery_only=True,
            verifier='media_result', contract='media_jobs',
            verifies=('Есть файл музыки: существует, не пустой, читается (ffprobe: звуковая дорожка)',
                      'Журнал платных задач: task id Suno или его отсутствие',
                      'После возобновления файл перечитывается независимо (ffprobe), число задач в журнале не выросло'),
            limits=('«Повтор» надзора у музыки = только возобновление задачи Suno по task id: ни одного нового платного запроса',
                    'Один билет на ноду за запуск; решает оценка media_recovery.assess',
                    'Повторная платная отправка по провалу надзором не допущена',
                    'Если Kie не настроен, генерация идёт без идентификатора задачи: возобновить нельзя',
                    'Надзор ноду не запускает и не перезапускает'),
            recovery_cap=1),
        C('audio', 'audio', 'generate_audio.run → elevenlabs_api',
            ('reserved', 'submitted', 'verifying', 'verified', 'alignment', 'failed', 'unknown'),
            ('provider_generation', 'local_file', 'frame_timing', 'artifact_row'), READ_ACTIONS, False,
            verifier='media_result', contract='media_jobs',
            verifies=('Файл озвучки существует, читается (ffprobe: звуковая дорожка, длительность)',
                      'Число кусков озвучки сверяется с числом кадров с закадровым текстом',
                      'Маркер .tts_uncertain_* (таймаут или обрыв после отправки) выводится как неизвестный исход'),
            limits=('Повторный POST после таймаута запрещён, пока человек не снимет маркер или не положит файл',
                    'Готовый файл озвучки на диске переиспользуется без POST',
                    'Вердикт resume_local (готовый файл) надзором не исполняется: результатом ноды файл делает только '
                    'её собственный запуск, а повтор рабочим процессом не допущен (нет проверки для такого повтора)',
                    'Один платный POST на сегмент без ключа идемпотентности: исход таймаута не доказать'),
            forbidden_code='paid_no_idempotency',
            forbidden_reason='Запрос озвучки без ключа идемпотентности: после таймаута неизвестно, '
                             'списаны ли деньги, поэтому повтор делает только человек.'),
        C('assemble', 'assembly', 'assemble.run → assembly.assemble',
            ('inputs', 'processing', 'subtitles', 'verifying', 'verified', 'failed', 'unknown'),
            ('local_file', 'artifact_row'), READ_ACTIONS, False, verifier='media_result',
            contract='media_jobs',
            verifies=('Итоговый файл собран во временный файл и подменяет прежний только после verify_artifact '
                      '(видео и звук, длительность)',
                      'Заново читаются файл, его sha256 и запрос approve_final для этого файла',
                      'Сбой сборки или проверки: ровно одна повторная попытка внутри ноды'),
            limits=('Подтверждение approve_final нельзя снять повтором: запрос один на пару проект и sha256',
                    'Автоодобрение финала требует живой успешной проверки; ручное одобрение не затронуто',
                    'Платный запрос недостижим (сухой прогон test_media_dry_run_local_nodes.py: охрана и разбор импортов)',
                    'Повтор надзором не допущен: одна попытка внутри ноды уже есть, а своей проверки результата для '
                    'повтора рабочим процессом у не-модельной ноды нет (см. причину запрета)'),
            forbidden_code='media_recovery_not_admitted',
            forbidden_reason='Атомарная запись, проверка итога, один повтор внутри ноды и недостижимость платного '
                             'запроса доказаны сухим прогоном, но повтор сборки надзором не допущен: повтор идёт '
                             'только рабочим процессом, а его проверка в надзоре построена на фактах модельного '
                             'запроса (применённые данные и разобранный ответ), которых у сборки нет; сбой при '
                             'наличии прежнего итога не помечается как медиа-инцидент, а основа ревизии входа для '
                             'сборки не записывается. Запуск, повтор и подтверждение финала остаются за человеком.'),
        C('sfx_plan', 'structured',
            'plan_sfx.run → sfx_plan.plan_sfx_events → ai_result_io.text_job',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_file', 'local_checkpoint'),
            READ_ACTIONS + ('retry_same', 'finish'), True, native_json_retry=False,
            recovery_continuation='none', acceptance='native_sfx_plan',
            verifier='sfx_plan_result', contract='sfx_plan',
            verifies=('План читается заново из чекпоинта и файла sfx_plan.json независимой сессией',
                      'Виды звуков из каталога, границы таймлайна, длительность, громкость, плотность '
                      'и зазор между событиями (validate_events)',
                      'В управляемом запуске план без подтверждения удерживает запуск'),
            limits=('Один повтор на ноду за запуск',
                    'Только если ответ модели не разобран (invalid_json / missing_payload) и ничего не применено',
                    'Нарушение правил таймлайна у разобранного плана повтором не лечится: решает человек',
                    'Не запускает sfx_gen (платный ElevenLabs) и не меняет кадры',
                    'Выключенный SFX (проход без плана) в управляемом запуске не считается проверенным'),
            recovery_cap=1),
        C('sfx_gen', 'audio',
            'generate_sfx.run → sfx_gen.generate_sfx_files → elevenlabs_api/local_synth',
            ('running', 'generating', 'writing', 'verifying', 'verified', 'failed', 'unknown'),
            ('local_file', 'project_database'), READ_ACTIONS, False,
            verifier='sfx_result', contract='media_jobs',
            verifies=('Каждый звуковой файл существует, читается как WAV и не пустой по уровню',
                      'Число файлов совпадает с планом, пути лежат внутри папки sfx проекта',
                      'Откат ElevenLabs на локальный синтез виден: факт provider_fallback, инцидент '
                      'provider_degraded и поле provider_fallback у события'),
            limits=('Повтор по событию не больше одного и только после ответа провайдера (списания не было)',
                    'Откат после таймаута или обрыва (мог быть списан платёж) не повторяется никогда',
                    'Готовые файлы событий переиспользует сама нода по чекпоинту; вердикта resume_local у sfx_gen нет, '
                    'надзор повтор не делает',
                    'Платный вызов на событие остаётся без ключа идемпотентности'),
            forbidden_code='paid_no_idempotency',
            forbidden_reason='Платный вызов на каждое событие без ключа идемпотентности: исход таймаута '
                             'не доказать, поэтому повтор по событию допускает только нативный слой '
                             'и только после ответа провайдера.'),
        C('publish', 'publication', 'publish.run',
            ('input_validation', 'dry_run', 'blocked'), ('external_accounts',),
            ('read_events', 'read_contract', 'human_review'), False, contract='media_jobs',
            forbidden_code='external_irreversible',
            forbidden_reason='Публикация во внешних аккаунтах необратима, повтор мог бы опубликовать дважды; '
                             'управляемая публикация отключена, решает только человек.'),
        C('animation_prompts', 'frame_prompts',
            'make_animation_prompts.run → animation_prompt_gpt.parse_animation_reply',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'), ('project_database',),
            READ_ACTIONS + ('retry_same', 'finish'), True, volatile_attachment_names=True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='native_animation_prompts', verifier='animation_result',
            contract='animation_prompts',
            verifies=('Первичные и вторые (shot02) промпты заполнены у кадров, бывших целью на входе',
                      'Закадр и image_prompt не изменились, посторонние поля кадров не тронуты',
                      'Исходная проверка harness anim_pr пройдена до статуса готовности'),
            limits=('Один повтор на ноду за запуск',
                    'Только пока ни одна пачка не применена: после первого применения повтор запрещён',
                    'Смена профиля запрещена: во входе картинки, профиль не может пересобрать запрос',
                    'Локальный запасной путь и пропуск shot_02 при сбое провайдера видны фактом animation_degraded '
                    'и инцидентом animation_degraded; сам по себе он повтор не открывает',
                    'Не запускает videos (платная нода с утверждением человеком)'),
            recovery_cap=1),
        *_coverage_only_specs(),
    ):
        register_capability_spec(spec)


def _watch(node_type, code, reason, *, verifies=(), hook=None):
    """Coverage-only entry: the node type is registered and visible, but the supervisor offers
    nothing beyond reading. Same payload as an unregistered type (result_kind 'unsupported',
    read-only actions), so behaviour is unchanged; the entry only carries WHY."""
    from app.orchestrator.node_spec import CapabilitySpec as C
    return C(node_type, 'unsupported', '', ('unknown',), (),
             ('read_events', 'read_contract', 'human_review'),
             verifies=tuple(verifies), observation_hook=hook,
             forbidden_code=code, forbidden_reason=reason)


def _coverage_only_specs():
    scene_hook = 'app.services.scene_contracts:observe_scene_result'
    legacy = ('Устаревший тип enrich_N: управляемый путь и повтор есть только у excel_gpt; '
              'граф нужно перенести на excel_gpt (миграция C5).')
    return (
        _watch('scene_design', 'legacy_node_type',
               'Устаревший тип одной ноды на всю фазу не отличить от агента, который в ней идёт: результат '
               'проверяется как объединение агентов и сборки, а повтор небезопасен и остаётся за человеком.',
               verifies=('Для каждого агента веера, который отработал: срез по контракту (список, id, поля) '
                         'и сохранённые ячейки совпадают с тем, что выводит нативный конвертер',
                         'Для сборки: итоговый payload по нативной проверке и сверка с Frame.attrs',
                         'Сбой ответа типизируется как invalid_json, missing_payload или contract_rejected'),
               hook=scene_hook),
        _watch('sd_agent', 'partial_commit',
               'Веер агентов идёт одним шагом: чекпоинты лежат на диске, ячейки коммитятся частями, а сам шаг '
               'не пишет транзакционного факта применения, поэтому повтор нечем проверить и он небезопасен.',
               verifies=('Агенты skeleton, characters, world, camera, action: срез по контракту агента '
                         '(обязательный список, id по шаблону, поля) и сохранённые ячейки равны тому, что '
                         'выводит нативный конвертер; кадры и ячейки других агентов не тронуты',
                         'Сбой ответа типизируется: invalid_json (нет JSON), missing_payload (нет списка), '
                         'contract_rejected (срез или ячейки нарушают контракт)'),
               hook=scene_hook),
        _watch('sd_assemble', 'pre_request_commit',
               'До запроса к модели нода уже записала дочерние кадры, поэтому вход повтора изменён '
               '(в режиме chrono_dyn запроса к модели нет вовсе): повтор небезопасен.',
               verifies=('Разбор и проверка итогового payload, сверка его полей с реальными Frame.attrs',
                         'Сбой типизируется: invalid_json (нет JSON), missing_payload (нет scenes или ops), '
                         'contract_rejected (правила сборки отклонили)'),
               hook=scene_hook),
        _watch('hero', 'nondeterministic_paid_request',
               'Промт героя v1 каждый раз заново строит GPT, поэтому после рестарта тот же платный запрос '
               'не воспроизвести и открытую задачу по task id не найти: повтор мог бы заплатить заново.',
               verifies=('У героя есть файл картинки: существует, не пустой, декодируется, лежит в папке проекта',
                         'Журнал платных задач: task id Kie или отсутствие id у Outsee')),
        *(_watch(f'enrich_{n}', 'legacy_node_type', legacy,
                 verifies=('Журнал ведётся как у excel_gpt (observe_attempt подставляет excel_gpt)',),
                 hook='app.services.observation:observe_attempt')
          for n in range(1, 6)),
    )


# Recovery decisions for media/paid-or-external node types. Pure declarative data kept OUTSIDE
# NodeCapability on purpose: contract() is built with asdict() and is persisted/sent to the
# decision layer, so a new dataclass field would change those payloads. Nothing reads this
# table at runtime; docs/MEDIA_NODES_READINESS.md and tests/test_media_nodes_recovery_decision.py
# are its only consumers. Every entry is human-only (no supervisor retry) except the resume-only ones
# (videos, items, music): the supervisor may only resume a known provider task, never submit a new one.
# Facts are code-grounded (file:line in the doc); "reason" is the honest short justification.
_MEDIA_HUMAN_ONLY = "human_only"
_MEDIA_RESUME_ONLY = "resume_only"
MEDIA_RECOVERY_DECISIONS = {
    "hero": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_image",
        "provider": "outsee/kie via generate_image_with_retries",
        "reason": "No managed lifecycle; the prompt of hero v1 is rebuilt by GPT on every run, so after a restart "
                  "the same paid request cannot be reproduced and an open Kie task is not found: a supervisor "
                  "retry could double-pay (observation: media_result verifier, ledger trail)."},
    "items": {"decision": _MEDIA_RESUME_ONLY, "cost_class": "paid_per_image",
        "provider": "outsee/kie via generate_image_with_retries",
        "reason": "Supervisor admission is a resume of a known Kie task id only (poll and download inside the "
                  "resume-only scope, zero new paid requests); Outsee has no task id (unknown outcome, never "
                  "resubmitted) and a paid re-submit stays with the native layer or a human: a retry could double-pay."},
    "images": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_image",
        "provider": "kie (managed, synthetic loopback only) / outsee (native)",
        "reason": "Managed route is synthetic-loopback only (no authorized production network profile); "
                  "a failed or unknown job is terminal and never resubmitted; uncertain submit may "
                  "already be charged."},
    "videos": {"decision": _MEDIA_RESUME_ONLY, "cost_class": "paid_per_clip_highest",
        "provider": "outsee/kie (Seedance, Kling, Veo ladder)",
        "reason": "Supervisor admission is a resume of a known Kie task id only (poll and download inside the "
                  "resume-only scope, zero new paid requests); Outsee has no id and the native ladder is up to "
                  "4 primary + 3 fallback paid submits per clip, so a paid re-submit by the supervisor is not "
                  "admitted: a blind retry can double-pay."},
    "audio": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_character",
        "provider": "elevenlabs_api (direct HTTP)",
        "reason": "managed=False; request has no idempotency key and the native client already retries "
                  "3 times on timeout/429/5xx, so a timeout after acceptance may be charged twice."},
    "music": {"decision": _MEDIA_RESUME_ONLY, "cost_class": "paid_per_generation",
        "provider": "kie suno (direct) or browser outsee",
        "reason": "Supervisor admission is a resume of a known Suno task id only (poll and download inside the "
                  "resume-only scope, saved prompt, zero new paid requests); a paid re-submit is not admitted "
                  "because a retry could re-submit and re-pay."},
    "sfx_gen": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_event_small_or_free_local",
        "provider": "elevenlabs sound-effects (or local_synth when no key)",
        "reason": "managed=False; per-event paid call without idempotency key; on error it falls back to local "
                  "synthesis (now recorded as provider_fallback), a timeout outcome stays unknown and may be charged."},
    "assemble": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "no_provider_spend_local_cpu",
        "provider": "local ffmpeg (+ local ASR timing)",
        "reason": "No provider spend (proven by a dry run: guard and import scan); atomic write, verify_artifact and one "
                  "in-node retry exist, but supervisor recovery is not admitted: a retry must run the worker, and the "
                  "supervisor has no recovery verifier for a node without model facts (unknown result evidence), a "
                  "failure with an earlier final is not typed as a media incident and the input baseline is not recorded."},
    "publish": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "external_side_effect_not_money",
        "provider": "social publishers (external accounts)",
        "reason": "Posting to external accounts is irreversible; managed publication is hard-disabled "
                  "and a retry could publish twice."},
}


def media_recovery_decision(node_type: str):
    """Declarative human-only decision for a media node, or None for any other type."""
    return MEDIA_RECOVERY_DECISIONS.get(node_type)


def capability_for(node_type: str) -> NodeCapability:
    from app.orchestrator.node_registry import is_work_node_type
    from app.orchestrator.node_spec import capability_spec_for
    spec = capability_spec_for(node_type)
    if spec is not None and is_work_node_type(node_type):
        return _CAPABILITIES[node_type]
    return NodeCapability(node_type, "unsupported", "", ("unknown",), (),
                          ("read_events", "read_contract", "human_review"))


def require_managed_capability(node_type: str):
    capability = capability_for(node_type)
    if not capability.managed:
        raise ValueError("node adapter has no accepted managed executor")
    from app.orchestrator.node_spec import capability_spec_for
    spec = capability_spec_for(node_type)
    if spec is not None and spec.recovery_only:
        raise ValueError("node is managed for supervised recovery only: the supervisor never starts or restarts it")
    return capability


def image_prompts_contract():
    """Original img_pr parent/child and write rules, without recovery authority."""
    from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
    return {'node_type':'image_prompts','input':'original scoped Frame rows and resolved native prompt',
        'output':'same scoped rows; primary image_prompt and native second-shot attrs',
        'min_characters':IMG_PR_MIN_WRITE_CHARS,'primary_prompt_required':True,
        'threshold':'either native prompt slot; short primary allowed with complete second slot',
        'target_predicate':'vo_shot_expand.is_img_pr_vo_parent; children exempt from parent threshold',
        'allowed_fields':['image_prompt','image_prompt_shot2','characters'],
        'write_contract':'node_write_contract.filter_ops_for_node(node_kind=img_pr)',
        'native_partial_extraction_preserved':True,'semantic_quality_verified':False,
        'arbitrary_shell_code_paths':False}


def image_prompt_input_snapshot(frames,*,project_id):
    from app.services.vo_shot_expand import is_img_pr_vo_parent
    return {'project_id':project_id,'frames':[
        {'project_id':f.project_id,'uuid':f.uuid,'number':f.number,
         'voiceover_text':f.voiceover_text,'required_prompt':is_img_pr_vo_parent(f)}
        for f in frames]}


def verify_image_prompts(frames,*,project_id,snapshot):
    """Read-only native output integrity; no semantic/model quality claim."""
    from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR
    from app.services.observation import digest
    rows=[{'project_id':f.project_id,'uuid':f.uuid,'number':f.number,
        'voiceover_text':f.voiceover_text,'image_prompt':f.image_prompt,
        'second_prompt':(f.attrs or {}).get(SHOT2_PROMPT_ATTR)} for f in frames]
    proof={'node_type':'image_prompts','frame_count':len(rows),'frame_set_sha256':digest(rows),
        'input_sha256':digest(snapshot) if snapshot is not None else None,
        'business_commit_claim':False,'semantic_quality_verified':False,
        'source':'original executor Frame rows; committed outbox proves application'}
    if (not isinstance(snapshot,dict) or not isinstance(snapshot.get('frames'),list)
            or not snapshot['frames'] or any(not isinstance(r,dict) or
                not {'uuid','number','voiceover_text','required_prompt'}<=r.keys() for r in snapshot['frames'])):
        return {**proof,'status':'unknown','reason':'captured input unavailable or incomplete','errors':[],
            'failures':[],'failures_total':0,'voiceover_preserved':False}
    errors=[];failures=[];before=snapshot['frames']
    if snapshot.get('project_id')!=project_id or any(r.get('project_id')!=project_id for r in before):
        errors.append('input_project_outside_scope')
    before_ids=[r['uuid'] for r in before];after_ids=[r['uuid'] for r in rows]
    if any(not uid for uid in before_ids+after_ids):errors.append('missing_uuid')
    if len(set(before_ids))!=len(before_ids) or len(set(after_ids))!=len(after_ids):errors.append('duplicate_uuid')
    if set(before_ids)!=set(after_ids) or len(before)!=len(rows):errors.append('frame_set_changed')
    if any(r['project_id']!=project_id for r in rows):errors.append('output_project_outside_scope')
    if len({r['number'] for r in rows})!=len(rows):errors.append('duplicate_number')
    by_id={r['uuid']:r for r in rows};voiceover_preserved=True;targets=0
    for row in before:
        current=by_id.get(row['uuid'])
        if current is None:
            voiceover_preserved=False
            continue
        if current['voiceover_text']!=row['voiceover_text']:
            voiceover_preserved=False;errors.append('voiceover_changed:'+str(row['uuid']))
            failures.append({'criterion':'voiceover_changed','frame_uuid':row['uuid'],'number':row['number']})
        if current['number']!=row['number']:
            errors.append('frame_number_changed:'+str(row['uuid']))
            failures.append({'criterion':'frame_number_changed','frame_uuid':row['uuid'],'number':row['number']})
        if row['required_prompt'] is True:
            targets+=1
            primary=str(current['image_prompt'] or '').strip()
            second=str(current['second_prompt'] or '').strip()
            if not primary or max(len(primary),len(second))<IMG_PR_MIN_WRITE_CHARS:
                errors.append('missing_or_short_parent_prompt:'+str(row['uuid']))
                failures.append({'criterion':'missing_or_short_parent_prompt','frame_uuid':row['uuid'],
                    'number':row['number'],'primary_len':len(primary),'second_len':len(second),
                    'required_min':IMG_PR_MIN_WRITE_CHARS})
        elif row['required_prompt'] is not False:errors.append('input_target_rule_unknown')
    if not targets:errors.append('no_required_parent_frames')
    return {**proof,'status':'failed' if errors else 'passed',
        'reason':errors[0] if errors else None,'errors':errors,
        'failures':failures[:IMAGE_PROMPT_FAILURES_LIMIT],'failures_total':len(failures),
        'voiceover_preserved':voiceover_preserved,'target_frame_count':targets}


IMAGE_PROMPT_FAILURES_LIMIT=20
_IMAGE_PROMPT_CRITERIA={
    'input_project_outside_scope':'вход относится к другому проекту',
    'output_project_outside_scope':'кадры результата относятся к другому проекту',
    'missing_uuid':'у кадра нет uuid',
    'duplicate_uuid':'повторяющийся uuid кадра',
    'frame_set_changed':'набор кадров изменился с момента старта ноды',
    'duplicate_number':'повторяющийся номер кадра',
    'no_required_parent_frames':'в проекте нет ни одного кадра, которому нужен основной промпт',
    'input_target_rule_unknown':'неизвестно, нужен ли кадру промпт (вход неполон)',
}


def explain_image_prompt_verification(proof):
    """Human-readable reason a native verifier put the run on hold. Pure; reads only
    the recorded proof. Releasing the hold stays a human action."""
    from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
    if not isinstance(proof,dict):return None
    status=proof.get('status')
    if status=='passed':return None
    errors=[str(e) for e in (proof.get('errors') or [])]
    failures=[f for f in (proof.get('failures') or []) if isinstance(f,dict)]
    lines=[];criteria=[]
    if status=='unknown':
        criteria.append('input_or_output_unavailable')
        why=proof.get('reason') or 'captured input unavailable'
        lines.append('Проверка не смогла сравнить результат со входом ('+str(why)+'); '
            'по правилу безопасности запуск удержан, пока человек не посмотрит данные.')
    short=[f for f in failures if f.get('criterion')=='missing_or_short_parent_prompt']
    if short:
        criteria.append('missing_or_short_parent_prompt')
        nums=', '.join(str(f.get('number')) for f in short[:10])
        best=max(max(int(f.get('primary_len') or 0),int(f.get('second_len') or 0)) for f in short)
        lines.append(f'У {proof.get("failures_total") or len(short)} кадров(а) промпт пустой или короче '
            f'{IMG_PR_MIN_WRITE_CHARS} символов (кадры: {nums}; максимум среди слотов: {best}). '
            'Нода не перезапрашивает уже непустой промпт, поэтому повтор не поможет: нужна правка человека или запуск с очисткой.')
    for key in ('voiceover_changed','frame_number_changed'):
        hit=[f for f in failures if f.get('criterion')==key]
        if hit:
            criteria.append(key)
            nums=', '.join(str(f.get('number')) for f in hit[:10])
            lines.append(('Закадр изменился' if key=='voiceover_changed' else 'Номер кадра изменился')+f' у кадров: {nums}.')
    for err in errors:
        code=err.split(':',1)[0]
        if code in _IMAGE_PROMPT_CRITERIA and code not in criteria:
            text=_IMAGE_PROMPT_CRITERIA[code]
            criteria.append(code);lines.append(text[0].upper()+text[1:]+'.')
    if not lines:lines.append('Нативная проверка не подтвердила результат: '+str(proof.get('reason') or status)+'.')
    summary=' '.join(lines)+' Снять удержание может только человек.'
    return {'summary':summary,'criteria':criteria,'failing_frames':[
        {k:f.get(k) for k in ('criterion','frame_uuid','number','primary_len','second_len','required_min')}
        for f in failures[:IMAGE_PROMPT_FAILURES_LIMIT]],
        'failures_total':proof.get('failures_total',len(failures)),
        'hold_release':'human_only','automatic_action':False,'semantic_quality_verified':False}


async def recompute_image_prompt_verification(bind,*,project_id,snapshot):
    """Read-only recomputation of the native verifier from the committed rows
    (independent session, no flush, no write, no hold change)."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models import Frame
    async with async_sessionmaker(bind,expire_on_commit=False,autoflush=False)() as reader:
        frames=(await reader.scalars(select(Frame).where(Frame.project_id==project_id)
            .order_by(Frame.number))).all()
    proof=verify_image_prompts(frames,project_id=project_id,snapshot=snapshot)
    return {'proof':proof,'explanation':explain_image_prompt_verification(proof)}


async def record_image_prompt_input(frames,project):
    from app.services.observation import _attempt,record_fact,digest
    ctx=_attempt.get()
    if not ctx:return
    snapshot=image_prompt_input_snapshot(frames,project_id=project.id)
    ctx.image_prompt_input=snapshot
    await record_fact('node_input',{'contract':'native_image_prompts','frame_count':len(frames),
        'input_sha256':digest(snapshot)},body=snapshot)


async def record_image_prompt_parse(reply,ops,*,batch_i,target_uuids):
    import json
    from app.services.img_pr_batches import writeable_img_pr_ops,uuid_of_op
    from app.services.observation import _attempt,record_fact,digest,completed_model_request_id
    ctx=_attempt.get()
    if not ctx:return
    try:json.loads(reply);syntax=True
    except (ValueError,TypeError):syntax=False
    writable=writeable_img_pr_ops(ops);ids=[uuid_of_op(op) for op in writable]
    scoped=bool(ids) and len(set(ids))==len(ids) and set(ids)<=set(target_uuids)
    request_id=completed_model_request_id()
    requests=getattr(ctx,'image_prompt_batch_requests',None)
    if requests is None:requests={};ctx.image_prompt_batch_requests=requests
    requests[batch_i]=request_id
    if request_id is None and ctx.scope.mode=='managed':
        ctx.observer.mark_gap(ctx.scope.execution_id,RuntimeError('native image prompt model request identity unavailable'))
    await record_fact('parse_result',{'contract':'native_image_prompts','batch':batch_i,
        'syntax_valid':syntax,'parsed':bool(ops),'schema_valid':scoped,'ops_count':len(ops),
        'batch_coverage_complete':set(ids)==set(target_uuids),
        'writeable_ops_count':len(writable),'payload_required':False,
        'parser':'original img_pr_batches.parse_img_pr_ops; partial extraction preserved',
        'sha256':digest(ops)},body={'text':reply,'extracted_ops':ops},request_id=request_id)
    await record_fact('runtime_check',{'check':'native_image_prompt_batch','batch':batch_i,
        'status':'passed' if scoped else 'failed','performed':True,
        'business_commit_claim':False},request_id=request_id)


async def observe_image_prompt_result(session,project,node_type):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models import Frame
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type!='image_prompts':return
    project_id=ctx.scope.project_id
    read_error=None
    try:
        # Bind to the existing business DB without initialization, migration,
        # autoflush, commit, or repair of a failed native session.
        async with async_sessionmaker(session.bind,expire_on_commit=False,autoflush=False)() as reader:
            frames=(await reader.scalars(select(Frame).where(Frame.project_id==project_id)
                .order_by(Frame.number))).all()
        proof=verify_image_prompts(frames,project_id=project_id,snapshot=getattr(ctx,'image_prompt_input',None))
    except Exception as exc:
        read_error=exc;frames=None
        proof={'node_type':'image_prompts','status':'unknown','reason':'committed output unavailable',
            'error_type':type(exc).__name__,'frame_count':None,'frame_set_sha256':None,
            'semantic_quality_verified':False,'business_commit_claim':False}
    output={'project_id':project_id,'frames':None if frames is None else [
        {'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text,
            'image_prompt':f.image_prompt,'attrs':f.attrs} for f in frames]}
    await record_fact('node_output',{'frame_count':None if frames is None else len(frames),
        'frame_set_sha256':proof['frame_set_sha256'],'source':'committed original executor rows; independent read session'},body=output)
    await record_fact('node_result_verification',proof,body=proof)
    ctx.image_prompt_verification_recorded=True
    if read_error:ctx.observer.mark_gap(ctx.scope.execution_id,read_error)
    if proof['status']!='passed':
        from app.services.actions_v1 import ActionSupervisor
        try:
            await ActionSupervisor(ctx.observer.master).set_held(project_id=project_id,execution_id=ctx.scope.execution_id,
                held=True,initiator='native_image_prompt_verifier',rationale=proof['reason'],
                idempotency_key='image-prompts-verify:'+ctx.attempt_id,operator_request=False)
        except Exception as exc:
            ctx.observer.mark_gap(ctx.scope.execution_id,exc)
            raise
        ctx.image_prompt_hold_saved=True
        raise RuntimeError('managed image prompt verification '+proof['status'])


def project_text_contract(node_type):
    """Exact native contracts; no generic write/retry permission for text nodes."""
    from app.services.plan_validation import MIN_GENERAL_PLAN_CHARS
    if node_type not in {'plan','script'}:raise ValueError('unsupported project text contract')
    return {'node_type':node_type,'input_field':'topic' if node_type=='plan' else 'general_plan',
        'output_field':'general_plan' if node_type=='plan' else 'script_text',
        'apply_ops_field':'общий_план' if node_type=='plan' else 'закадровый_текст',
        'min_characters':MIN_GENERAL_PLAN_CHARS if node_type=='plan' else 200,
        'formats':['apply_ops_json'] if node_type=='plan' else ['apply_ops_json','VOICEOVER_block','plain_text_native_fallback'],
        'validator':'plan_validation.is_meaningful_general_plan' if node_type=='plan' else 'make_script.run minimum voiceover length',
        'human_approval_preserved':True,'semantic_quality_verified':False,
        'retry_policy':('original native runtime first; after it is exhausted with invalid_json/missing_payload '
            'ONE bounded supervisor retry_same or approved-profile retry of the same executor. '
            'Never approves the mandatory HITL; success is verified at the HITL boundary.')}


def project_text_parse_evidence(reply, text, node_type):
    """Report actual extraction and native validation, without requiring JSON for VO."""
    import json
    from types import SimpleNamespace
    from app.services import db_apply

    contract = project_text_contract(node_type)
    try:
        json.loads(reply)
        syntax_valid = True
    except (ValueError, TypeError):
        syntax_valid = False
    parsed = db_apply.extract_apply_ops_json(reply or '')
    proof = verify_project_text(SimpleNamespace(**{contract['output_field']: text}), node_type)
    return {
        'contract': 'native_' + node_type,
        'node_type': node_type,
        'syntax_valid': syntax_valid,
        'parsed': isinstance(parsed, dict) or (node_type == 'script' and bool(text)),
        'payload_present': bool(text),
        # plan always needs the JSON field. script accepts JSON, a VOICEOVER block or plain
        # text, so a payload is "required" in the diagnostic sense only when NOTHING usable
        # was extracted (e.g. malformed JSON and no voiceover): that is the typed
        # invalid_json/missing_payload failure; a valid non-JSON voiceover never is.
        'payload_required': node_type == 'plan' or (node_type == 'script' and not text),
        'nonempty_payload_required': True,
        'schema_valid': proof['status'] == 'passed',
        'schema_note': 'Original field extraction and native validation; no semantic quality claim',
        'output_field': contract['output_field'],
        'characters': proof['characters'],
        'sha256': proof['sha256'],
        'semantic_quality_verified': False,
    }


async def record_project_text_input(project, node_type):
    from app.services.observation import _attempt, record_fact
    if _attempt.get() is None:
        return
    contract = project_text_contract(node_type)
    await record_fact('node_input', {'contract': 'native_' + node_type,
        'source': 'authoritative project.db fields'},
        body={contract['input_field']: getattr(project, contract['input_field'], None)})


async def record_project_text_parse(reply, text, node_type):
    from app.services.observation import _attempt, record_fact
    ctx = _attempt.get()
    if ctx is None:
        return
    proof = project_text_parse_evidence(reply, text, node_type)
    await record_fact('parse_result', proof, request_id=ctx.last_request_id,
        body={'reply': reply, 'extracted_text': text})
    await record_fact('runtime_check', {'check': 'native_' + node_type + '_text_requirement',
        'performed': True, 'status': 'passed' if proof['schema_valid'] else 'failed',
        'initiator': 'original executor', 'business_commit_claim': False,
        'semantic_quality_verified': False}, request_id=ctx.last_request_id)


def verify_project_text(project,node_type):
    import hashlib
    from app.services.plan_validation import is_meaningful_general_plan
    contract=project_text_contract(node_type)
    text=getattr(project,contract['output_field'],None)
    valid=is_meaningful_general_plan(text) if node_type=='plan' else isinstance(text,str) and len(text.strip())>=200
    return {'status':'passed' if valid else 'failed','node_type':node_type,
        'field':contract['output_field'],'characters':len(text or ''),
        'sha256':hashlib.sha256((text or '').encode()).hexdigest(),
        'source':'original executor output; business commit/status require separate evidence',
        'reason':None if valid else 'native project text requirement not satisfied',
        'semantic_quality_verified':False,'human_approval_preserved':True}


async def observe_project_text_result(project,node_type):
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type not in {'plan','script'}:return
    proof=verify_project_text(project,node_type)
    await record_fact('node_result_verification',proof,body={proof['field']:getattr(project,proof['field'],None)})
    if proof['status']!='passed':raise RuntimeError(proof['reason'])


def split_contract():
    return {'input':'original voiceover.txt snapshot','output':'project.db frames ordered by number',
        'minimum_frames':2,'formats':['replace_frames_json','dash_separated_blocks','native_local_voiceover_fallback'],
        'effects':['replace_all_frames','original XLSX export and snapshots','original split metadata'],
        'managed_verification':['scoped project','unique nonempty UUID','unique sequential number',
            'nonempty voiceover','complete ordered voiceover after whitespace normalization'],
        'semantic_quality_verified':False,'retry_policy':'original runtime only; no supervisor recovery/profile'}


def verify_split_frames(frames, *, project_id, voiceover):
    """Verify actual frame rows against the captured input, without changing them."""
    import hashlib
    from app.services.observation import digest
    rows=[{'project_id':fr.project_id,'uuid':fr.uuid,'number':fr.number,
           'voiceover_text':fr.voiceover_text or ''} for fr in frames]
    errors=[]
    if len(rows)<2:errors.append('minimum_frames')
    if any(r['project_id']!=project_id for r in rows):errors.append('project_scope')
    uuids=[r['uuid'] for r in rows]
    if any(not isinstance(u,str) or not u.strip() for u in uuids) or len(set(uuids))!=len(uuids):errors.append('frame_identity')
    if [r['number'] for r in rows]!=list(range(1,len(rows)+1)):errors.append('frame_order')
    if any(not isinstance(r['voiceover_text'],str) or not r['voiceover_text'].strip() for r in rows):errors.append('empty_voiceover')
    normalize=lambda text:' '.join(text.split())
    known=isinstance(voiceover,str) and bool(voiceover.strip())
    output=' '.join(r['voiceover_text'] for r in rows)
    preserved=normalize(output)==normalize(voiceover) if known else None
    if preserved is False:errors.append('voiceover_changed_or_missing')
    return {'status':'failed' if errors else ('passed' if known else 'unknown'),
        'node_type':'split','frame_count':len(rows),'frame_set_sha256':digest(rows),
        'input_sha256':hashlib.sha256(voiceover.encode()).hexdigest() if known else None,
        'voiceover_preserved':preserved,'errors':errors,
        'reason':'; '.join(errors) if errors else (None if known else 'captured input unavailable'),
        'business_commit_claim':False,'semantic_quality_verified':False}


async def record_split_input(voiceover):
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if ctx is None:return
    ctx.split_input_voiceover=voiceover
    await record_fact('node_input',{'contract':'native_split','source':'original voiceover file snapshot'},
        body={'voiceover':voiceover})


async def record_split_parse(reply,frames_spec,*,frames_source=None,dash_rejected=False):
    import json
    from types import SimpleNamespace
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if ctx is None:return
    try:json.loads(reply);syntax_valid=True
    except (ValueError,TypeError):syntax_valid=False
    frames=[SimpleNamespace(project_id=ctx.scope.project_id,uuid='extracted-'+str(i),number=i,
        voiceover_text=str(f.get('voiceover_text') or f.get('закадр') or f.get('voiceover') or '').strip())
        for i,f in enumerate(frames_spec,1)]
    proof=verify_split_frames(frames,project_id=ctx.scope.project_id,voiceover=ctx.split_input_voiceover)
    await record_fact('parse_result',{'contract':'native_split','syntax_valid':syntax_valid,
        'parsed':bool(frames_spec),'payload_present':bool(frames_spec),'payload_required':False,
        'schema_valid':proof['status']=='passed','frame_count':len(frames_spec),
        'native_fallback_allowed':True,'frames_source':frames_source,
        'fallback_used':frames_source=='local_voiceover_fallback',
        'model_payload_usable':frames_source in {'json_replace_frames','dash_blocks'},
        'dash_rejected':bool(dash_rejected),'semantic_quality_verified':False},request_id=ctx.last_request_id,
        body={'reply':reply,'extracted_frames':frames_spec})
    await record_fact('runtime_check',{'check':'native_split_voiceover_coverage','performed':True,
        **proof,'source':'actual extraction; no DB commit claim'},request_id=ctx.last_request_id)


async def observe_split_result(session,project,node_type):
    from sqlalchemy import select
    from app.models import Frame
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type!='split':return
    frames=(await session.scalars(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number))).all()
    proof=verify_split_frames(frames,project_id=project.id,voiceover=getattr(ctx,'split_input_voiceover',None))
    await record_fact('node_result_verification',{**proof,'source':'original executor frame rows; commit requires outbox'},
        body={'frames':[{'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text} for f in frames]})
    if proof['status']!='passed':
        from app.services.actions_v1 import ActionSupervisor
        await ActionSupervisor(ctx.observer.master).set_held(project_id=ctx.scope.project_id,
            execution_id=ctx.scope.execution_id,held=True,initiator='native_split_verifier',
            rationale=proof['reason'],idempotency_key='split-verify:'+ctx.attempt_id,operator_request=False)
        raise RuntimeError('native split verification not confirmed: '+str(proof['reason']))


async def _observe_project_text(session, project, node_type):
    # Adapter to the common verifier signature; resolves the module global at
    # call time so the original function stays the single implementation.
    await observe_project_text_result(project, node_type)


def _register_builtin_verifiers() -> None:
    """Name the existing result-observation hooks (observation.observe_attempt
    looks them up by node type instead of an if-chain)."""
    from app.orchestrator.node_spec import VerifierSpec, register_verifier
    me = 'app.services.node_capabilities:'
    register_verifier(VerifierSpec('project_text_result', me + '_observe_project_text',
        managed_only=True, success_errors='raise'))
    register_verifier(VerifierSpec('split_result', me + 'observe_split_result',
        managed_only=True, success_errors='raise'))
    register_verifier(VerifierSpec('image_prompts_result', me + 'observe_image_prompt_result',
        managed_only=True, success_errors='raise', on_error=True,
        error_done_flag='image_prompt_verification_recorded',
        error_gap_exempt_flag='image_prompt_hold_saved'))
    register_verifier(VerifierSpec('sfx_result', 'app.services.sfx_plan:observe_sfx_result',
        success_errors='gap', success_flag='sfx_result_observed', on_error=True,
        error_done_flag='sfx_result_observed'))
    # sfx_plan: the same journal hook plus the managed-scope gate (observe_sfx_plan_result).
    # It never raises in an observation scope; on a native failure it only records.
    register_verifier(VerifierSpec('sfx_plan_result', 'app.services.sfx_plan:observe_sfx_plan_result',
        success_errors='raise', success_flag='sfx_result_observed', on_error=True,
        error_done_flag='sfx_result_observed'))
    # Paid media nodes: the same read-only hook for all, bound per node type (hero, items and music are
    # coverage-only entries without a CapabilitySpec verifier name). Observation only, never holds a run.
    register_verifier(VerifierSpec('media_result', 'app.services.media_verification:observe_media_result',
        success_errors='gap', success_flag='media_result_observed', on_error=True,
        error_done_flag='media_result_observed'))
    from app.orchestrator.node_spec import bind_verifier
    for node_type in ('hero', 'items', 'music'):
        bind_verifier(node_type, 'media_result')
    register_verifier(VerifierSpec('animation_result',
        'app.services.native_animation_contracts:observe_animation_result',
        success_errors='gap', on_error=True, error_done_flag='animation_verification_recorded'))


_register_builtin_capabilities()
_register_builtin_verifiers()
