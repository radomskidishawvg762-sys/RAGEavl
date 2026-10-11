from __future__ import annotations

import logging

from app.core.errors import DatasetNotEvaluableError
from app.domain.schemas import EvidenceBundle

logger = logging.getLogger(__name__)


class InsufficientEvidenceError(Exception):
    """Raised by `assert_evidence_present` when a bundle has no items.

    DiagnosisEngine does not raise this. An unmet integrity contract returns
    `DiagnosisResult(status="undetermined", missing_evidence=[...])` and the
    service persists that undetermined row. The duplicate class that used to
    live on `EvidenceCollector` was never raised.
    """

    def __init__(self, contract: str, missing: list[str] | None = None) -> None:
        self.contract = contract
        self.missing = missing or []
        msg = f"insufficient evidence for contract {contract}"
        if self.missing:
            msg += f"; missing={self.missing}"
        super().__init__(msg)


def assert_evidence_present(bundle: EvidenceBundle) -> None:
    """Minimum entry guard: a structured bundle with >=1 item.

    Per-type contract validation (the full minimum-sufficient sets) lives in
    EvidenceCollector (T-14, M3). This guard only blocks the forbidden empty/flat form.
    """
    if not bundle.items:
        raise InsufficientEvidenceError(bundle.contract, missing=["items"])


def assert_dataset_evaluable(*, dataset_id: str, validation_status: str, record_count: int) -> None:
    """Reject unusable imports before creating a run or locking the version."""
    if validation_status == "valid" and record_count > 0:
        return
    logger.warning(
        "evaluation dataset rejected: dataset_id=%s validation_status=%s record_count=%s",
        dataset_id, validation_status, record_count,
    )
    raise DatasetNotEvaluableError(
        "数据集未通过校验或没有可评估记录，请查看校验报告，修正后重新导入。",
        context={
            "dataset_id": dataset_id,
            "validation_status": validation_status,
            "record_count": record_count,
        },
    )
