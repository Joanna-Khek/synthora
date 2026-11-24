from typing import Literal
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "configs" / "config.yaml"


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    max_tokens: int = Field(gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"]


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    album_wait_seconds: float = Field(gt=0)
    preview_chars: int = Field(gt=0)
    max_reply_chars: int = Field(gt=0, le=4096)


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: float = Field(gt=0)


class StorageConfig(BaseModel):
    db_path: Path


class TracingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    tracking_uri: str
    experiment: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        yaml_file=CONFIG_FILE,
        yaml_file_encoding="utf-8",
        env_nested_delimiter="__",  # LLM__MODEL=... overrides llm.model
        extra="ignore",  # environment variable is case insensitive
        hide_input_in_errors=True,  # If ValidationErrors occur, secrets is not printed out
    )

    # From .env
    telegram_token: SecretStr
    llm_api_key: SecretStr
    google_api_key: SecretStr
    owner_telegram_id: int
    github_token: SecretStr | None = (
        None  # without token is 60req/hr. with token is 5000req/hr
    )

    # From .yaml
    llm: LLMConfig
    bot: BotConfig
    extraction: ExtractionConfig
    storage: StorageConfig
    tracing: TracingConfig

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win: arguments > environment variables > .env > config.yaml
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
