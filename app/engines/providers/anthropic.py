"""Anthropic Messages judge adapter (T-19).

opencode.ai/zen/go is an Anthropic-Messages-compatible gateway:
  POST {base_url}/v1/messages
  headers: x-api-key, anthropic-version
  body: {model, max_tokens, temperature?, messages: [{role: user, content}]}

Plain httpx — no anthropic SDK dependency (SDK isolation like openai.py).
Errors: transport/API failures -> ExtJudgeUnavailableError. The api_key is a
SecretStr, never logged/described.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.errors import ExtJudgeUnavailableError
from app.engines.judge import JudgeConfig
from app.engines.providers.failures import InvalidJudgeResponse, invoke_with_retry, provider_failure
from app.engines.providers.transport import uses_environment_proxy

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicMessagesJudge:
    def __init__(self, config: JudgeConfig) -> None:
        self._config = config

    # ---- JudgeClient protocol ----

    async def ainvoke(self, prompt: str) -> str:
        try:
            return await self._invoke(prompt)
        except ExtJudgeUnavailableError:
            raise
        except Exception as error:  # noqa: BLE001 -- client construction/configuration boundary
            raise provider_failure(error, self._config) from None

    def describe(self) -> dict[str, Any]:
        return {**self._config.public_dump(), "configured": True}

    # ---- internals ----

    async def _invoke(self, prompt: str) -> str:
        base = self._config.base_url.rstrip("/")
        key = self._config.api_key.get_secret_value() if self._config.api_key else ""
        headers = {
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        headers.update(self._config.extra_headers or {})
        body: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._config.temperature != 0:
            body["temperature"] = self._config.temperature
        async with httpx.AsyncClient(timeout=self._config.timeout, trust_env=uses_environment_proxy(base)) as client:
            async def call() -> str:
                response = await client.post(f"{base}/v1/messages", headers=headers, json=body)
                response.raise_for_status()
                try:
                    data = response.json()
                    blocks = data.get("content") if isinstance(data, dict) else None
                    if not isinstance(blocks, list):
                        raise InvalidJudgeResponse()
                    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                except (ValueError, TypeError):
                    raise InvalidJudgeResponse() from None
                if not text.strip():
                    raise InvalidJudgeResponse()
                return text

            return await invoke_with_retry(call, self._config)
