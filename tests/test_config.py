import pytest
from pydantic import ValidationError
from synthora.config import Settings

def test_validation_errors_do_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-appear")
    monkeypatch.delenv("TELEGRAM", raising=False) # force a validation error
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)

    assert "sk-should-not-appear" not in str(exc.value)