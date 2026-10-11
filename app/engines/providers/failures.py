"""Provider-neutral failures and bounded retry policy; never expose upstream bodies."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress

import httpx

from app.core.errors import ExtJudgeUnavailableError
from app.engines.judge import JudgeConfig


class InvalidJudgeResponse(ValueError):
    """A successful HTTP response did not contain usable model text."""


_MESSAGES = {
    "authentication": ("EXT_JUDGE_AUTHENTICATION", "API Key 无效或已过期，请检查密钥。"),
    "permission": ("EXT_JUDGE_PERMISSION_DENIED", "当前账号或套餐无权调用该模型，请更换可用模型或检查权限。"),
    "request_invalid": ("EXT_JUDGE_REQUEST_INVALID", "请求地址、模型名或参数不符合接口要求，请检查接口协议与配置。"),
    "quota_exhausted": ("EXT_JUDGE_QUOTA_EXHAUSTED", "模型服务额度已用尽，请等待额度恢复或调整套餐。"),
    "rate_limited": ("EXT_JUDGE_RATE_LIMITED", "模型服务请求过于频繁，请降低并发或稍后重试。"),
    "timeout": ("EXT_JUDGE_TIMEOUT", "模型服务响应超时，请检查网络或调整超时设置。"),
    "network": ("EXT_JUDGE_NETWORK", "无法连接模型服务，请检查服务地址、网络和代理。"),
    "response_invalid": ("EXT_JUDGE_RESPONSE_INVALID", "模型服务返回格式不正确或没有文本，请检查接口协议及输出参数。"),
    "unavailable": ("EXT_JUDGE_UNAVAILABLE", "模型服务暂时不可用，请稍后重试。"),
}
_QUOTA_MARKERS = ("insufficient_quota", "quota_exceeded", "usage_limit", "usage limit", "weekly limit",
                  "usagelimiterror", "daily limit", "monthly limit", "credit balance", "insufficient balance", "billing_hard_limit")


def provider_failure(error: Exception, config: JudgeConfig, *, attempts: int = 1) -> ExtJudgeUnavailableError:
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    # Some compatible gateways return errors through a non-SDK wrapper.
    text = str(error).lower()
    if status is None:
        match = re.match(r"(?:http\s+)?(4\d\d|5\d\d)\b", text)
        status = int(match.group(1)) if match else None
    body = str(getattr(error, "body", "")).lower()
    if response is not None:
        with suppress(Exception):  # Streamed/unread bodies are optional classification evidence.
            body += response.text.lower()
    if isinstance(error, InvalidJudgeResponse) or (status is not None and 200 <= status < 300):
        category = "response_invalid"
    elif status == 401:
        category = "authentication"
    elif status == 402 or (status in (403, 429) and any(marker in text + body for marker in _QUOTA_MARKERS)):
        category = "quota_exhausted"
    elif status == 403:
        category = "permission"
    elif status == 429:
        category = "rate_limited"
    elif status in (408, 504) or isinstance(error, (TimeoutError, httpx.TimeoutException)) or "timeout" in type(error).__name__.lower():
        category = "timeout"
    elif status is not None and 400 <= status < 500:
        category = "request_invalid"
    elif isinstance(error, (ConnectionError, httpx.TransportError)) or "connection" in type(error).__name__.lower():
        category = "network"
    else:
        category = "unavailable"
    retryable = category in {"rate_limited", "timeout", "network"} or (
        category == "unavailable" and status is not None and status >= 500
    )
    code, message = _MESSAGES[category]
    retry_after = None
    if response is not None:
        with suppress(ValueError, TypeError):
            retry_after = max(0.0, float(response.headers.get("retry-after")))
    # Never persist/log upstream text: it can contain echoed keys, headers or URLs.
    detail = message + (f"（HTTP {status}）" if status is not None else "")
    return ExtJudgeUnavailableError(detail, code=code, context={
        "provider": config.provider, "model": config.model, "category": category,
        "upstream_status": status, "retryable": retryable, "attempts": attempts,
        "retry_after_seconds": retry_after,
    })


async def invoke_with_retry(call: Callable[[], Awaitable[str]], config: JudgeConfig) -> str:
    attempts = max(1, config.retry)
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except ExtJudgeUnavailableError:
            raise
        except Exception as error:  # noqa: BLE001 -- provider boundary, classified below
            failure = provider_failure(error, config, attempts=attempt)
            retry_after = failure.context.get("retry_after_seconds") or 0
            # A long server cooldown belongs in the UI, not a sleeping evaluation task.
            if not failure.context["retryable"] or attempt == attempts or retry_after > 5:
                raise failure from None
            await asyncio.sleep(max(min(0.5 * attempt, 2), retry_after))
    raise AssertionError("unreachable")
