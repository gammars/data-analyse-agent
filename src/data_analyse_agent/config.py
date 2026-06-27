import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


class Settings(BaseModel):
    """Application settings loaded from environment variables and .env."""

    app_env: str = "development"
    log_level: str = "INFO"
    data_dir: Path = Path("data")


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
    )
