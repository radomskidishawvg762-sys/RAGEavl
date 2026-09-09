from __future__ import annotations

from app.domain.schemas import EvidenceBundle


class InsufficientEvidenceError(Exception):
    """Evidence did not satisfy a diagnosis type's contract (ADR-07).

    Never infer root cause from score alone (A.5.5). DiagnosisEngine persists
    `undetermined` instead of guessing.
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
