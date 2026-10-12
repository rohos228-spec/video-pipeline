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
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'), ('project_database', 'local_xlsx'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True,
            profile_compatible=True, volatile_attachment_names=True, native_json_retry=False,
            recovery_continuation='none', acceptance='native_split',
            verifier='split_result', contract='frame_rows',
            verifies=('Кадров не меньше двух, у каждого свой uuid и подряд идущий номер',
                      'Закадровый текст сохранён целиком и по порядку',
                      'В управляемом запуске ответ проверяется по контракту ДО записи в БД: негодный ответ не '
                      'трогает проект (SplitContractRejected)',
                      'Если не получилось ни ответа модели, ни локальной разбивки, это записывается типизированным '
                      'сбоем (invalid_json / missing_payload) до любой записи кадров',
                      'При провале проверки после записи запуск удерживается до решения человека; прежние кадры, '
                      'снятые перед заменой, возвращаются откатом (rollback_split)'),
            limits=('Один повтор на ноду за запуск, только по инциденту invalid_json или missing_payload (и 5xx шлюза), '
                    'после исчерпания штатных повторов; повтор идёт тем же исполнителем и ничего не записал до сбоя',
                    'Повтор запрещён после любого применённого пакета (data_applied): замена кадров необратима для '
                    'надзора, провал проверки после записи решает человек',
                    'Повтор не стирает ручную работу: если в проекте есть кадры с промтами, статусами или атрибутами '
                    'и разбивка не помечена завершённой, повторная замена отказывает ДО запроса к модели',
                    'Уже завершённая разбивка (split_completed и не меньше двух кадров) повтором не запускается заново',
                    'Локальная разбивка по предложениям при негодном ответе остаётся принятой деградацией '
                    '(split_degraded), повтор её не заменяет',
                    'Откат возвращает кадры, их uuid, закадр, поля и привязку артефактов; история версий промтов '
                    'выводится заново из полей кадра, а не восстанавливается',
                    'Смена профиля меняет только тело запроса: ответ по-прежнему проверяется до записи'),
            recovery_cap=1),
        C('image_prompts', 'frame_prompts',
            'generate_image_prompts.run → xlsx_step_runners.run_img_pr_xlsx',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_xlsx', 'local_checkpoint'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True,
            profile_compatible=True, volatile_attachment_names=True, native_json_retry=False,
            recovery_continuation='none',
            acceptance='native_image_prompts', verifier='image_prompts_result',
            contract='frame_rows',
            verifies=('Набор кадров, номера и закадр не изменились',
                      'У каждого кадра-родителя основной промпт не короче порога',
                      'Каждая операция записи проверяется до записи: короткий или пустой промпт отбрасывается '
                      '(writeable_img_pr_ops), адрес кадра только по uuid',
                      'Если ни одна пачка не дала пригодных операций, это записывается типизированным сбоем '
                      '(invalid_json / missing_payload) до любой записи',
                      'Причина удержания объясняется человеку по записанной проверке'),
            limits=('Один повтор на ноду за запуск, только по инциденту invalid_json или missing_payload, '
                    'когда ничего не применено и чекпоинт пуст',
                    'Повтор запрещён после любого применённого пакета (data_applied): частичный результат решает человек',
                    'Дублей записей нет: операции адресуются по uuid кадра, а нативный запуск не перезапрашивает '
                    'уже непустой промпт и не затирает ручные правки',
                    'Заполненный, но короткий или негодный промпт повтором не перезапрашивается: удержание после '
                    'провала проверки снимает только человек',
                    'Смена профиля меняет только тело запроса пачки; каждая пачка по-прежнему проверяется до записи'),
            recovery_cap=1),
        C('excel_gpt', 'structured', 'enrich_xlsx.run',
            ('running', 'parse', 'validate', 'apply', 'commit'), ('project_database',),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True, profile_compatible=True,
            acceptance='excel_gpt_group', verifier='excel_gpt_result', contract='structured',
            verifies=('Операции применяются только через общий контракт записи (db_apply)',
                      'Покрытие кадров и проверки группы script_frames_qc',
                      'После шага независимая проверка структуры кадров проекта: uuid не пустые и не повторяются, '
                      'номера кадров положительные и не повторяются; брак записывается как инцидент, запуск не '
                      'удерживается'),
            limits=('Один повтор на ноду за запуск',
                    'Повтор запрещён после любого применённого пакета (частичное применение)'),
            recovery_cap=1),
        C('images', 'image', 'generate_images.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='synthetic_image_scenario', verifier='media_result', contract='media_jobs',
            successor='continue_managed_image',
            verifies=('У каждого кадра с промтом картинки есть файл: запись артефакта привязана к этому кадру, '
                      'файл не пустой, декодируется как PNG, JPEG, WEBP или GIF, не меньше нативного порога сцены '
                      'и не уже 256 пикселей; число картинок сверяется с числом кадров (проверка независимой сессией '
                      'после шага, media_verification)',
                      'Журнал платных задач: у кадра виден task id Kie или его отсутствие (Outsee); неизвестный '
                      'исход заново не отправляется',
                      'Outsee: идемпотентность по ключу запроса (проект, ключ кадра, модель, промт): намерение пишется '
                      'до платного вызова, результат после; возобновление resume_local берёт записанный результат '
                      'без платного вызова (media_local_resume)',
                      'Качество картинки не оценивается; в бою проверка только наблюдает и запуск не удерживает '
                      '(удержание делает синтетический управляемый контур, media_jobs.require_verified_images)'),
            limits=('Надзор допускает ТОЛЬКО локальное возобновление resume_local из записанного результата '
                    '(ключ запроса Outsee с результатом, целый файл): ни одного нового платного запроса',
                    'Платный повтор retry_same_not_admitted: его делает нативный слой или человек',
                    'Попытка Outsee без вердикта или с неизвестным исходом (uncertain) не возобновляется: решает человек',
                    'Один билет на ноду за запуск; надзор ноду не запускает и не перезапускает'),
            recovery_cap=1),
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
                    'Возобновление действует только для открытой задачи Kie в журнале: нативный шаг предметов при '
                    'модели Kie отправляет запрос в Outsee, а не в Kie (docs/MEDIA_ROUTE_AUDIT.md), так что такую '
                    'строку он сам не создаёт; доказано сухим прогоном через run_generation, не маршрутом шага',
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
            ('provider_generation', 'local_file', 'frame_timing', 'artifact_row'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='media_resume_dry_run', recovery_only=True,
            verifier='media_result', contract='media_jobs',
            verifies=('Файл озвучки существует, читается (ffprobe: звуковая дорожка, длительность)',
                      'Независимый верификатор audio_verification.verify_audio: формат из разрешённого набора, '
                      'длительность в допуске, ненулевая амплитуда (тишина даёт провал), результат пишется фактом '
                      'audio_result_verification',
                      'Число кусков озвучки сверяется с числом кадров с закадровым текстом',
                      'Маркер .tts_uncertain_* (таймаут или обрыв после отправки) выводится как неизвестный исход'),
            limits=('Надзор допускает ТОЛЬКО локальное возобновление resume_local по готовому файлу озвучки: '
                    'ни одного нового платного POST',
                    'Повторный платный POST после таймаута запрещён, пока человек не снимет маркер или не положит файл: '
                    'платный повтор retry_same_not_admitted',
                    'Один платный POST на сегмент без ключа идемпотентности: исход таймаута не доказать',
                    'Один билет на ноду за запуск; надзор ноду не запускает и не перезапускает'),
            recovery_cap=1),
        C('assemble', 'assembly', 'assemble.run → assembly.assemble',
            ('inputs', 'processing', 'subtitles', 'verifying', 'verified', 'failed', 'unknown'),
            ('local_file', 'artifact_row'), READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='local_assemble_dry_run', local_recovery=True,
            verifier='media_result', contract='media_jobs',
            verifies=('Итоговый файл собран во временный файл и подменяет прежний только после verify_artifact '
                      '(видео и звук, длительность)',
                      'Независимая проверка частичного файла перед подменой (ffprobe): файл не пуст, есть видеодорожка '
                      'и звуковая дорожка, их длины сходятся, длина файла не короче 0,9 и не длиннее 1,5 озвучки с '
                      'запасом; итог пишется событием artifact_verified (stage assembly_result); доказанный брак '
                      'проваливает попытку, и единственный локальный повтор может её повторить',
                      'Отказ Telegram после проверки не теряет запрос: approve_final сохраняется с пометкой '
                      'notify_failed, сборка остаётся собранной, подтверждение не выдаётся',
                      'Заново читаются файл, его sha256 и запрос approve_final для этого файла',
                      'Сбой сборки типизируется по исходу самой попытки (медиа-сбой), а не по прежнему файлу на диске',
                      'Повтор надзора проверяется независимо от ноды: итог создан после запуска повтора, verify_artifact '
                      'пройден заново, sha256 совпадает с артефактом, запрос approve_final на этот файл ждёт человека, '
                      'журнал платных задач не вырос, ни один платный шов не задет'),
            limits=('Один билет на ноду за запуск; повтор идёт только рабочим процессом внутри режима «только '
                    'возобновление»: платный запрос недостижим',
                    'Повтор допускается только после сбоя, который нода сама считает повторяемым (ffmpeg, проверка '
                    'итога, ошибка файла); ошибки входа (нет клипов, нет озвучки) решает человек',
                    'Подтверждение approve_final нельзя снять повтором: запрос один на пару проект и sha256, надзор '
                    'его не одобряет',
                    'Свежий запуск сборки надзором не допущен: только повтор по билету после сбоя',
                    'Платный запрос недостижим (сухой прогон test_media_dry_run_local_nodes.py: охрана и разбор импортов)'),
            recovery_cap=1),
        C('sfx_plan', 'structured',
            'plan_sfx.run → sfx_plan.plan_sfx_events → ai_result_io.text_job',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_file', 'local_checkpoint'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True, profile_compatible=True,
            native_json_retry=False,
            recovery_continuation='none', acceptance='native_sfx_plan',
            verifier='sfx_plan_result', contract='sfx_plan',
            verifies=('План читается заново из чекпоинта и файла sfx_plan.json независимой сессией',
                      'Виды звуков из каталога, границы таймлайна, длительность, громкость, плотность '
                      'и зазор между событиями (validate_events)',
                      'В управляемом запуске план без подтверждения удерживает запуск'),
            limits=('Один повтор на ноду за запуск',
                    'Только если ответ модели не разобран (invalid_json / missing_payload) и ничего не применено',
                    'Нарушение правил таймлайна у разобранного плана повтором не лечится: решает человек',
                    'Смена профиля меняет только тело единственного текстового запроса плана; ответ по-прежнему '
                    'разбирается и проверяется (validate_events) ДО записи файла плана и чекпоинта',
                    'Не запускает sfx_gen (платный ElevenLabs) и не меняет кадры',
                    'Выключенный SFX (проход без плана) в управляемом запуске не считается проверенным'),
            recovery_cap=1),
        C('sfx_gen', 'audio',
            'generate_sfx.run → sfx_gen.generate_sfx_files → elevenlabs_api/local_synth',
            ('running', 'generating', 'writing', 'verifying', 'verified', 'failed', 'unknown'),
            ('local_file', 'project_database'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='media_resume_dry_run', recovery_only=True,
            verifier='sfx_result', contract='media_jobs',
            verifies=('Каждый звуковой файл существует, читается как WAV и не пустой по уровню',
                      'Независимый верификатор audio_verification.verify_audio проверяет каждый файл: формат, '
                      'звуковая дорожка, длительность, ненулевая амплитуда (тишина или битый файл дают провал)',
                      'Число файлов совпадает с планом, пути лежат внутри папки sfx проекта',
                      'Откат ElevenLabs на локальный синтез виден: факт provider_fallback, инцидент '
                      'provider_degraded и поле provider_fallback у события'),
            limits=('Надзор допускает ТОЛЬКО локальное возобновление resume_local по файлам событий из чекпоинта '
                    'sfx_progress: ни одного нового платного вызова',
                    'Событие с отправленным запросом без записанного исхода остаётся denied (исход неизвестен), решает человек',
                    'Откат после таймаута или обрыва (мог быть списан платёж) не повторяется никогда',
                    'Платный повтор события retry_same_not_admitted: его делает нативный слой или человек',
                    'Один билет на ноду за запуск; надзор ноду не запускает и не перезапускает'),
            recovery_cap=1),
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
                    'Смена профиля запрещена и нода не помечена совместимой с профилями: во входе может быть лента '
                    'картинок (input_image), профиль пересобирает только текстовый запрос, а пачки применяются по '
                    'одной, то есть проверка до записи не охватывает весь результат',
                    'Локальный запасной путь и пропуск shot_02 при сбое провайдера видны фактом animation_degraded '
                    'и инцидентом animation_degraded; сам по себе он повтор не открывает',
                    'Не запускает videos (платная нода с утверждением человеком)'),
            recovery_cap=1),
        C('sd_agent', 'structured',
            'scene_design.run → scene_contracts.verify_scene_agent → pkg:scene_design',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_checkpoint', 'local_file'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True, profile_compatible=True,
            native_json_retry=False,
            recovery_continuation='none', acceptance='native_scene_agent',
            verifier='scene_agent_result', contract='scene_agent',
            observation_hook='app.services.scene_contracts:observe_scene_result',
            verifies=('Агент (characters, world): срез по контракту (обязательный список, id по шаблону, поля) и '
                      'сохранённые ячейки равны тому, что выводит нативный конвертер; кадры и ячейки других '
                      'агентов не тронуты',
                      'Результат читается независимой сессией после шага; без пройденной проверки управляемый '
                      'запуск удерживается, а попытка считается проваленной',
                      'Применение ячеек одной транзакцией с транзакционным фактом data_applied',
                      'Сбой ответа типизируется: invalid_json (нет JSON), missing_payload (нет списка), '
                      'contract_rejected (срез или ячейки нарушают контракт)'),
            limits=('Допущены к управляемому запуску и повтору только агенты characters и world: один запрос '
                    'к модели и запись ячеек одной транзакцией',
                    'skeleton (черновик и редактор с состоянием между запросами, ячейки по частям), camera и '
                    'action (параллельные куски запроса, чекпоинты кусков сбрасываются при сбое, ячейки по '
                    'частям) и устаревший scene_design в управляемый запуск не допускаются: решает человек',
                    'Один повтор на ноду за запуск, только по инциденту invalid_json или missing_payload',
                    'После факта data_applied (ячейки записаны) повтор запрещён как частичное применение; '
                    'проваленный по контракту срез повтором не лечится',
                    'Смена профиля допущена только агентам characters и world (один текстовый запрос без картинок); '
                    'профиль меняет тело запроса, срез проверяется по контракту ДО записи ячеек',
                    'Не запускает остальных агентов и сборку sd_assemble'),
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
    from app.orchestrator.node_spec import CapabilitySpec as C
    scene_hook = 'app.services.scene_contracts:observe_scene_result'
    legacy = ('Устаревший тип enrich_N: управляемый путь и повтор есть только у excel_gpt; граф переносится '
              'миграцией (app/services/graph_migrations.py, scripts/migrate_enrich_graphs.py: сухой проход, '
              'применение, откат), после неё узла этого типа в графе нет.')
    return (
        _watch('scene_design', 'legacy_node_type',
               'Устаревший тип одной ноды на всю фазу не отличить от агента, который в ней идёт: результат '
               'проверяется как объединение агентов и сборки, а повтор небезопасен и остаётся за человеком.',
               verifies=('Для каждого агента веера, который отработал: срез по контракту (список, id, поля) '
                         'и сохранённые ячейки совпадают с тем, что выводит нативный конвертер',
                         'Для сборки: итоговый payload по нативной проверке и сверка с Frame.attrs',
                         'Сбой ответа типизируется как invalid_json, missing_payload или contract_rejected'),
               hook=scene_hook),
        C('sd_assemble', 'structured',
            'scene_design.run_assemble → scene_contracts.verify_scene_assembly → pkg:scene_design',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_checkpoint', 'local_file'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True, profile_compatible=True,
            native_json_retry=False,
            recovery_continuation='none', acceptance='native_scene_assembly',
            verifier='scene_agent_result', contract='scene_agent',
            observation_hook='app.services.scene_contracts:observe_scene_result',
            verifies=('Разбор и проверка итогового payload, сверка его полей с реальными Frame.attrs и реестром сцен',
                      'Результат читается независимой сессией после шага; без пройденной проверки управляемый '
                      'запуск удерживается, а попытка считается проваленной',
                      'Применение (атрибуты кадров, реестр сцен) одной транзакцией с фактом data_applied от db_apply.apply_ops',
                      'Сбой типизируется: invalid_json (нет JSON), missing_payload (нет scenes или ops), '
                      'contract_rejected (правила сборки отклонили)'),
            limits=('Один повтор на ноду за запуск, только по инциденту invalid_json или missing_payload',
                    'Сбой шага откатывает всё, что попытка записала после старта (дочерние кадры камеры, их '
                    'родителей, частичное применение): вход повтора равен входу попытки, uuid дочерних кадров '
                    'выводятся из родителя и номера шота, поэтому первый запрос повтора равен первому запросу сбоя',
                    'После факта data_applied повтор запрещён как частичное применение; проваленный по правилам '
                    'сборки payload повтором не лечится',
                    'Сущности (Entity), scene_space и файлы проверкой результата не покрыты (unchecked_effects)',
                    'В режиме chrono_dyn запроса к модели нет: повтор детерминированной локальной сборки '
                    'ничего не исправит, решает человек',
                    'Смена профиля меняет только тело текстового запроса сборки; payload проверяется правилами '
                    'сборки ДО записи, сбой откатывает записанное, первый запрос повтора равен первому запросу сбоя '
                    'с учётом профиля',
                    'Не запускает остальных агентов и следующие ноды'),
            recovery_cap=1),
        C('hero', 'image', 'generate_hero.run → generate_image_with_retries (Outsee / Kie)',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'),
            READ_ACTIONS + ('retry_same', 'finish'), True,
            native_json_retry=False, recovery_continuation='none',
            acceptance='media_resume_dry_run', recovery_only=True,
            verifier='media_result', contract='media_jobs',
            verifies=('У героя есть файл картинки: существует, не пустой, декодируется, лежит в папке проекта',
                      'Журнал платных задач: task id Kie или отсутствие id у Outsee',
                      'Outsee: идемпотентность по ключу запроса (проект, ключ героя, модель, промт); промт героя v1 '
                      'сохраняется до платного вызова и после рестарта берётся тот же'),
            limits=('Надзор допускает ТОЛЬКО локальное возобновление resume_local из записанного результата '
                    '(ключ запроса Outsee с результатом, целый файл): ни одного нового платного запроса',
                    'Неизвестный исход отправки блокирует повтор по ключу героя, решает человек',
                    'Платный повтор retry_same_not_admitted: его делает нативный слой или человек',
                    'Один билет на ноду за запуск; надзор ноду не запускает и не перезапускает'),
            recovery_cap=1),
        *(_watch(f'enrich_{n}', 'legacy_node_type', legacy,
                 verifies=('Журнал ведётся как у excel_gpt (observe_attempt подставляет excel_gpt)',),
                 hook='app.services.observation:observe_attempt')
          for n in range(1, 6)),
    )


# Recovery decisions for media/paid-or-external node types. Pure declarative data kept OUTSIDE
# NodeCapability on purpose: contract() is built with asdict() and is persisted/sent to the
# decision layer, so a new dataclass field would change those payloads. Nothing reads this
# table at runtime; docs/MEDIA_NODES_READINESS.md and tests/test_media_nodes_recovery_decision.py
# are its only consumers. Every entry is human-only (no supervisor retry) except the local-resume ones (hero, images,
# audio, sfx_gen: only a recorded result is resumed, never a paid resend) and the resume-only ones
# (videos, items, music): the supervisor may only resume a known provider task, never submit a new one, and
# the local-retry one (assemble): a single re-run of a node without any provider call through the original worker.
# Facts are code-grounded (file:line in the doc); "reason" is the honest short justification.
_MEDIA_HUMAN_ONLY = "human_only"
_MEDIA_RESUME_ONLY = "resume_only"
_MEDIA_LOCAL_RETRY = "local_retry"
_MEDIA_LOCAL_RESUME = "resume_local"
MEDIA_RECOVERY_DECISIONS = {
    "hero": {"decision": _MEDIA_LOCAL_RESUME, "cost_class": "paid_per_image",
        "provider": "outsee/kie via generate_image_with_retries",
        "reason": "Outsee gives no task id, so a repeat is made safe by a REQUEST KEY (project, hero key, model, prompt): "
                  "the intent is recorded before the paid call and the result after it (outsee_idempotency). The "
                  "supervisor admits ONLY a local resume (resume_local) of a recorded result with an intact file, with "
                  "zero new paid requests; an attempt without a verdict or with an unknown outcome is denied and a "
                  "human decides. A paid re-submit (retry_same) stays with the native layer or a human."},
    "items": {"decision": _MEDIA_RESUME_ONLY, "cost_class": "paid_per_image",
        "provider": "outsee/kie via generate_image_with_retries",
        "reason": "Supervisor admission is a resume of a known Kie task id only (poll and download inside the "
                  "resume-only scope, zero new paid requests); Outsee has no task id (unknown outcome, never "
                  "resubmitted) and a paid re-submit stays with the native layer or a human: a retry could double-pay. "
                  "The native items step routes a Kie slug to the Outsee seam (docs/MEDIA_ROUTE_AUDIT.md), so the Kie "
                  "task row this resume needs is not created by the step itself."},
    "images": {"decision": _MEDIA_LOCAL_RESUME, "cost_class": "paid_per_image",
        "provider": "kie (managed, synthetic loopback) / outsee (native, idempotent by request key)",
        "reason": "Outsee idempotency by request key (project, frame key, model, prompt): intent before the paid call, "
                  "result after it. The supervisor admits ONLY a local resume (resume_local) of a recorded result with "
                  "an intact file, zero new paid requests; an unknown outcome is denied and a human decides. A paid "
                  "re-submit (retry_same) stays with the native layer or a human."},
    "videos": {"decision": _MEDIA_RESUME_ONLY, "cost_class": "paid_per_clip_highest",
        "provider": "outsee/kie (Seedance, Kling, Veo ladder)",
        "reason": "Supervisor admission is a resume of a known Kie task id only (poll and download inside the "
                  "resume-only scope, zero new paid requests); Outsee has no id and the native ladder is up to "
                  "4 primary + 3 fallback paid submits per clip, so a paid re-submit by the supervisor is not "
                  "admitted: a blind retry can double-pay."},
    "audio": {"decision": _MEDIA_LOCAL_RESUME, "cost_class": "paid_per_character",
        "provider": "elevenlabs_api (direct HTTP)",
        "reason": "The request has no idempotency key, so a paid re-submit (a second POST could be charged twice) is never admitted to the supervisor. It admits "
                  "ONLY a local resume (resume_local) of a finished voice file already on disk (zero new paid requests); "
                  "an .tts_uncertain_* marker (timeout after send) is denied and a human decides."},
    "music": {"decision": _MEDIA_RESUME_ONLY, "cost_class": "paid_per_generation",
        "provider": "kie suno (direct) or browser outsee",
        "reason": "Supervisor admission is a resume of a known Suno task id only (poll and download inside the "
                  "resume-only scope, saved prompt, zero new paid requests); a paid re-submit is not admitted "
                  "because a retry could re-submit and re-pay."},
    "sfx_gen": {"decision": _MEDIA_LOCAL_RESUME, "cost_class": "paid_per_event_small_or_free_local",
        "provider": "elevenlabs sound-generation (or local_synth when no key)",
        "reason": "A per-event paid call has no idempotency key, so a paid re-send is never admitted to the supervisor. It "
                  "admits ONLY a local resume (resume_local) of the event files recorded in the sfx_progress checkpoint "
                  "(zero new paid requests); an event with a request sent and no recorded outcome is denied and a human "
                  "decides."},
    "assemble": {"decision": _MEDIA_LOCAL_RETRY, "cost_class": "no_provider_spend_local_cpu",
        "provider": "local ffmpeg (+ local ASR timing)",
        "reason": "No provider spend (dry run: guard and import scan). The supervisor re-runs the node ONCE through the "
                  "original worker under a recovery ticket, inside the resume-only scope; the result is verified from "
                  "independent facts (new file after the claim, verify_artifact, sha256 against the artifact, a pending "
                  "approve_final for that file, an unchanged provider ledger, no paid seam hit). The human barrier "
                  "approve_final is never touched; a fresh start and every failure the node does not retry itself stay "
                  "with a human."},
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


def require_managed_capability(node_type: str, *, recovery_claim: bool = False):
    """The managed capability of a node a supervisor may START. ``recovery_claim=True`` is passed only by a worker
    attempt that has just claimed a recovery ticket: a local_recovery node (assemble) is admitted for it and for
    nothing else, so a fresh managed start of such a node stays refused."""
    capability = capability_for(node_type)
    if not capability.managed:
        raise ValueError("node adapter has no accepted managed executor")
    from app.orchestrator.node_spec import capability_spec_for
    spec = capability_spec_for(node_type)
    if spec is not None and spec.recovery_only:
        raise ValueError("node is managed for supervised recovery only: the supervisor never starts or restarts it")
    if spec is not None and spec.local_recovery and not recovery_claim:
        raise ValueError("node is managed for supervised recovery only: the supervisor never starts it fresh, "
                         "it re-runs it once under a recovery ticket")
    return capability


def require_managed_node(node):
    """require_managed_capability for a GRAPH NODE: the capability of its effective type plus the per-node
    admission rule of that type (an sd_agent node of a refused agent may not enter a managed scope)."""
    from app.orchestrator.node_spec import node_admission_problem
    from app.services.excel_gpt_node import effective_node_type
    capability = require_managed_capability(effective_node_type(node))
    problem = node_admission_problem(node)
    if problem:
        raise ValueError(problem)
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
    if proof.get('observe_only'):
        lines=[l.replace('по правилу безопасности запуск удержан, пока человек не посмотрит данные',
            'запуск не остановлен: это режим наблюдения') for l in lines]
        summary=' '.join(lines)+' Режим наблюдения: запуск не удерживался и ничего не повторялось, решает человек.'
    else:
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


def note_image_prompt_unusable(reply,*,batch_i=None):
    """A batch gave no writeable operation and the node cannot split further. Only remembered on the attempt:
    the typed fact is written by record_image_prompt_unusable when the whole step finally fails, so a native
    second attempt that succeeds never leaves an open incident behind."""
    from app.services.observation import _attempt
    ctx=_attempt.get()
    if ctx is not None:ctx.image_prompt_unusable={'reply':reply or '','batch':batch_i}


def clear_image_prompt_unusable():
    from app.services.observation import _attempt
    ctx=_attempt.get()
    if ctx is not None:ctx.image_prompt_unusable=None


async def record_image_prompt_unusable():
    """Terminal typed failure of image_prompts: no batch produced a writeable operation, so nothing was written.
    payload_required makes diagnosis derive invalid_json (reply is not JSON) or missing_payload (JSON without
    usable prompt operations); the step has failed and a supervised retry is idempotent. Journal only, never raises."""
    import json
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get();note=getattr(ctx,'image_prompt_unusable',None) if ctx else None
    if not note or getattr(ctx,'image_prompt_unusable_recorded',False):return False
    ctx.image_prompt_unusable_recorded=True
    reply,batch_i=note['reply'],note['batch']
    try:json.loads(reply or '');syntax=True
    except (ValueError,TypeError):syntax=False
    requests=getattr(ctx,'image_prompt_batch_requests',None) or {}
    request_id=requests.get(batch_i) if batch_i is not None else None
    if request_id is None and requests:request_id=requests[max(requests)]
    await record_fact('parse_result',{'contract':'native_image_prompts','terminal':True,'batch':batch_i,
        'syntax_valid':syntax,'parsed':False,'payload_present':False,'payload_required':True,
        'nonempty_payload_required':True,'schema_valid':False,'ops_count':0,'writeable_ops_count':0,
        'applied_before':False,'semantic_quality_verified':False,
        'parser':'original img_pr_batches.parse_img_pr_ops; no writeable operation from any batch'},
        request_id=request_id,body={'text':reply})
    return True


async def observe_image_prompt_result(session,project,node_type):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models import Frame
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or node_type!='image_prompts':return
    observe_only=ctx.scope.mode!='managed'
    if observe_only and not _observe_only_applies(ctx):return
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
    if observe_only:proof={**proof,'observe_only':True,'run_held':False}
    await record_fact('node_result_verification',proof,body=proof)
    ctx.image_prompt_verification_recorded=True
    if read_error:ctx.observer.mark_gap(ctx.scope.execution_id,read_error)
    if observe_only:return  # observe scope: the incident is the result; nothing is held, nothing raised
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


async def record_observe_only_proof(ctx,node_type,proof,*,body=None,incident=True):
    """Observe-mode result check (C02): journal the proof and, when it failed, a failed ``runtime_check`` that
    diagnosis turns into a ``contract_rejected`` incident. Never raises, never holds, never retries: the run
    goes on, a human sees the incident. A journal failure is an audit gap, not a failure of the node."""
    from app.services.observation import record_fact
    proof={**proof,'observe_only':True,'run_held':False}
    try:
        await record_fact('node_result_verification',proof,body=body if body is not None else proof)
        if incident and proof.get('status')=='failed':
            await record_fact('runtime_check',{'check':f'результата ноды {node_type} (режим наблюдения, запуск не остановлен)',
                'status':'failed','performed':True,'observe_only':True,'reason':str(proof.get('reason'))[:200],
                'business_commit_claim':False,'semantic_quality_verified':False})
    except Exception as exc:
        try:ctx.observer.mark_gap(ctx.scope.execution_id,exc)
        except Exception:pass


def _observe_only_applies(ctx):
    """Observe scope, successful native attempt. A failed attempt already has its own node_error incident."""
    return bool(ctx) and ctx.scope.mode!='managed' and getattr(ctx,'attempt_exception',None) is None


async def observe_project_text_result(project,node_type):
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or node_type not in {'plan','script'}:return
    if ctx.scope.mode!='managed':
        if _observe_only_applies(ctx):
            try:proof=verify_project_text(project,node_type)
            except Exception as exc:
                ctx.observer.mark_gap(ctx.scope.execution_id,exc);return
            await record_observe_only_proof(ctx,node_type,proof,body={proof['field']:getattr(project,proof['field'],None)})
        return
    proof=verify_project_text(project,node_type)
    await record_fact('node_result_verification',proof,body={proof['field']:getattr(project,proof['field'],None)})
    if proof['status']!='passed':raise RuntimeError(proof['reason'])


def split_contract():
    return {'input':'original voiceover.txt snapshot','output':'project.db frames ordered by number',
        'minimum_frames':2,'formats':['replace_frames_json','dash_separated_blocks','native_local_voiceover_fallback'],
        'effects':['replace_all_frames','original XLSX export and snapshots','original split metadata'],
        'managed_verification':['scoped project','unique nonempty UUID','unique sequential number',
            'nonempty voiceover','complete ordered voiceover after whitespace normalization'],
        'pre_write_validation':'managed runs: answer is verified before any DB write (E1b); invalid -> SplitContractRejected, project untouched',
        'semantic_quality_verified':False,
        'retry_policy':('original native runtime first; after it is exhausted with invalid_json/missing_payload '
            '(nothing written: the answer is verified before any DB write) ONE bounded supervisor retry_same or '
            'approved-profile retry of the same executor. Refused after any applied package, over frames that carry '
            'manual work, and for an already completed split. A post-write verification failure holds the run and '
            'restores the pre-replacement frames (rollback_split); it is never retried by the supervisor.')}


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


class SplitContractRejected(RuntimeError):
    """The model's split answer violates the split contract; raised BEFORE any DB write (E1b)."""
    failure_class='contract_rejected'
    def __init__(self,errors,reason):
        self.errors=list(errors);super().__init__('split answer rejected before write: '+str(reason))


def _split_spec_frames(frames_spec,project_id):
    from types import SimpleNamespace
    return [SimpleNamespace(project_id=project_id,uuid='extracted-'+str(i),number=i,
        voiceover_text=str(f.get('voiceover_text') or f.get('закадр') or f.get('voiceover') or '').strip())
        for i,f in enumerate(frames_spec,1)]


async def enforce_split_contract_before_write(frames_spec):
    """Managed runs only: validate the extracted frames against the split contract before
    anything reaches the DB. An invalid answer raises SplitContractRejected and the project
    stays untouched. Outside a managed attempt the native behaviour is unchanged."""
    from app.services.observation import _attempt
    ctx=_attempt.get()
    if ctx is None or ctx.scope.mode!='managed':return
    proof=verify_split_frames(_split_spec_frames(frames_spec,ctx.scope.project_id),
        project_id=ctx.scope.project_id,voiceover=getattr(ctx,'split_input_voiceover',None))
    if proof['status']=='failed':raise SplitContractRejected(proof['errors'],proof['reason'])


# --- D05: safe split retry (preflight over manual work) and rollback to the pre-replacement frames -------------------
# Project.meta keys that split_frames.run resets after a replacement; the snapshot keeps their previous values.
SPLIT_SNAPSHOT_META_KEYS=('split_completed','split_params_applied','enrich_completed_slots','excel_gpt_completed_keys',
    'active_excel_gpt_node_key','scene_design','scene_registry')
_SPLIT_SNAPSHOT_COLUMNS=('id','number','voiceover_text','meaning','transition_from','transition_to','duration_seconds',
    'start_ts','end_ts','image_prompt','animation_prompt','status','attrs','uuid','sort_key','scene_id')


class SplitRetryRefused(RuntimeError):
    """A supervised split retry would replace frames that carry work. Raised BEFORE any model request or write."""
    failure_class='retry_refused'


def is_supervised_retry():
    """True inside the worker attempt of a supervisor recovery ticket (retry_same / retry_profile)."""
    from app.services.observation import _attempt
    ctx=_attempt.get()
    attempt_id=getattr(ctx,'attempt_id',None)
    info=getattr(getattr(ctx,'session',None),'info',None)
    return bool(attempt_id and isinstance(info,dict) and info.get('recovery_attempt_id')==attempt_id)


def frame_has_manual_work(frame):
    """A frame that is more than a bare split result: a prompt, a non-initial status or attributes of a later step."""
    attrs=frame.attrs if isinstance(getattr(frame,'attrs',None),dict) else {}
    status=getattr(frame.status,'value',frame.status)
    return bool((frame.image_prompt or '').strip() or (getattr(frame,'animation_prompt',None) or '').strip()
        or attrs or status not in (None,'','planned'))


def split_retry_refusal(frames,meta):
    """Russian reason why a repeated split must not run over the existing frames, or None. Pure.

    A completed split (>=2 frames and split_completed) is skipped by the executor itself, so it is not a refusal.
    Otherwise existing frames carrying work (prompts, statuses, attributes) would be replaced by the new answer."""
    frames=list(frames or [])
    if len(frames)>=2 and (meta or {}).get('split_completed'):return None
    worked=[f for f in frames if frame_has_manual_work(f)]
    if not worked:return None
    nums=', '.join(str(f.number) for f in worked[:10])
    return ('Повтор разбивки отклонён до запроса к модели: в проекте есть кадры с промтами, статусами или '
        f'атрибутами (кадры: {nums}), а разбивка не помечена завершённой. Замена стёрла бы эту работу; решает человек.')


def _jsonable(value):
    import json
    return json.loads(json.dumps(value,ensure_ascii=False,default=lambda o:getattr(o,'value',None) or str(o)))


async def capture_split_snapshot(session,project):
    """Snapshot of the project's frames, their artifact links and the meta keys a split resets, taken before the
    replacement. None when the project has no frames (nothing to give back). Read-only; the file copy is best effort."""
    import json
    from sqlalchemy import select
    from app.models import Artifact,Frame
    frames=(await session.execute(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number))).scalars().all()
    if not frames:return None
    links=(await session.execute(select(Artifact.id,Artifact.frame_id).where(Artifact.project_id==project.id,
        Artifact.frame_id.is_not(None)))).all()
    meta=project.meta if isinstance(project.meta,dict) else {}
    snapshot={'version':1,'project_id':project.id,
        'frames':[_jsonable({c:getattr(f,c) for c in _SPLIT_SNAPSHOT_COLUMNS}) for f in frames],
        'artifact_links':[{'artifact_id':a,'frame_id':f} for a,f in links],
        'meta':_jsonable({k:meta[k] for k in SPLIT_SNAPSHOT_META_KEYS if k in meta}),
        'meta_absent':[k for k in SPLIT_SNAPSHOT_META_KEYS if k not in meta]}
    try:
        from app.services.observation import _attempt
        ctx=_attempt.get();name=(ctx.attempt_id if ctx is not None and ctx.attempt_id else 'manual')
        folder=project.data_dir/'split_rollback';folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'{name}.json';path.write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
        snapshot['path']=str(path)
    except Exception:pass
    return snapshot


async def rollback_split(session,project,snapshot):
    """Give back the frames of a snapshot (same ids, uuids, numbers, text, prompts, attributes), re-link their
    artifacts and restore the meta keys the split reset. The frames of the rejected replacement are removed.
    Prompt versions, texts and edges are derived again from the frame fields (backfill), not restored from history."""
    from sqlalchemy import delete,select,update
    from app.models import Artifact,Frame,FrameEdge,FrameStatus,FrameText,PromptVersion
    from app.services import db_v2
    if not isinstance(snapshot,dict) or not snapshot.get('frames') or snapshot.get('project_id')!=project.id:
        return {'rolled_back':False,'reason':'no_prior_frames'}
    await session.execute(update(Artifact).where(Artifact.project_id==project.id).values(frame_id=None))
    await session.execute(delete(FrameEdge).where(FrameEdge.project_id==project.id))
    current=list((await session.execute(select(Frame.id).where(Frame.project_id==project.id))).scalars())
    if current:
        await session.execute(delete(PromptVersion).where(PromptVersion.frame_id.in_(current)))
        await session.execute(delete(FrameText).where(FrameText.frame_id.in_(current)))
        await session.execute(delete(Frame).where(Frame.project_id==project.id))
    await session.flush()
    # bulk deletes leave the removed rows in the identity map; a restored row has the old primary key
    for obj in list(session.identity_map.values()):
        if (isinstance(obj,(Frame,FrameEdge)) and obj.project_id==project.id) or (
                isinstance(obj,(FrameText,PromptVersion)) and obj.frame_id in current):
            session.expunge(obj)
    for row in snapshot['frames']:
        values={c:row.get(c) for c in _SPLIT_SNAPSHOT_COLUMNS}
        values['status']=FrameStatus(values['status']) if values.get('status') else FrameStatus.planned
        values['attrs']=values.get('attrs') or {}
        session.add(Frame(project_id=project.id,**values))
    await session.flush()
    restored=set(r['id'] for r in snapshot['frames'])
    relinked={}
    for link in snapshot.get('artifact_links') or []:
        if link.get('frame_id') in restored:
            relinked[link['artifact_id']]=link['frame_id']
            await session.execute(update(Artifact).where(Artifact.id==link['artifact_id'],
                Artifact.project_id==project.id).values(frame_id=link['frame_id']))
    from sqlalchemy.orm.attributes import set_committed_value
    for obj in list(session.identity_map.values()):
        if isinstance(obj,Artifact) and obj.project_id==project.id:
            set_committed_value(obj,'frame_id',relinked.get(obj.id))
    meta=dict(project.meta or {})
    for key in snapshot.get('meta_absent') or []:meta.pop(key,None)
    meta.update(snapshot.get('meta') or {})
    project.meta=meta
    await session.flush()
    await db_v2.backfill_project_v2(session,project)
    await session.flush()
    return {'rolled_back':True,'frames':len(snapshot['frames']),'artifact_links':len(snapshot.get('artifact_links') or [])}


async def prepare_split_replacement(session,project):
    """Called by split_frames.run right before the model is asked. In a supervised retry it refuses to run over
    frames with work (no request, no write). In a managed attempt it keeps the pre-replacement snapshot on the
    attempt context for rollback_split. Outside a managed scope nothing changes."""
    from sqlalchemy import select
    from app.models import Frame
    from app.services.observation import _attempt
    ctx=_attempt.get()
    if ctx is None or ctx.scope.mode!='managed':return None
    frames=(await session.execute(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number))).scalars().all()
    if is_supervised_retry():
        reason=split_retry_refusal(frames,project.meta)
        if reason:raise SplitRetryRefused(reason)
    ctx.split_pre_snapshot=await capture_split_snapshot(session,project)
    return ctx.split_pre_snapshot


