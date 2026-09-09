from __future__ import annotations

import logging
import re

from app.core.config import settings

_DEFAULT_REDACT_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "database_url",
    "connection_string",
}

# Masks connection-string credentials and bearer/api-key tokens in free text.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(postgresql(?:\+\w+)?://)[^@\s]+(@)", re.IGNORECASE), r"\1***\2"),
    (re.compile(r"(sk-)[A-Za-z0-9_-]+"), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._-]+", re.IGNORECASE), r"\1***"),
]


class RedactingFilter(logging.Filter):
    """Strips secrets from log messages. Never logs raw connection strings or keys."""

    def __init__(self, redact_keys: set[str] | None = None) -> None:
        self.redact_keys = redact_keys or set(_DEFAULT_REDACT_KEYS)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage(), redact_keys=self.redact_keys)
        record.args = ()
        return True


def redact_text(text: str, *, redact_keys: set[str] | None = None) -> str:
    """Secret-safe free text (T-14C): the same masking the logging filter uses,
    reusable for Machine Report error messages — reports must never carry
    connection strings, sk-* keys or bearer tokens."""
    msg = text
    for pat, repl in _PATTERNS:
        msg = pat.sub(repl, msg)
    for k in (redact_keys or _DEFAULT_REDACT_KEYS):
        msg = re.sub(rf'("{k}"\s*:\s*")[^"]*(")', r"\1***\2", msg, flags=re.IGNORECASE)
    return msg


def setup_logging() -> None:
    level = logging.INFO
    if settings.rageval_env == "test":
        level = logging.WARNING
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(levelname)-5.5s [%(name)s] %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
