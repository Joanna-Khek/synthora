from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # environment variable is case insensitive
        hide_input_in_errors=True,  # If ValidationErrors occur, secrets is not printed out
    )

    telegram_token: SecretStr
    llm_api_key: SecretStr
    google_api_key: SecretStr
    owner_telegram_id: int
    github_token: SecretStr | None = (
        None  # without token is 60req/hr. with token is 5000req/hr
    )
