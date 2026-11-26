from zoneinfo import ZoneInfo
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


class CaptureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_linked: int = Field(gt=0)
    max_concurrent: int = Field(gt=0)
    max_source_chars: int = Field(gt=0)


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


class PullApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str  # where the laptop runner pulls from, e.g. https://synthora-bot.fly.dev


class VaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # the vault name Obsidian shows
    path: Path  # checked against the CLI at startup
    busy_seconds: float = Field(
        gt=0
    )  # FR-25: a note changed this recently may be in use
    git_commits: bool  # FR-20: commit after each Capture, so `git revert` undoes one
    backend: Literal["cli", "files"] = (
        "cli"  # files: no Obsidian app needed (the server)
    )


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
    pull_api_token: SecretStr | None = None  # 8.4: the laptop's key to the pull API
    pull_api: PullApiConfig | None = (
        None  # set: pull from the server; unset: read the local DB
    )
    timezone: ZoneInfo = ZoneInfo("UTC")

    # From .yaml
    llm: LLMConfig
    bot: BotConfig
    capture: CaptureConfig
    extraction: ExtractionConfig
    storage: StorageConfig
    tracing: TracingConfig
    vault: VaultConfig | None = None

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
