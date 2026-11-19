import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from synthora.config import Settings

FAKE_SECRETS = {
    "TELEGRAM_TOKEN": "123:fake",
    "LLM_API_KEY": "sk-fake",
    "GOOGLE_API_KEY": "fake",
    "OWNER_TELEGRAM_ID": "111",
}


@pytest.fixture
def fake_secrets(monkeypatch):
    for name, value in FAKE_SECRETS.items():
        monkeypatch.setenv(name, value)


def test_validation_errors_do_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-appear")
    monkeypatch.delenv("TELEGRAM", raising=False)  # force a validation error
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)

    assert "sk-should-not-appear" not in str(exc.value)


def test_reads_the_real_config_yaml(fake_secrets):
    settings = Settings(_env_file=None)  # secrets from the fake env vars, not your .env
    assert settings.llm.model.startswith("claude-")
    assert settings.llm.effort in {"low", "medium", "high", "xhigh", "max"}
    assert settings.bot.album_wait_seconds > 0


def test_environment_variable_overrides_yaml(fake_secrets, monkeypatch):
    monkeypatch.setenv("LLM__MODEL", "claude-opus-5-5")
    assert Settings(_env_file=None).llm.model == "claude-opus-5-5"


def test_a_typo_in_the_yaml_is_an_error(fake_secrets, tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(
        "llm: {model: claude-sonnet-5-5, max_tokens: 100, efort: medium}\n"
        "bot: {album_wait_seconds: 1.5, preview_chars: 600}\n"
        "extraction: {timeout_seconds: 20}\n",
        encoding="utf-8",
    )

    class SettingsFromBadFile(Settings):  # same settings, different YAML file
        model_config = SettingsConfigDict(
            yaml_file=bad
        )  # merged with the parent's config

    with pytest.raises(ValidationError) as exc:
        SettingsFromBadFile(_env_file=None)
    assert "efort" in str(exc.value)


def test_an_invalid_effort_is_an_error(fake_secrets, monkeypatch):
    monkeypatch.setenv("LLM__EFFORT", "extreme")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
