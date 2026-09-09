"""Golden-set validation: the 5 checks (Spec A.4 / PRD FR-05).

Check names match the Spec A.4 report contract verbatim:
  schema / duplicate / missing_field / reference / domain_metadata

Issues locate records by row_index = source position (0-based), which becomes
dataset_records.row_index (Spec §5.2: "error localization to row_index").

Normalization rule for duplicate detection (Spec: "deduplicate on normalized
question" — rule itself is a decided extension): NFKC + trim + collapse inner
whitespace + casefold.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from app.domain.schemas import (
    ValidationCheck,
    ValidationIssue,
    ValidationReport,
)

DUPLICATE_POLICY_STRICT = "strict"
DUPLICATE_POLICY_SKIP = "skip"


def normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", q).strip()).casefold()


def _lookup(record: dict[str, Any], dotted: str) -> tuple[Any, bool]:
    """Dotted-path lookup (supports input_requirements like 'metadata.domain')."""
    cur: Any = record
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def check_schema(records: list[dict[str, Any]]) -> ValidationCheck:
    """Required fields present, types correct. `question` is the only record-level
    required field (answer/contexts belong to runtime, not the golden set)."""
    issues: list[ValidationIssue] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            issues.append(ValidationIssue(row_index=i, field="record", detail="must be an object"))
            continue
        question, present = _lookup(rec, "question")
        if not present:
            issues.append(ValidationIssue(row_index=i, field="question", detail="missing"))
        elif not isinstance(question, str):
            issues.append(ValidationIssue(row_index=i, field="question", detail="must be a string"))
        elif not question.strip():
            issues.append(ValidationIssue(row_index=i, field="question", detail="blank"))
        rc = rec.get("reference_contexts")
        if rc is not None and not (
            isinstance(rc, list) and all(isinstance(x, str) for x in rc)
        ):
            issues.append(
                ValidationIssue(row_index=i, field="reference_contexts", detail="must be a list of strings")
            )
        meta = rec.get("metadata")
        if meta is not None and not isinstance(meta, dict):
            issues.append(ValidationIssue(row_index=i, field="metadata", detail="must be an object"))
    return ValidationCheck(
        name="schema", passed=not issues, count=len(records) if not issues else None, issues=issues
    )


def check_duplicate(
    records: list[dict[str, Any]], policy: str
) -> tuple[ValidationCheck, list[int]]:
    """Normalized-question duplicate detection.

    strict: any duplicate -> check fails, issues list every subsequent occurrence.
    skip:   check passes with count = dropped rows; issues still included for
            auditability. Returns (check, indexes_to_drop).
    """
    seen: dict[str, int] = {}
    issues: list[ValidationIssue] = []
    drop: list[int] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        q = rec.get("question")
        if not isinstance(q, str):
            continue  # schema check already flags it
        key = normalize_question(q)
        if key in seen:
            issues.append(
                ValidationIssue(
                    row_index=i, field="question", detail=f"duplicate of row {seen[key]}"
                )
            )
            drop.append(i)
        else:
            seen[key] = i
    passed = policy == DUPLICATE_POLICY_SKIP or not issues
    return (
        ValidationCheck(
            name="duplicate",
            passed=passed,
            count=len(drop) if passed and drop else (None if issues else 0),
            issues=issues,
        ),
        drop if policy == DUPLICATE_POLICY_SKIP else [],
    )


def check_missing_fields(
    records: list[dict[str, Any]], required_fields: Iterable[str]
) -> ValidationCheck:
    """Missing-field check against metric input_requirements (Spec A.4 check #3).

    required_fields comes from the MetricRegistry (union of registered metrics'
    input_requirements) — never hardcoded here. Empty registry -> trivially passes.
    """
    issues: list[ValidationIssue] = []
    required = sorted(set(required_fields))
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        for dotted in required:
            value, present = _lookup(rec, dotted)
            if not present or value is None or (isinstance(value, str) and not value.strip()):
                issues.append(
                    ValidationIssue(row_index=i, field=dotted, detail="required by enabled metrics")
                )
    return ValidationCheck(
        name="missing_field",
        passed=not issues,
        count=len(records) if not issues else None,
        issues=issues,
    )


def check_reference(records: list[dict[str, Any]]) -> ValidationCheck:
    """reference_answer / reference_contexts validity (decided sub-rules):
    - reference_answer present but blank/whitespace -> issue
    - reference_contexts present but empty list -> issue
    - reference_contexts entries blank strings -> issue
    (Both fields are individually optional; absence alone is not an issue.)"""
    issues: list[ValidationIssue] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        ra = rec.get("reference_answer")
        if isinstance(ra, str) and not ra.strip():
            issues.append(
                ValidationIssue(row_index=i, field="reference_answer", detail="blank")
            )
        rc = rec.get("reference_contexts")
        if isinstance(rc, list):
            if len(rc) == 0:
                issues.append(
                    ValidationIssue(row_index=i, field="reference_contexts", detail="empty list")
                )
            else:
                for j, x in enumerate(rc):
                    if isinstance(x, str) and not x.strip():
                        issues.append(
                            ValidationIssue(
                                row_index=i,
                                field=f"reference_contexts[{j}]",
                                detail="blank entry",
                            )
                        )
    return ValidationCheck(
        name="reference", passed=not issues, count=len(records) if not issues else None, issues=issues
    )


def check_domain_metadata(records: list[dict[str, Any]], domain: str) -> ValidationCheck:
    """metadata.domain must match the dataset's declared domain when present
    (Spec A.4 check #5). Absent metadata.domain is not an issue."""
    issues: list[ValidationIssue] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        meta = rec.get("metadata")
        if not isinstance(meta, dict):
            continue
        rec_domain = meta.get("domain")
        if rec_domain is not None and rec_domain != domain:
            issues.append(
                ValidationIssue(
                    row_index=i,
                    field="metadata.domain",
                    detail=f"{rec_domain!r} != dataset domain {domain!r}",
                )
            )
    return ValidationCheck(
        name="domain_metadata",
        passed=not issues,
        count=len(records) if not issues else None,
        issues=issues,
    )


def validate_records(
    raw_records: list[dict[str, Any]],
    *,
    domain: str,
    required_fields: Iterable[str],
    duplicate_policy: str,
) -> tuple[ValidationReport, list[int]]:
    """Run all 5 checks; return (report, indexes_to_drop).

    indexes_to_drop is non-empty only for duplicate_policy='skip'.
    """
    schema_check = check_schema(raw_records)
    dup_check, drop = check_duplicate(raw_records, duplicate_policy)
    missing_check = check_missing_fields(raw_records, required_fields)
    ref_check = check_reference(raw_records)
    domain_check = check_domain_metadata(raw_records, domain)
    report = ValidationReport(
        valid=all(
            c.passed for c in (schema_check, dup_check, missing_check, ref_check, domain_check)
        ),
        checks=[schema_check, dup_check, missing_check, ref_check, domain_check],
    )
    return report, drop
