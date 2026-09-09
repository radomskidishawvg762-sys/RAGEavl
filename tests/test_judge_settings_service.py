"""Runtime Judge settings are public-field-only and never expose API keys."""

from __future__ import annotations

from pydantic import SecretStr

from app.engines.judge import JudgeConfig
from app.services.judge_settings_service import JudgeSettingsService


def test_runtime_judge_update_is_not_persisted_or_echoed(monkeypatch) -> None:
    service = JudgeSettingsService()
    monkeypatch.setattr(
        "app.services.judge_settings_service.judge_config_from_settings",
        lambda: JudgeConfig(provider="openai", model="old", api_key=SecretStr("secret-value")),
    )
    before = service.get()
    assert before["source"] == "environment"
    saved = service.update({"provider": "anthropic", "model": "new-model"})
    assert saved["source"] == "runtime"
    assert saved["provider"] == "anthropic"
    assert saved["model"] == "new-model"
    assert saved["api_key_configured"] is True
    assert "api_key" not in saved
    assert "secret-value" not in str(saved)


def test_runtime_judge_override_takes_precedence_over_profile_defaults(monkeypatch) -> None:
    service = JudgeSettingsService()
    monkeypatch.setattr(
        "app.services.judge_settings_service.judge_config_from_settings",
        lambda: JudgeConfig(provider="openai", model="default", api_key=SecretStr("secret-value")),
    )
    service.update({"provider": "anthropic", "model": "runtime-model"})
    assert service.has_runtime_override() is True
    assert service.config().model == "runtime-model"
