"""Runtime Judge settings overlay.

The overlay is intentionally process-local for the MVP. Secrets are accepted only
as SecretStr and are never returned, persisted, or included in snapshots.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from app.engines.judge import JudgeConfig, build_judge, judge_config_from_settings


class JudgeSettingsService:
    def __init__(self) -> None:
        self._lock = RLock()
        self._config: JudgeConfig | None = None

    def get(self) -> dict[str, Any]:
        with self._lock:
            config = self._config or judge_config_from_settings()
            source = "runtime" if self._config is not None else "environment"
            result = config.public_dump()
            result["api_key_configured"] = config.api_key is not None
            result["configured"] = bool(config.provider and config.model and config.api_key)
            result["source"] = source
            return result

    def config(self) -> JudgeConfig:
        with self._lock:
            return self._config or judge_config_from_settings()

    def has_runtime_override(self) -> bool:
        with self._lock:
            return self._config is not None

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self._config or judge_config_from_settings()
            # Partial update: empty string / None means "keep current value".
            # Empty strings must never clobber an existing provider/model/base_url.
            update = {
                key: value
                for key, value in values.items()
                if value is not None and not (isinstance(value, str) and value.strip() == "")
            }
            if "api_key" not in update:
                update["api_key"] = current.api_key
            self._config = JudgeConfig.model_validate({**current.model_dump(), **update})
            return self.get()

    def test(self) -> dict[str, Any]:
        with self._lock:
            config = self._config or judge_config_from_settings()
            judge = build_judge(config)
        description = judge.describe()
        return {
            **description,
            "api_key_configured": config.api_key is not None,
            "configured": bool(description.get("configured", False)),
            "reachable": False,
            "message": "配置已通过本地校验；未发送测试请求。",
        }


default_judge_settings_service = JudgeSettingsService()
