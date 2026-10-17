from datetime import date, datetime
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True
    )

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr = SecretStr("")
    model_name: str = ""
    reasoning_effort: str | None = None
    model_timeout_seconds: float = 30
    max_iterations: int = 8
    demo_user_id: str = "demo-manager"
    demo_now: datetime | None = datetime(2026, 10, 14, 10)
    data_dir: Path = DATA_DIR


class Clock:
    def __init__(self, fixed: datetime | None = None):
        self.fixed = fixed

    def now(self) -> datetime:
        return self.fixed or datetime.now()

    def today(self) -> date:
        return self.now().date()
