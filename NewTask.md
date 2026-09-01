Новые находки и потенциальные риски
🔴 Критические зоны риска
1. Outsee concurrency_limit backoff может привести к слишком долгому ожиданию
Где искать:
app/bots/outsee_http.py:120-145 (функция _post_generate)
Проблема:
При concurrency_limit от Outsee API добавлена логика ожидания до 24 попыток с backoff:
_CONCURRENCY_MAX_WAITS = 24
_CONCURRENCY_BACKOFF_S = (15.0, 30.0, 45.0, 75.0, 120.0)  # 5 уровней
Риск:
Максимальное время ожидания: 24 × 120s = 2880s (48 минут!)
Если Outsee API стабильно отдаёт concurrency_limit, пользователь будет ждать почти час
Нет явного WARNING в логах о длительном ожидании
Парето-фикс (10 минут):
# app/bots/outsee_http.py — ДОБАВИТЬ в _post_generate:
if waits >= 10:
    logger.warning(
        "outsee_api: concurrency_limit уже {} попыток ({}с прошло). "
        "Проверь CREATE_MAX_PARALLEL_OUTSEE (сейчас={}) и загрузку Outsee API",
        waits, sum(_CONCURRENCY_BACKOFF_S[:waits]), settings.create_max_parallel_outsee
    )

Приоритет: P1 (UX-проблема, может блокировать генерацию)
2. Отсутствие generate_video.py в orchestrator/steps
Где искать:
app/orchestrator/steps/generate_video.py
Проблема:
Файл возвращает 404, хотя должен существовать. Возможно:
Удалён при рефакторинге Grsai
Перемещён в другое место (но импорт не обновлён)
Забыли закоммитить
Риск:
Если шаг generate_video всё ещё используется в STEPS (menu.py), воркер упадёт при попытке запустить генерацию видео
step_by_code("video") вернёт StepDef, но исполнителя нет
Парето-фикс (15 минут):
Проверить app/telegram/menu.py — есть ли video в STEPS
Если есть — создать generate_video.py или удалить из STEPS
Проверить app/orchestrator/advance_runner.py — какой шаг вызывается для ProjectStatus.generating_videos
Приоритет: P1 (блокирует генерацию видео)

. Nano Banana Pro ban: жёсткая проверка без graceful fallback
Где искать:
app/bots/outsee_http.py:85-95 (функция assert_not_nano_banana_pro_on_outsee)
app/services/media_route.py:45-55 (функция is_nano_banana_pro)
Проблема:
def assert_not_nano_banana_pro_on_outsee(slug: str | None) -> None:
    if is_nano_banana_pro(slug):
        raise NanoBananaProOutseeBannedError(
            "Nano Banana Pro запрещена на Outsee — нельзя генерировать этой моделью",
            context={"model": slug, "provider": "outsee", "banned": True},
        )

Риск:
Если пользователь случайно выберет Nano Banana Pro в Studio UI, генерация мгновенно упадёт без предложения альтернативы
Нет automatic fallback на gpt-image-2 или другую доступную модель
Пользователь увидит только ошибку, не поймёт что делать
Парето-фикс (5 минут):
# app/bots/outsee_http.py — ИЗМЕНИТЬ:
def assert_not_nano_banana_pro_on_outsee(slug: str | None) -> str:
    """Вернуть fallback-модель если запрещена."""
    if is_nano_banana_pro(slug):
        fallback = "gpt-image-2"
        logger.warning(
            "outsee_api: Nano Banana Pro запрещена, fallback на {} (исходная: {})",
            fallback, slug
        )
        return fallback
    return slug

# И использовать: model = assert_not_nano_banana_pro_on_outsee(model_slug)
Приоритет: P2 (UX-проблема, не блокирует, но раздражает)
🟡 Зоны внимания (не критично, но стоит улучшить)
4. ElevenLabs TTS: нет обработки rate limits
Где искать:
app/services/elevenlabs_tts.py (новый файл)
Проблема:
Новая интеграция с ElevenLabs REST API. Нужно проверить:
Есть ли retry-логика для 429 Too Many Requests?
Обрабатываются ли квоты (characters per month)?
Есть ли fallback на Whisper/Faster-Whisper при сбое ElevenLabs?
Парето-фикс: Проверить файл, добавить retry для 429 с exponential backoff.
Приоритет: P3
5. Server-side download conversion: нет валидации размера файла
Где искать:
app/web/routers/projects.py:download_project_artifacts (новый эндпоинт)
Проблема:
Новая фича server-side PNG/JPG/WEBP conversion при скачивании. Нужно проверить:
Есть ли лимит на размер файла (чтобы не OOM)?
Обрабатываются ли corrupt файлы?
Есть ли таймаут на conversion?
Парето-фикс: Добавить проверки размера и таймауты.
Приоритет: P3
6. AI Prompt Enhancer: нет валидации промптов от LLM
Где искать:
app/services/prompt_enhancer.py (новый файл)
Проблема:
Новая фича: 50 random prompt masterpieces + AI enhancement. Нужно проверить:
Валидируется ли output от LLM (чтобы не сломать pipeline)?
Есть ли fallback на дефолтные промпты при сбое?
Логируются ли плохие промпты?
Парето-фикс: Добавить валидацию и fallback.
Приоритет: P3