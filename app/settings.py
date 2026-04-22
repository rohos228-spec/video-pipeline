from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_owner_chat_id: int = Field(..., alias="TELEGRAM_OWNER_CHAT_ID")

    # Database
    postgres_user: str = Field("video", alias="POSTGRES_USER")
    postgres_password: str = Field("video", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field("video", alias="POSTGRES_DB")
    postgres_host: str = Field("postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(5432, alias="POSTGRES_PORT")

    # Browser
    browser_cdp_url: str = Field("http://host.docker.internal:29229", alias="BROWSER_CDP_URL")

    # Service URLs
    outsee_image_url: str = Field("https://outsee.io/image?model=nano-banana-2", alias="OUTSEE_IMAGE_URL")
    outsee_video_url: str = Field("https://outsee.io/video?model=veo-3-fast", alias="OUTSEE_VIDEO_URL")
    elevenlabs_web_url: str = Field(
        "https://elevenlabs.io/app/speech-synthesis", alias="ELEVENLABS_WEB_URL"
    )

    # MoreLogin / социалки
    morelogin_profile_id: str | None = Field(None, alias="MORELOGIN_PROFILE_ID")
    social_publish_enabled: bool = Field(False, alias="SOCIAL_PUBLISH_ENABLED")

    # Paths
    data_dir: Path = Field(Path("/data"), alias="DATA_DIR")

    # Logic
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    hitl_auto_approve: bool = Field(False, alias="HITL_AUTO_APPROVE")

    @property
    def db_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()  # type: ignore[call-arg]
