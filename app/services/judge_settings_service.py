"""Runtime Judge settings overlay.

The overlay is intentionally process-local for the MVP. Secrets are accepted only
as SecretStr and are never returned, persisted, or included in snapshots.
"""

from __future__ import annotations

import asyncio
from threading import RLock
from time import perf_counter
from typing import Any

from app.core.errors import ExtJudgeUnavailableError, JudgeNotConfiguredError
from app.engines.judge import JudgeConfig, build_judge, judge_config_from_settings
from app.engines.providers.failures import provider_failure


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

    @staticmethod
    def _merge(current: JudgeConfig, values: dict[str, Any]) -> JudgeConfig:
        # Empty fields keep the existing values/key, matching runtime save behavior.
        update = {key: value for key, value in values.items()
                  if value is not None and not (isinstance(value, str) and not value.strip())}
        return JudgeConfig.model_validate({**current.model_dump(), **update})

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self._config or judge_config_from_settings()
            self._config = self._merge(current, values)
            return self.get()

    async def test(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            config = self._merge(self._config or judge_config_from_settings(), values or {})
            source = "draft" if values else ("runtime" if self._config is not None else "environment")
        # Probe the draft without saving it. Exactly one provider attempt, bounded cost/time.
        probe = config.model_copy(update={"retry": 1, "max_tokens": min(config.max_tokens, 64),
                                          "timeout": min(config.timeout, 10)})
        judge = build_judge(probe)
        result = {**config.public_dump(), "source": source,
                  "api_key_configured": config.api_key is not None,
                  "configured": bool(judge.describe().get("configured", False)),
                  "reachable": False, "test_status": "failed"}
        if not result["configured"]:
            return {**result, "test_status": "not_configured", "error_code": "BIZ_JUDGE_NOT_CONFIGURED",
                    "message": "Judge 配置不完整或接口协议不受支持，请检查 provider、模型和 API Key。"}
        started = perf_counter()
        try:
            await asyncio.wait_for(judge.ainvoke("Reply with OK."), timeout=probe.timeout)
        except JudgeNotConfiguredError:
            result.update(test_status="not_configured", error_code="BIZ_JUDGE_NOT_CONFIGURED",
                          message="Judge 配置不完整，请检查 provider、模型和 API Key。")
        except Exception as error:  # noqa: BLE001 -- probe returns a safe structured result
            failure = error if isinstance(error, ExtJudgeUnavailableError) else provider_failure(error, probe)
            result.update(error_code=failure.code, error_category=failure.context.get("category"),
                          retryable=failure.context.get("retryable", False),
                          upstream_status=failure.context.get("upstream_status"), message=failure.detail)
        else:
            result.update(reachable=True, test_status="success", message="连接成功，模型已返回有效文本。")
        result["latency_ms"] = round((perf_counter() - started) * 1000)
        return result


default_judge_settings_service = JudgeSettingsService()
