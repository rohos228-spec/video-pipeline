from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_owner_chat_id: int = Field(..., alias="TELEGRAM_OWNER_CHAT_ID")

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

    # Logic
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    hitl_auto_approve: bool = Field(False, alias="HITL_AUTO_APPROVE")

    @property
    def db_url(self) -> str:
        # SQLite path → sqlite+aiosqlite:///absolute_or_relative/path
        p = self.sqlite_path
        if not p.is_absolute():
            # относительный путь — относительно CWD
            p = Path.cwd() / p
        # Для Windows путь начинается с буквы диска: sqlite+aiosqlite:///C:/Users/...
        as_posix = p.as_posix()
        return f"sqlite+aiosqlite:///{as_posix}"


settings = Settings()  # type: ignore[call-arg]
