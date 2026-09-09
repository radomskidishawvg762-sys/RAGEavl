"""JSON import adapter (T-07 / FR-04, FR-08).

Parses the import envelope into raw record dicts. Parse-level failures raise
AdapterParseError -> BIZ_ADAPTER_PARSE_ERROR (400); nothing is persisted.
Structural/type validation of records happens downstream in validation.py
(check #1 "schema"), so type errors surface as locatable report issues, not
parse errors (Spec A.4: checks 1-5 produce the validation_report).

Envelope (decided extension, Spec leaves request schema undefined):
  {"name": str, "domain": str = "general",
   "duplicate_policy": "strict" | "skip" = "strict",
   "records": [ {...}, ... ]}
"""

from __future__ import annotations

import json
from typing import Any

from app.core.errors import BizError
from app.domain.schemas import DatasetRecordData

# Spec §8.2: import must cap file size and reject pathological nesting.
MAX_JSON_BYTES = 10 * 1024 * 1024  # 10 MB engineering default (limit, not a threshold)


class AdapterParseError(BizError):
    code = "BIZ_ADAPTER_PARSE_ERROR"
    http_status = 400


def parse_import_payload(
    raw: bytes | str, max_records: int
) -> tuple[str, str, str, list[dict[str, Any]]]:
    """Parse the import envelope. Returns (name, domain, duplicate_policy, records).

    Raises AdapterParseError on: oversize, unparseable JSON, non-object envelope,
    missing/invalid name or records, records list over max_records.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise AdapterParseError(
            "import payload exceeds size limit",
            context={"max_bytes": MAX_JSON_BYTES},
        )
    try:
        payload = json.loads(raw)
    except RecursionError as e:
        raise AdapterParseError("JSON nesting too deep") from e
    except (ValueError, UnicodeDecodeError) as e:
        raise AdapterParseError(f"invalid JSON: {e}") from e

    if not isinstance(payload, dict):
        raise AdapterParseError(
            "envelope must be an object with name/records", context={"got": type(payload).__name__}
        )

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AdapterParseError("field 'name' (non-empty string) is required")

    domain = payload.get("domain", "general")
    if not isinstance(domain, str) or not domain.strip():
        raise AdapterParseError("field 'domain' must be a non-empty string")

    policy = payload.get("duplicate_policy", "strict")
    if policy not in ("strict", "skip"):
        raise AdapterParseError(
            "field 'duplicate_policy' must be 'strict' or 'skip'", context={"got": policy}
        )

    records = payload.get("records")
    if not isinstance(records, list):
        raise AdapterParseError("field 'records' must be a list")
    if len(records) > max_records:
        raise AdapterParseError(
            "records exceed max_records_per_dataset",
            context={"count": len(records), "max_records": max_records},
        )
    return name.strip(), domain.strip(), policy, records


def to_dataset_records(raw_records: list[dict[str, Any]]) -> list[DatasetRecordData]:
    """Convert schema-validated raw dicts into DatasetRecordData (post-validation).

    Invalid input raises AdapterParseError; callers only invoke this after the
    schema check passed, so this is a defensive guard, not a validation path.
    """
    try:
        return [DatasetRecordData.model_validate(r) for r in raw_records]
    except Exception as e:  # pragma: no cover - defensive
        raise AdapterParseError(f"record conversion failed: {e}") from e
