from __future__ import annotations

import uuid
from typing import Any


class AppError(Exception):
    """Base for all RAGEval errors. Unified body: {detail, code, trace_id, context}."""

    code: str = "SYS_INTERNAL"
    http_status: int = 500

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        context: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.detail = detail
        self.code = code or self.code
        self.http_status = http_status or self.http_status
        self.context = context or {}
        self.trace_id = trace_id or str(uuid.uuid4())
        super().__init__(detail)

    def body(self) -> dict[str, Any]:
        return {
            "detail": self.detail,
            "code": self.code,
            "trace_id": self.trace_id,
            "context": self.context,
        }


class BizError(AppError):
    code = "BIZ_VALIDATION_FAILED"
    http_status = 400


class NotFoundError(AppError):
    code = "BIZ_NOT_FOUND"
    http_status = 404


class DatasetLockedError(AppError):
    code = "BIZ_DATASET_LOCKED"
    http_status = 409


class ProjectNameExistsError(AppError):
    """Project names are unique across active AND archived rows (FR-01):
    creation with an existing name is refused with an explicit code instead of
    silently creating a look-alike. App-level check — projects.name has no DB
    unique constraint (no migration in Phase 1A)."""
    code = "BIZ_PROJECT_NAME_EXISTS"
    http_status = 409


class ConfigInvalidError(AppError):
    code = "BIZ_CONFIG_INVALID"
    http_status = 409


class ConfigImportInvalidError(AppError):
    """Configuration Lifecycle v1 (Phase B): YAML import failed validation.

    422 per task §5. `context.errors` carries structured per-field entries
    [{path, message}] — never a bare "YAML invalid".
    """

    code = "BIZ_CONFIG_IMPORT_INVALID"
    http_status = 422


class RunNotCancellableError(BizError):
    """Terminal runs cannot be cancelled (T-13 §七: BIZ_RUN_NOT_CANCELLABLE)."""
    code = "BIZ_RUN_NOT_CANCELLABLE"
    http_status = 409


class JudgeNotConfiguredError(BizError):
    """Judge provider/model/api-key missing (T-14A §六): configuration absence,
    distinct from EXT_JUDGE_UNAVAILABLE (provider reachable but failing) and
    SYS_METRIC_ERROR (internal code bug)."""
    code = "BIZ_JUDGE_NOT_CONFIGURED"
    http_status = 400


class SysError(AppError):
    code = "SYS_INTERNAL"
    http_status = 500


class ExtDbUnavailableError(AppError):
    code = "EXT_DB_UNAVAILABLE"
    http_status = 503


class ExtJudgeUnavailableError(AppError):
    code = "EXT_JUDGE_UNAVAILABLE"
    http_status = 503


# ---- T-14B: RAG input adapter errors (EXT_ family; Judge codes NEVER reused) ----

class ExtRagInputNotFoundError(AppError):
    code = "EXT_RAG_INPUT_NOT_FOUND"
    http_status = 404


class ExtRagAdapterTimeoutError(AppError):
    code = "EXT_RAG_ADAPTER_TIMEOUT"
    http_status = 504


class ExtRagAdapterHttpError(AppError):
    code = "EXT_RAG_ADAPTER_HTTP_ERROR"
    http_status = 502


class ExtRagAdapterParseError(AppError):
    code = "EXT_RAG_ADAPTER_PARSE_ERROR"
    http_status = 502
