from pathlib import Path

from pydantic import Field, field_validator, model_validator
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
        20_000, alias="BROWSER_CDP_CONNECT_TIMEOUT_MS"
    )
    # При зависании connect_over_cdp после ws connected — перезапуск Chrome (Win)
    browser_cdp_auto_recover: bool = Field(True, alias="BROWSER_CDP_AUTO_RECOVER")

    # Service URLs
    outsee_image_url: str = Field(
        "https://outsee.io/image?model=nano-banana-2", alias="OUTSEE_IMAGE_URL"
    )
    outsee_video_url: str = Field(
        "https://outsee.io/video?model=veo-3-fast", alias="OUTSEE_VIDEO_URL"
    )
    # True = вариант A (image+video): глобальная очередь Outsee, одна новая
    # картинка/ролик после Generate, без перебора галереи по [ID: …].
    outsee_queue_mode: bool = Field(True, alias="OUTSEE_QUEUE_MODE")
    elevenlabs_web_url: str = Field(
        "https://elevenlabs.io/app/speech-synthesis", alias="ELEVENLABS_WEB_URL"
    )
    # REST API (предпочтительно — без Chrome/CDP и гео-банов UI)
    elevenlabs_api_key: str = Field("", alias="ELEVENLABS_API_KEY")
    elevenlabs_api_model: str = Field(
        "eleven_multilingual_v2", alias="ELEVENLABS_API_MODEL"
    )
    # Опционально: http/socks5 прокси только для API (EU/US), если ключ режут по IP
    elevenlabs_proxy_url: str | None = Field(None, alias="ELEVENLABS_PROXY_URL")
    # Второй URL — когда у провайдера другой порт/протокол (переключение вручную в Lab)
    elevenlabs_proxy_alt_url: str | None = Field(None, alias="ELEVENLABS_PROXY_ALT_URL")
    # Отдельный HTTP proxy для upload клона (SOCKS часто режет multipart на Windows)
    elevenlabs_upload_proxy_url: str | None = Field(None, alias="ELEVENLABS_UPLOAD_PROXY_URL")

    # MoreLogin / социалки
    morelogin_profile_id: str | None = Field(None, alias="MORELOGIN_PROFILE_ID")
    social_publish_enabled: bool = Field(False, alias="SOCIAL_PUBLISH_ENABLED")

    # Paths
    data_dir: Path = Field(Path("./data"), alias="DATA_DIR")
    # Явный путь к .xlsx-шаблону для новых project.xlsx (иначе — newest v8 в templates/)
    project_xlsx_template: Path | None = Field(None, alias="PROJECT_XLSX_TEMPLATE")

    # ASR — на ПК монтажа: nvidia (NeMo Parakeet) или whisper fallback
    asr_backend: str = Field("nvidia", alias="ASR_BACKEND")
    whisper_model: str = Field("large-v3", alias="WHISPER_MODEL")
    whisper_device: str = Field("cuda", alias="WHISPER_DEVICE")
    whisper_compute_type: str = Field("float16", alias="WHISPER_COMPUTE_TYPE")
    nvidia_asr_model: str = Field(
        "nvidia/stt_ru_fastconformer_hybrid_large_pc",
        alias="NVIDIA_ASR_MODEL",
    )
    # Без файла в audio/ — ошибка, а не 11Labs (импорт озвучки с диска)
    audio_use_elevenlabs_fallback: bool = Field(
        False, alias="AUDIO_USE_ELEVENLABS_FALLBACK"
    )

    # Fleet — сеть рабочих станций (hub = этот ПК, agent = удалённый)
    fleet_enabled: bool = Field(True, alias="FLEET_ENABLED")
    fleet_role: str = Field("hub", alias="FLEET_ROLE")
    fleet_hub_url: str = Field("http://127.0.0.1:8765", alias="FLEET_HUB_URL")
    fleet_agent_token: str = Field("", alias="FLEET_AGENT_TOKEN")
    fleet_node_name: str = Field("", alias="FLEET_NODE_NAME")
    fleet_is_main: bool = Field(True, alias="FLEET_IS_MAIN")
    fleet_montage_hub: bool = Field(True, alias="FLEET_MONTAGE_HUB")
    fleet_hub_is_worker: bool = Field(True, alias="FLEET_HUB_IS_WORKER")
    fleet_auto_pull: bool = Field(False, alias="FLEET_AUTO_PULL")
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
    subtitle_max_words: int = Field(1, alias="SUBTITLE_MAX_WORDS")
    subtitle_lead_seconds: float = Field(0.18, alias="SUBTITLE_LEAD_SECONDS")
    subtitle_chars_per_second: float = Field(14.0, alias="SUBTITLE_CHARS_PER_SECOND")
    subtitle_rewhisper_on_assemble: bool = Field(True, alias="SUBTITLE_REWHISPER_ON_ASSEMBLE")

    # Logic
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    hitl_auto_approve: bool = Field(False, alias="HITL_AUTO_APPROVE")

    # Web UI (локальный FastAPI + Next.js)
    web_enabled: bool = Field(True, alias="WEB_ENABLED")
    web_host: str = Field("127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(8765, alias="WEB_PORT")

    @field_validator(
        "telegram_enabled",
        "browser_cdp_auto_recover",
        "outsee_queue_mode",
        "social_publish_enabled",
        "audio_use_elevenlabs_fallback",
        "fleet_enabled",
        "fleet_is_main",
        "fleet_montage_hub",
        "fleet_hub_is_worker",
        "fleet_auto_pull",
        "bgm_default_enabled",
        "subtitle_rewhisper_on_assemble",
        "hitl_auto_approve",
        "web_enabled",
        mode="before",
    )
    @classmethod
    def _coerce_bool(cls, v: object) -> object:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "1", "yes", "on"):
                return True
            if s in ("false", "0", "no", "off", ""):
                return False
        return v

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