async def record_split_parse(reply,frames_spec,*,frames_source=None,dash_rejected=False):
    import json
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if ctx is None:return
    try:json.loads(reply);syntax_valid=True
    except (ValueError,TypeError):syntax_valid=False
    frames=_split_spec_frames(frames_spec,ctx.scope.project_id)
    proof=verify_split_frames(frames,project_id=ctx.scope.project_id,voiceover=ctx.split_input_voiceover)
    # payload_required only when NOTHING usable exists (not even the native local fallback): then nothing is
    # written, the step fails and diagnosis types it invalid_json / missing_payload (a retry is idempotent).
    # A usable answer or the accepted local fallback never opens such an incident (split_degraded covers it).
    await record_fact('parse_result',{'contract':'native_split','syntax_valid':syntax_valid,
        'parsed':bool(frames_spec),'payload_present':bool(frames_spec),'payload_required':not frames_spec,
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
    if not ctx or node_type!='split':return
    if ctx.scope.mode!='managed':
        if _observe_only_applies(ctx):
            try:
                with session.no_autoflush:
                    frames=(await session.scalars(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number))).all()
                proof=verify_split_frames(frames,project_id=project.id,voiceover=getattr(ctx,'split_input_voiceover',None))
            except Exception as exc:
                ctx.observer.mark_gap(ctx.scope.execution_id,exc);return
            await record_observe_only_proof(ctx,'split',{**proof,'source':'original executor frame rows; observe mode'},
                body={'frames':[{'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text} for f in frames]})
        return
    frames=(await session.scalars(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number))).all()
    proof=verify_split_frames(frames,project_id=project.id,voiceover=getattr(ctx,'split_input_voiceover',None))
    await record_fact('node_result_verification',{**proof,'source':'original executor frame rows; commit requires outbox'},
        body={'frames':[{'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text} for f in frames]})
    if proof['status']!='passed':
        from app.services.actions_v1 import ActionSupervisor
        rationale=str(proof['reason'])
        snapshot=getattr(ctx,'split_pre_snapshot',None)
        if snapshot:
            # D05: the replacement is already committed. Give the previous frames back, then hold. The run is
            # never retried by the supervisor (data_applied exists); a human decides what happens next.
            try:
                outcome=await rollback_split(session,project,snapshot)
                await session.commit()
                ok=bool(outcome.get('rolled_back'))
            except Exception as exc:
                try:await session.rollback()
                except Exception:pass
                outcome={'rolled_back':False,'reason':type(exc).__name__};ok=False
            await record_fact('runtime_check',{'check':'откат разбивки к прежним кадрам','status':'passed' if ok else 'failed',
                'performed':True,'frames':outcome.get('frames'),'reason':outcome.get('reason'),
                'business_commit_claim':False,'semantic_quality_verified':False})
            rationale+='; прежние кадры возвращены' if ok else '; откат к прежним кадрам не удался'
        await ActionSupervisor(ctx.observer.master).set_held(project_id=ctx.scope.project_id,
            execution_id=ctx.scope.execution_id,held=True,initiator='native_split_verifier',
            rationale=rationale,idempotency_key='split-verify:'+ctx.attempt_id,operator_request=False)
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
    # plan/script/split/image_prompts (C02): the same hook runs in observe scope too. There it only journals the
    # proof and an incident (record_observe_only_proof); the hold/raise stays managed-only inside the hook.
    register_verifier(VerifierSpec('project_text_result', me + '_observe_project_text',
        success_errors='raise'))
    register_verifier(VerifierSpec('split_result', me + 'observe_split_result',
        success_errors='raise'))
    register_verifier(VerifierSpec('image_prompts_result', me + 'observe_image_prompt_result',
        success_errors='raise', on_error=True,
        error_done_flag='image_prompt_verification_recorded',
        error_gap_exempt_flag='image_prompt_hold_saved'))
    register_verifier(VerifierSpec('excel_gpt_result', me + 'observe_excel_gpt_result',
        success_errors='gap'))
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


def _register_scene_agent_admission() -> None:
    """sd_agent is managed per TYPE but admitted per AGENT (scene_contracts.RETRY_ADMITTED_AGENTS)."""
    from app.orchestrator import node_spec
    from app.orchestrator.node_spec import VerifierSpec, register_verifier
    from app.services import scene_contracts
    register_verifier(VerifierSpec('scene_agent_result', 'app.services.scene_contracts:observe_scene_agent_gate',
        managed_only=True, success_errors='raise'))
    node_spec.register_contract_builder('scene_agent', scene_contracts.scene_agent_contract, replace=True)
    node_spec.register_node_admission('sd_agent', scene_contracts.scene_retry_admission_problem, replace=True)
    node_spec.register_node_admission('scene_design', scene_contracts.scene_retry_admission_problem, replace=True)


_register_builtin_capabilities()
_register_builtin_verifiers()
_register_scene_agent_admission()


# ---------------------------------------------------------------------------
# C02: named result check of excel_gpt (observation only: it never holds or retries)
# ---------------------------------------------------------------------------

def verify_frame_structure(frames, *, project_id):
    """Structural integrity of the project's Frame rows after an excel_gpt step (read-only, no quality claim).

    ``failed``: a frame of another project, an empty or repeated uuid, a missing, non-positive or repeated
    number. ``unknown``: no frames yet (nothing to check, not a pass). It does NOT prove that the step covered
    the frames it had to (script_frames_qc coverage stays with the original runtime)."""
    rows = [{'uuid': f.uuid, 'number': f.number, 'project_id': f.project_id} for f in frames]
    errors = []
    if any(r['project_id'] != project_id for r in rows):
        errors.append('project_scope')
    uuids = [r['uuid'] for r in rows]
    if any(not isinstance(u, str) or not u.strip() for u in uuids):
        errors.append('missing_uuid')
    elif len(set(uuids)) != len(uuids):
        errors.append('duplicate_uuid')
    numbers = [r['number'] for r in rows]
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in numbers):
        errors.append('bad_number')
    elif len(set(numbers)) != len(numbers):
        errors.append('duplicate_number')
    status = 'failed' if errors else ('passed' if rows else 'unknown')
    return {'node_type': 'excel_gpt', 'contract': 'native_frame_structure', 'status': status,
            'reason': errors[0] if errors else (None if rows else 'no_frames_yet'), 'errors': errors,
            'frame_count': len(rows), 'business_commit_claim': False, 'semantic_quality_verified': False,
            'source': 'independent read of project Frame rows after the step'}


async def observe_excel_gpt_result(session, project, node_type):
    """Verifier hook of excel_gpt (named ``excel_gpt_result``), observation only. Runs after a successful native
    attempt in every scope: journals the proof, a failed one also as an incident. Never raises or holds."""
    from sqlalchemy import select
    from app.models import Frame
    from app.services.observation import _attempt
    ctx = _attempt.get()
    if ctx is None or session is None or getattr(ctx, 'attempt_exception', None) is not None:
        return
    try:
        with session.no_autoflush:
            frames = (await session.scalars(select(Frame).where(Frame.project_id == ctx.scope.project_id)
                                            .order_by(Frame.number))).all()
        proof = verify_frame_structure(frames, project_id=ctx.scope.project_id)
    except Exception as exc:  # noqa: BLE001
        ctx.observer.mark_gap(ctx.scope.execution_id, exc)
        return
    await record_observe_only_proof(ctx, 'excel_gpt', proof, body=proof)


# ---------------------------------------------------------------------------
# C11: per-node proof registry ("what is in the data" vs "what a run confirmed")
# ---------------------------------------------------------------------------
# U unit test, D dry run through the real code with a replaced model, L live run on a real provider
# (docs/LIVE_RUNS.md), H manual check by a human. ``proof`` is the text of the confirmation column and is
# checked against the registry and docs/LIVE_RUNS.md by tests/w4_2_c11_proof_registry_test.py.
# ``live_node`` lists dates of a live run of THE NODE; ``live_seam`` of a seam only (one provider call);
# a node with no live run of its own says why in ``why_no_live`` (shown as "только сухо: причина").
_NO_LIVE_ROW = 'в реестре живых прогонов строки по этой ноде нет'
_RESUME_REASON = 'возобновление нужно проверять на реально оплаченной задаче провайдера, такого прогона не было'
_LEGACY_REASON = 'устаревший тип: живой прогон не планируется, граф переносится миграцией'
NODE_PROOF = {
    'plan': dict(proof='D + L 2026-10-08 (Vibecode: сбой смоделирован, восстановление реальное)',
        live_node=('2026-10-08',), live_seam=(), why_no_live=''),
    'script': dict(proof='D + L 2026-10-08 (Vibecode: сбой смоделирован, восстановление реальное)',
        live_node=('2026-10-08',), live_seam=(), why_no_live=''),
    'excel_gpt': dict(proof='D + L 2026-10-01 (сбой смоделирован)', live_node=('2026-10-01',), live_seam=(),
        why_no_live=''),
    'split': dict(proof='U/D', live_node=(), live_seam=(), why_no_live=_NO_LIVE_ROW),
    'image_prompts': dict(proof='U/D', live_node=(), live_seam=(), why_no_live=_NO_LIVE_ROW),
    'sfx_plan': dict(proof='D + L 2026-10-08 (Vibecode: сбой смоделирован, восстановление реальное)',
        live_node=('2026-10-08',), live_seam=(), why_no_live=''),
    'animation_prompts': dict(proof='D + L 2026-10-08 (Vibecode: сбой смоделирован, восстановление реальное)',
        live_node=('2026-10-08',), live_seam=(), why_no_live=''),
    'images': dict(proof='U/D (только синтетический путь); шов Outsee L 2026-10-07 и 2026-10-12', live_node=(),
        live_seam=('2026-10-07', '2026-10-12'),
        why_no_live='нода целиком живьём не гонялась (проверен один запрос картинки), управляемый путь только '
                    'синтетический, маршрут Outsee или Kie ждёт решения владельца'),
    'videos': dict(proof='D (только возобновление)', live_node=(), live_seam=(), why_no_live=_RESUME_REASON),
    'items': dict(proof='D (только возобновление)', live_node=(), live_seam=(), why_no_live=_RESUME_REASON),
    'music': dict(proof='D + L 2026-10-12 (генерация Kie и возобновление той же задачи; списание выше каталожной оценки: цена открыта)',
        live_node=('2026-10-12',), live_seam=(), why_no_live=''),
    'assemble': dict(proof='D (монтажный движок подменён)', live_node=(), live_seam=(),
        why_no_live='живой сборки под надзором не было, в сухих прогонах монтажный движок подменён'),
    'sd_agent': dict(proof='D (допуск: characters, world) + L 2026-10-08 (только агент characters; world живьём не гонялся)',
        live_node=('2026-10-08',), live_seam=(), why_no_live=''),
    'sd_assemble': dict(proof='D + L 2026-10-08 (после исправления дефекта проверки освещения)',
        live_node=('2026-10-08',), live_seam=(), why_no_live=''),
    'hero': dict(proof='D', live_node=(), live_seam=(), why_no_live=_NO_LIVE_ROW),
    'audio': dict(proof='U; шов TTS L 2026-10-07', live_node=(), live_seam=('2026-10-07',),
        why_no_live='нода целиком живьём не гонялась, проверен только один запрос озвучки'),
    'sfx_gen': dict(proof='D; шов SFX L 2026-10-07', live_node=(), live_seam=('2026-10-07',),
        why_no_live='нода целиком живьём не гонялась, проверен только один запрос звукового эффекта'),
    'scene_design': dict(proof='D (устаревший тип)', live_node=(), live_seam=(), why_no_live=_LEGACY_REASON),
    **{f'enrich_{n}': dict(proof='U (миграция графов, устаревший тип)', live_node=(), live_seam=(),
                           why_no_live=_LEGACY_REASON) for n in range(1, 6)},
    'publish': dict(proof='U', live_node=(), live_seam=(),
        why_no_live='публикация необратима, живой прогон возможен только по решению владельца; L и H нет'),
}
_LEVEL_RU = {'managed': 'под управлением', 'observed': 'под наблюдением', 'human_only': 'только человек'}
PROOF_TABLE_HEADER = ('| Нода | Есть в данных (уровень) | Подтверждено прогоном (U/D/L/H) | '
                      'Чего нет (без L: «только сухо» и причина) |')


def node_proof_rows():
    """One row per registered node type: level from coverage_level() and the proof entry above."""
    from app.orchestrator.node_spec import coverage_level, registered_capability_specs
    from app.services.node_coverage import registry_node_types
    specs = registered_capability_specs()
    rows = []
    for node_type in registry_node_types():
        spec, proof = specs.get(node_type), NODE_PROOF.get(node_type)
        if spec is None or proof is None:
            rows.append({'node_type': node_type, 'level': None, 'proof': None, 'complete': False})
            continue
        rows.append({'node_type': node_type, 'level': coverage_level(spec), 'proof': proof['proof'],
                     'live_node': tuple(proof['live_node']), 'live_seam': tuple(proof['live_seam']),
                     'missing': ('-' if proof['live_node'] else 'только сухо: ' + proof['why_no_live']),
                     'complete': True})
    return rows


def render_proof_table() -> str:
    """The markdown table of docs/NODE_COVERAGE.md (section "Подтверждено по нодам"), generated from the registry."""
    lines = [PROOF_TABLE_HEADER, '|---|---|---|---|']
    rows = {r['node_type']: r for r in node_proof_rows()}
    for node_type in NODE_PROOF:
        row = rows.get(node_type)
        if row is None or not row['complete']:
            continue
        lines.append(f"| `{node_type}` | {_LEVEL_RU[row['level']]} | {row['proof']} | {row['missing']} |")
    return '\n'.join(lines)
