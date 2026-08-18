from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.project_root import find_project_root, resolve_project_path

_ROOT = find_project_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        extra="ignore",
    )

    # Telegram (опционально — пустой токен = web-only, без бота)
    telegram_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    telegram_owner_chat_id: int = Field(279887118, alias="TELEGRAM_OWNER_CHAT_ID")
    # false / 0 — не поднимать бота даже если токен задан
    telegram_enabled: bool = Field(False, alias="TELEGRAM_ENABLED")
    # Опциональный HTTP/SOCKS5 прокси для Telegram-API.
    # Примеры: http://user:pass@host:port, socks5://user:pass@host:port
    telegram_proxy_url: str | None = Field(None, alias="TELEGRAM_PROXY_URL")

    # Database — SQLite file + aiosqlite.
    # Пути в Windows: C:\Users\<user>\vp_state.db → пишется как C:/Users/<user>/vp_state.db
    sqlite_path: Path = Field(Path("./data/state.db"), alias="SQLITE_PATH")

    # Browser — только Chrome из Start-Chrome.cmd (профиль .vp_browser_data, :29229)
    browser_cdp_url: str = Field("http://127.0.0.1:29229", alias="BROWSER_CDP_URL")
    browser_cdp_connect_timeout_ms: int = Field(
        45_000, alias="BROWSER_CDP_CONNECT_TIMEOUT_MS"
    )
    # При зависании connect_over_cdp после ws connected — перезапуск Chrome (Win)
    browser_cdp_auto_recover: bool = Field(True, alias="BROWSER_CDP_AUTO_RECOVER")

    # Service URLs
    outsee_image_url: str = Field(
        "https://outsee.io/image?model=gpt-image-2", alias="OUTSEE_IMAGE_URL"
    )
    outsee_video_url: str = Field(
        "https://outsee.io/video?model=veo-3-fast", alias="OUTSEE_VIDEO_URL"
    )
    # True = вариант A (image+video): глобальная очередь Outsee, одна новая
    # картинка/ролик после Generate, без перебора галереи по [ID: …].
    outsee_queue_mode: bool = Field(True, alias="OUTSEE_QUEUE_MODE")
    # Таймаут скачивания результата outsee (сек) — URL и клик «Скачать».
    outsee_download_timeout_s: float = Field(120.0, alias="OUTSEE_DOWNLOAD_TIMEOUT_S")
    # Developer API key (https://outsee.io/profile) — НЕ cookies / НЕ Grsai
    outsee_api_key: str = Field("", alias="OUTSEE_API_KEY")
    outsee_api_base_url: str = Field("https://outsee.io", alias="OUTSEE_API_BASE_URL")
    outsee_default_image_model: str = Field(
        "gpt-image-2", alias="OUTSEE_DEFAULT_IMAGE_MODEL"
    )
    outsee_default_video_model: str = Field(
        "veo-3-1-lite", alias="OUTSEE_DEFAULT_VIDEO_MODEL"
    )
    # при сбое Bearer API — откат на Playwright UI (нужен Chrome CDP)
    outsee_http_fallback_cdp: bool = Field(True, alias="OUTSEE_HTTP_FALLBACK_CDP")
    # legacy alias (cookie-era); ignored if OUTSEE_API_KEY set
    outsee_http_api: bool = Field(True, alias="OUTSEE_HTTP_API")
    # Create: параллель по провайдерам (остальные ждут status=queued).
    # CREATE_MAX_PARALLEL — legacy fallback, если provider-specific не задан.
    create_max_parallel: int = Field(5, alias="CREATE_MAX_PARALLEL")
    # Outsee API concurrency_limit=4 — не ставить выше 4.
    create_max_parallel_outsee: int = Field(4, alias="CREATE_MAX_PARALLEL_OUTSEE")
    create_max_parallel_grsai: int = Field(10, alias="CREATE_MAX_PARALLEL_GRSAI")
    # Пайплайн img: параллельные кадры 0..4 (0=не генерить; дефолт для проектов).
    img_max_streams: int = Field(2, alias="IMG_MAX_STREAMS")
    # Vision checkMode: параллельные GPT-батчи 0..10 (каждый батч ≤8 PNG).
    check_max_streams: int = Field(2, alias="CHECK_MAX_STREAMS")

    # Grsai API (https://grsai.com / https://grsaiapi.com) — image/video без CDP
    grsai_api_key: str = Field("", alias="GRSAI_API_KEY")
    grsai_base_url: str = Field("https://grsaiapi.com", alias="GRSAI_BASE_URL")
    # outsee | grsai — кто рисует img/hero/items
    image_provider: str = Field("grsai", alias="IMAGE_PROVIDER")
    # outsee | grsai — кто генерит video в Create / (опц.) пайплайн
    video_provider: str = Field("grsai", alias="VIDEO_PROVIDER")
    grsai_default_image_model: str = Field("gpt-image-2", alias="GRSAI_DEFAULT_IMAGE_MODEL")
    grsai_default_video_model: str = Field("sora-2", alias="GRSAI_DEFAULT_VIDEO_MODEL")

    # Текстовый LLM: GPT (kie) по умолчанию. Kimi K3 (TokenRouter) — доп. модель.
    # Переключение: Studio UI / data/text_llm_choice.json / TEXT_LLM_PROVIDER.
    # TEXT_LLM_PROVIDER=kie|tokenrouter|kimi — default kie (GPT не убирается).
    text_llm_provider: str = Field("kie", alias="TEXT_LLM_PROVIDER")
    tokenrouter_api_key: str = Field("", alias="TOKENROUTER_API_KEY")
    tokenrouter_base_url: str = Field(
        "https://api.tokenrouter.com/v1", alias="TOKENROUTER_BASE_URL"
    )
    tokenrouter_model: str = Field(
        "moonshotai/kimi-k3-free", alias="TOKENROUTER_MODEL"
    )

    # vibecode.moe — OpenAI-совместимый chat/completions (GPT 5.5 / 5.6 Sol).
    vibecode_api_key: str = Field("", alias="VIBECODE_API_KEY")
    vibecode_base_url: str = Field(
        "https://vibecode.moe/v1", alias="VIBECODE_BASE_URL"
    )

    # GPT / kie.ai — основной текстовый стек (не удалять при добавлении Kimi)
    gpt_api_key: str = Field("", alias="GPT_API_KEY")
    gpt_base_url: str = Field("", alias="GPT_BASE_URL")
    gpt_model: str = Field("gpt-5-6-sol", alias="GPT_MODEL")
    # Kie Market (Kling 2.6 video fallback). Пусто → GPT_API_KEY + api.kie.ai.
    kie_api_key: str = Field("", alias="KIE_API_KEY")
    kie_api_base_url: str = Field("https://api.kie.ai", alias="KIE_API_BASE_URL")
    # Yandex Object Storage — публичный хост реф-кадров для Outsee/Kling.
    yandex_storage_bucket: str = Field("", alias="YANDEX_STORAGE_BUCKET")
    yandex_storage_access_key: str = Field("", alias="YANDEX_STORAGE_ACCESS_KEY")
    yandex_storage_secret_key: str = Field("", alias="YANDEX_STORAGE_SECRET_KEY")
    yandex_storage_endpoint: str = Field(
        "https://storage.yandexcloud.net", alias="YANDEX_STORAGE_ENDPOINT"
    )
    yandex_storage_region: str = Field("ru-central1", alias="YANDEX_STORAGE_REGION")
    # Шаблон пути chat-эндпоинта. grsai/OpenAI: /v1/chat/completions;
    # kie.ai: путь зависит от модели → /{model}/v1/chat/completions.
    # Плейсхолдер {model} подставляется слагом модели.
    # TokenRouter: /chat/completions (база уже с /v1).
    gpt_chat_path: str = Field("/codex/v1/responses", alias="GPT_CHAT_PATH")
    # Формат API: chat (messages/choices) | responses (input/output) | auto.
    # auto → responses, если в пути есть "responses" (kie.ai gpt-5.6/5.5/5.4 codex).
    gpt_api_mode: str = Field("auto", alias="GPT_API_MODE")
    gpt_timeout_s: float = Field(600.0, alias="GPT_TIMEOUT_S")
    gpt_max_retries: int = Field(4, alias="GPT_MAX_RETRIES")
    # 0 = ждать EOF SSE (не рвать по GPT_TIMEOUT). >0 = httpx read timeout.
    gpt_stream_read_timeout_s: float = Field(0.0, alias="GPT_STREAM_READ_TIMEOUT_S")
    # HTTP/SOCKS5 для GPT/kie. Пусто = напрямую. Не путать с TELEGRAM_PROXY_URL.
    gpt_proxy_url: str | None = Field(None, alias="GPT_PROXY_URL")
    # Свой VPS-relay (deploy/gpt-relay): GPT_BASE_URL=https://gpt.example.com
    # + этот токен → заголовок X-VP-Relay-Token. Прокси на ПК не нужен.
    gpt_relay_token: str = Field("", alias="GPT_RELAY_TOKEN")

    def resolved_text_llm_provider(self) -> str:
        """Активный текстовый провайдер: kie | vibecode | tokenrouter.

        Default — kie. vibecode/Kimi только по явному выбору (UI / choice.json / env).
        """
        from app.services.text_llm_catalog import resolve_active_provider

        return resolve_active_provider(self)

    @property
    def text_llm_is_tokenrouter(self) -> bool:
        return self.resolved_text_llm_provider() == "tokenrouter"

    @property
    def text_llm_is_vibecode(self) -> bool:
        return self.resolved_text_llm_provider() == "vibecode"

    @property
    def text_llm_label(self) -> str:
        """Человекочитаемая метка для UI/логов (не «GPT», если это Kimi)."""
        if self.text_llm_is_tokenrouter:
            model = (self.tokenrouter_model or "moonshotai/kimi-k3-free").strip()
            short = model.split("/")[-1] if "/" in model else model
            return f"Kimi K3 · TokenRouter ({short})"
        if self.text_llm_is_vibecode:
            from app.services.text_llm_catalog import (
                catalog_item,
                resolve_active_model_id,
            )

            item = catalog_item(resolve_active_model_id(self))
            api_model = (item or {}).get("api_model") or "gpt-5.6-sol"
            pretty = (item or {}).get("label") or "GPT"
            return f"{pretty} · vibecode.moe ({api_model})"
        model = (self.gpt_model or "gpt").strip()
        base = (self.gpt_base_url or "").strip().lower()
        host = "kie.ai" if "kie.ai" in base else ("grsai" if "grsai" in base else "API")
        return f"GPT · {host} ({model})"

    @property
    def gpt_api_effective_key(self) -> str:
        """Ключ активного текстового LLM."""
        if self.text_llm_is_tokenrouter:
            return (
                (self.tokenrouter_api_key or "").strip()
                or (self.gpt_api_key or "").strip()
            )
        if self.text_llm_is_vibecode:
            return (self.vibecode_api_key or "").strip()
        return (self.gpt_api_key or "").strip() or (self.grsai_api_key or "").strip()

    @property
    def gpt_api_effective_base_url(self) -> str:
        """База активного текстового LLM."""
        if self.text_llm_is_tokenrouter:
            base = (self.tokenrouter_base_url or "https://api.tokenrouter.com/v1").strip()
            return base.rstrip("/")
        if self.text_llm_is_vibecode:
            relay = (self.gpt_relay_token or "").strip()
            gbase = (self.gpt_base_url or "").strip().rstrip("/")
            low = gbase.lower()
            if relay and gbase and "kie.ai" not in low and "vibecode.moe" not in low:
                # VPS gpt-relay: Studio → https://gpt.example.com/v1/chat/completions
                return gbase
            return (self.vibecode_base_url or "https://vibecode.moe/v1").strip().rstrip("/")
        base = (self.gpt_base_url or "").strip() or (self.grsai_base_url or "").strip()
        return base.rstrip("/")

    @property
    def gpt_model_effective(self) -> str:
        if self.text_llm_is_tokenrouter:
            return (self.tokenrouter_model or "moonshotai/kimi-k3-free").strip()
        if self.text_llm_is_vibecode:
            from app.services.text_llm_catalog import (
                catalog_api_model,
                resolve_active_model_id,
            )

            return catalog_api_model(resolve_active_model_id(self))
        return (self.gpt_model or "gpt-5-6-sol").strip()

    @property
    def gpt_chat_path_effective(self) -> str:
        """Путь chat-эндпоинта для активного провайдера."""
        if self.text_llm_is_tokenrouter:
            # base уже …/v1 → финальный URL …/v1/chat/completions
            return "/chat/completions"
        if self.text_llm_is_vibecode:
            base = self.gpt_api_effective_base_url.lower()
            if base.endswith("/v1"):
                return "/chat/completions"
            return "/v1/chat/completions"
        return (self.gpt_chat_path or "/v1/chat/completions").strip()

    @property
    def gpt_api_mode_effective(self) -> str:
        if self.text_llm_is_tokenrouter or self.text_llm_is_vibecode:
            return "chat"
        return (self.gpt_api_mode or "auto").strip().lower() or "auto"

    @property
    def gpt_api_enabled(self) -> bool:
        """API-транспорт текста доступен только при наличии ключа и базы."""
        return bool(self.gpt_api_effective_key and self.gpt_api_effective_base_url)

    elevenlabs_web_url: str = Field(
        "https://elevenlabs.io/app/speech-synthesis", alias="ELEVENLABS_WEB_URL"
    )
    # Опциональный API-ключ 11Labs — SFX-генерация звуков сопровождения
    # (POST /v1/sound-effects). Без ключа — локальный синтез (wave, офлайн).
    elevenlabs_api_key: str = Field("", alias="ELEVENLABS_API_KEY")
    # Звуки сопровождения в пайплайне (sfx_plan → sfx_gen → микс в сборке).
    sfx_enabled: bool = Field(True, alias="SFX_ENABLED")

    # MoreLogin / социалки
    morelogin_profile_id: str | None = Field(None, alias="MORELOGIN_PROFILE_ID")
    social_publish_enabled: bool = Field(False, alias="SOCIAL_PUBLISH_ENABLED")

    # Paths
    data_dir: Path = Field(Path("./data"), alias="DATA_DIR")
    # Центральный harness-гейт перед продвижением *_ready статусов (auto_advance).
    # True — выключить гейт (только диагностика, без блокировки продвижения).
    harness_gate_disabled: bool = Field(False, alias="HARNESS_GATE_DISABLED")
    # Явный путь к .xlsx-шаблону для новых project.xlsx (иначе — newest v8 в templates/)
    project_xlsx_template: Path | None = Field(None, alias="PROJECT_XLSX_TEMPLATE")

    # ASR — на ПК монтажа: nvidia (NeMo Parakeet) или whisper fallback
    asr_backend: str = Field("nvidia", alias="ASR_BACKEND")
    whisper_model: str = Field("large-v3", alias="WHISPER_MODEL")
    whisper_device: str = Field("cuda", alias="WHISPER_DEVICE")
    whisper_compute_type: str = Field("float16", alias="WHISPER_COMPUTE_TYPE")
    nvidia_asr_model: str = Field(
        "nvidia/parakeet-tdt-0.6b-v3", alias="NVIDIA_ASR_MODEL"
    )
    # Parakeet ~4–8 ГБ RAM/VRAM — не грузить при старте Studio (lazy при шаге «Аудио»)
    nvidia_asr_preload_on_startup: bool = Field(
        False, alias="NVIDIA_ASR_PRELOAD_ON_STARTUP"
    )
    # Без файла в audio/ — ошибка, а не 11Labs (импорт озвучки с диска)
    audio_use_elevenlabs_fallback: bool = Field(
        False, alias="AUDIO_USE_ELEVENLABS_FALLBACK"
    )

    # Ветка для commit/push оркестратора с этого ПК (housepc|tompc|strangepc|workpc).
    # Пусто = main. На каждом ПК своё значение в локальном .env.
    orchestrator_git_branch: str = Field("main", alias="ORCHESTRATOR_GIT_BRANCH")

    # Fleet — сеть рабочих станций (hub = этот ПК, agent = удалённый)
    fleet_enabled: bool = Field(True, alias="FLEET_ENABLED")
    fleet_role: str = Field("hub", alias="FLEET_ROLE")
    fleet_hub_url: str = Field("http://127.0.0.1:8765", alias="FLEET_HUB_URL")
    fleet_agent_token: str = Field("", alias="FLEET_AGENT_TOKEN")
    fleet_node_name: str = Field("", alias="FLEET_NODE_NAME")
    fleet_is_main: bool = Field(True, alias="FLEET_IS_MAIN")
    fleet_montage_hub: bool = Field(True, alias="FLEET_MONTAGE_HUB")
    fleet_hub_is_worker: bool = Field(True, alias="FLEET_HUB_IS_WORKER")
    fleet_auto_pull: bool = Field(True, alias="FLEET_AUTO_PULL")
    fleet_montage_max_parallel: int = Field(1, alias="FLEET_MONTAGE_MAX_PARALLEL")
    # Tailscale URL этого ПК для agents (например http://100.x.x.x:8765)
    fleet_public_url: str = Field("", alias="FLEET_PUBLIC_URL")

    # Web auth (fleet + удалённое управление)
    web_auth_user: str = Field("", alias="WEB_AUTH_USER")
    web_auth_password: str = Field("", alias="WEB_AUTH_PASSWORD")

    # Background music — auto if bgm.mp3 / music.mp3 found in project folder
    bgm_default_enabled: bool = Field(True, alias="BGM_DEFAULT_ENABLED")
    bgm_default_level: int = Field(35, alias="BGM_DEFAULT_LEVEL")  # 0..100
    bgm_path: Path | None = Field(None, alias="BGM_PATH")
    assembly_voice_gain: float = Field(1.0, alias="ASSEMBLY_VOICE_GAIN")
    assembly_bgm_mix_ratio: float = Field(0.35, alias="ASSEMBLY_BGM_MIX_RATIO")

    # Subtitles — одно слово; опережение озвучки (Whisper системно отстаёт ~0.2–0.3 с)
    # 2 = два слова за прогон, каждое на своей строке (ASS \\N).
    subtitle_max_words: int = Field(2, alias="SUBTITLE_MAX_WORDS")
    subtitle_lead_seconds: float = Field(0.18, alias="SUBTITLE_LEAD_SECONDS")
    subtitle_chars_per_second: float = Field(14.0, alias="SUBTITLE_CHARS_PER_SECOND")
    subtitle_rewhisper_on_assemble: bool = Field(
        False, alias="SUBTITLE_REWHISPER_ON_ASSEMBLE"
    )

    # Параллельная генерация: сколько проектов очереди могут выполняться
    # одновременно (top-N окно gen_queue + конкуррентный воркер). 1 = как раньше
    # (строго по одному). Chrome больше нет — можно >1 безопасно.
    worker_max_parallel: int = Field(1, alias="WORKER_MAX_PARALLEL")

    # Мульти-агентный дизайн сцен (нода scene_design между split и hero).
    # False + нет meta.scene_design_enabled → нода pass-through без GPT.
    scene_design_enabled: bool = Field(False, alias="SCENE_DESIGN_ENABLED")
    # Сколько категорийных агентов дёргают GPT одновременно внутри ноды.
    scene_design_max_parallel: int = Field(5, alias="SCENE_DESIGN_MAX_PARALLEL")
    # Сборка: сколько Frame-строк пайплайна в одном GPT-запросе (чанк).
    # 0 / 1 = один запрос на весь ролик (старое поведение, легко ловит 524).
    scene_design_assemble_chunk_frames: int = Field(
        10, alias="SCENE_DESIGN_ASSEMBLE_CHUNK_FRAMES"
    )
    # action/camera: сразу режем на куски ≤N кадров (не жечь 5 мин на 524).
    # 0 = старое: сначала полный запрос, дробим только после 524.
    scene_design_agent_chunk_frames: int = Field(
        10, alias="SCENE_DESIGN_AGENT_CHUNK_FRAMES"
    )
    # Сколько кусков action/camera одновременно (kie).
    scene_design_agent_chunk_parallel: int = Field(
        3, alias="SCENE_DESIGN_AGENT_CHUNK_PARALLEL"
    )
    # Один GPT-вызов action/camera: abort раньше Cloudflare ~300s → сразу /2.
    # V9 плотнее — 240с часто режет живые куски; 280 даёт запас до CF.
    scene_design_agent_attempt_timeout_s: float = Field(
        280.0, alias="SCENE_DESIGN_AGENT_ATTEMPT_TIMEOUT_S"
    )
    # Волна 0: черновик→редактор скелета (sd_skeleton / sd_skeleton_editor).
    # Per-project: meta.scene_design_skeleton + нода marker=skeleton на канвасе.
    scene_design_skeleton_enabled: bool = Field(
        True, alias="SCENE_DESIGN_SKELETON_ENABLED"
    )
    # Тайминг скелета: len(закадр)/RATE vs сумма время_сек кадров (допуск 15%).
    scene_design_vo_chars_per_sec: float = Field(
        14.0, alias="SCENE_DESIGN_VO_CHARS_PER_SEC"
    )

    # Logic
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    hitl_auto_approve: bool = Field(False, alias="HITL_AUTO_APPROVE")
    # True — прямая запись NodeRun.status мимо машины состояний → RuntimeError (dev/tests)
    node_status_strict: bool = Field(False, alias="NODE_STATUS_STRICT")

    # Web UI (локальный FastAPI + Next.js)
    web_enabled: bool = Field(True, alias="WEB_ENABLED")
    web_host: str = Field("127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(8765, alias="WEB_PORT")

    @model_validator(mode="after")
    def _resolve_paths_from_repo_root(self) -> "Settings":
        object.__setattr__(self, "sqlite_path", resolve_project_path(self.sqlite_path))
        object.__setattr__(self, "data_dir", resolve_project_path(self.data_dir))
        if self.bgm_path is not None:
            object.__setattr__(self, "bgm_path", resolve_project_path(self.bgm_path))
        return self

    @property
    def fleet_local_web_url(self) -> str:
        """Локальный URL API (heartbeat hub+worker на этом же ПК)."""
        host = self.web_host
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{self.web_port}"

    @property
    def fleet_agent_base_url(self) -> str:
        """URL, который agent сообщает hub (Tailscale или локальный)."""
        if self.fleet_public_url.strip():
            return self.fleet_public_url.strip().rstrip("/")
        return self.fleet_local_web_url

    @property
    def fleet_heartbeat_hub_url(self) -> str:
        """Куда слать heartbeat: hub на этой машине → localhost, иначе FLEET_HUB_URL."""
        role = (self.fleet_role or "hub").strip().lower()
        if role == "hub":
            return self.fleet_local_web_url
        hub = (self.fleet_hub_url or "").strip().rstrip("/")
        return hub or self.fleet_local_web_url

    @property
    def web_auth_enabled(self) -> bool:
        return bool(self.web_auth_user.strip() and self.web_auth_password)

    @property
    def telegram_active(self) -> bool:
        """Нужен ли живой Telegram-бот (поллинг + уведомления)."""
        if not self.telegram_enabled:
            return False
        return bool((self.telegram_bot_token or "").strip())

    @property
    def db_url(self) -> str:
        p = self.sqlite_path
        as_posix = p.as_posix()
        return f"sqlite+aiosqlite:///{as_posix}"


settings = Settings()  # type: ignore[call-arg]
