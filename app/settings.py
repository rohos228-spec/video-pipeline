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

    # MoreLogin / социалки
    morelogin_profile_id: str | None = Field(None, alias="MORELOGIN_PROFILE_ID")
    social_publish_enabled: bool = Field(False, alias="SOCIAL_PUBLISH_ENABLED")

    # Paths
    data_dir: Path = Field(Path("./data"), alias="DATA_DIR")
    # Явный путь к .xlsx-шаблону для новых project.xlsx (иначе — newest v8 в templates/)
    project_xlsx_template: Path | None = Field(None, alias="PROJECT_XLSX_TEMPLATE")

    # Whisper — large-v3 точнее по словам; первый прогон дольше
    whisper_model: str = Field("large-v3", alias="WHISPER_MODEL")

    # Background music — auto if bgm.mp3 / music.mp3 found in project folder
    bgm_default_enabled: bool = Field(True, alias="BGM_DEFAULT_ENABLED")
    bgm_default_level: int = Field(35, alias="BGM_DEFAULT_LEVEL")  # 0..100
    bgm_path: Path | None = Field(None, alias="BGM_PATH")

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

    @model_validator(mode="after")
    def _resolve_paths_from_repo_root(self) -> "Settings":
        object.__setattr__(self, "sqlite_path", resolve_project_path(self.sqlite_path))
        object.__setattr__(self, "data_dir", resolve_project_path(self.data_dir))
        if self.bgm_path is not None:
            object.__setattr__(self, "bgm_path", resolve_project_path(self.bgm_path))
        return self

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
