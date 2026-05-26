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

    # Browser — Chrome с --remote-debugging-port=29229 запущен локально на том же ПК
    browser_cdp_url: str = Field("http://localhost:29229", alias="BROWSER_CDP_URL")

    # Service URLs
    outsee_image_url: str = Field(
        "https://outsee.io/image?model=nano-banana-2", alias="OUTSEE_IMAGE_URL"
    )
    outsee_video_url: str = Field(
        "https://outsee.io/video?model=veo-3-fast", alias="OUTSEE_VIDEO_URL"
    )
    elevenlabs_web_url: str = Field(
        "https://elevenlabs.io/app/speech-synthesis", alias="ELEVENLABS_WEB_URL"
    )

    # MoreLogin / социалки
    morelogin_profile_id: str | None = Field(None, alias="MORELOGIN_PROFILE_ID")
    social_publish_enabled: bool = Field(False, alias="SOCIAL_PUBLISH_ENABLED")

    # Paths
    data_dir: Path = Field(Path("./data"), alias="DATA_DIR")

    # Whisper
    whisper_model: str = Field("medium", alias="WHISPER_MODEL")

    # Сборка (шаг 11)
    assembly_clip_audio_db: float = Field(-22.0, alias="ASSEMBLY_CLIP_AUDIO_DB")
    assembly_bgm_db: float = Field(-17.0, alias="ASSEMBLY_BGM_DB")
    assembly_max_stretch_ratio: float = Field(0.15, alias="ASSEMBLY_MAX_STRETCH_RATIO")
    assembly_audio_fade_out_sec: float = Field(3.0, alias="ASSEMBLY_AUDIO_FADE_OUT_SEC")

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
