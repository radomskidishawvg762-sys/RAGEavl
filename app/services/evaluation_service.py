"""EvaluationService (T-12) — persistence & orchestration boundary.

Responsibilities (user decision 2026-08-29):
  Run lifecycle  : create_run (dataset lock, one transaction) -> execute_run
                   (running -> terminal) -> cancel_run. State machine without
                   percentage thresholds (PRD Q6).
  Persistence    : evaluation_results + metric_results (comparison_basis ->
                   JSONB) + diagnoses + recommendations, via EvaluationRepository.
  Failure marking: is_failure = at least one CONFIRMED Failure for the record
                   (threshold failure OR deterministic integrity mismatch).
                   ambiguous / missing_reference / evaluator errors are NOT
                   failures — they are Undetermined / Evaluation Errors, kept
                   distinct via diagnosis status + run counters.
  Diagnosis      : MetricResult -> FailureClassifier -> Failure -> RuleEngine ->
                   Evidence Contract -> Diagnosis (chain preserved; never
                   MetricResult -> Diagnosis directly).

MetricResult.error semantics (user decision §1, enforced at THIS boundary):
  error ONLY means "evaluator execution exception" (comparison_basis is null).
  Judged null-score outcomes (ambiguous / unit_mismatch / value_mismatch /
  temporal_mismatch / entity_mismatch) are normal evaluation results: they
  carry comparison_basis and MUST persist with error=None. The in-memory T-10
  engine may attach an informational reason dict to error for such outcomes;
  _normalize_result strips it here so the DB never conflates the two shapes.

Boundaries: Service -> Repository (never Session/ORM directly); no LLM; no
Diagnosis rules here (DiagnosisEngine owns rules/evidence); MetricResult and
EvaluationRecord are never mutated in place — persistence uses normalized
copies.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.adapters.rag_input import GoldenRunMetadataAdapter, GoldenSample, RagInputAdapter
from app.core.errors import BizError, RunNotCancellableError
from app.diagnosis import DiagnosisEngine
from app.diagnosis.classifier import FailureClassifier
from app.domain.schemas import DiagnosisResult, EvaluationRecord, MetricResult
from app.engines.base import EvalParams, EvaluationEngine
from app.models import (
    Diagnosis as DiagnosisRow,
)
from app.models import (
    EvaluationResult as EvaluationResultRow,
)
from app.models import (
    MetricResult as MetricResultRow,
)
from app.models import (
    Recommendation as RecommendationRow,
)
from app.repositories.evaluation import EvaluationRepository
from app.runner.base import CancelCheck, EvaluationRunner, ProgressCallback
from app.runner.local import LocalAsyncRunner
from app.services.report_service import compute_overall_score

logger = logging.getLogger(__name__)

# Persisted diagnosis statuses ('not_failed' never becomes a row).
_PERSISTED_DIAGNOSIS_STATUSES = ("diagnosed", "undetermined")

_RUN_TERMINAL_STATES = {"completed", "completed_with_errors", "failed", "cancelled"}

_RUN_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "failed", "cancelled"},
    "running": {"completed", "completed_with_errors", "failed", "cancelled"},
    # terminal states have no outgoing transitions
    "completed": set(),
    "completed_with_errors": set(),
    "failed": set(),
    "cancelled": set(),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize_result(result: MetricResult) -> MetricResult:
    """Enforce the §1 error semantics at the persistence boundary.

    comparison_basis != null -> judged outcome -> error MUST persist as null
    (the in-memory engine may attach an informational reason dict; it never
    reaches the DB). comparison_basis == null + error != null -> genuine
    evaluator exception (score null by construction)."""
    if result.comparison_basis is not None and result.error is not None:
        return result.model_copy(update={"error": None})
    return result


class EvaluationService:
    def __init__(self, repo: EvaluationRepository) -> None:
        self._repo = repo

    # ---- lifecycle ----

    def create_run(
        self,
        *,
        project_id: str,
        dataset_id: str,
        config_id: str,
        reproducibility_meta: dict,
    ):
        """Pending run + dataset lock in ONE transaction (Spec 7.1)."""
        return self._repo.create_run_with_lock(
            project_id=project_id,
            dataset_id=dataset_id,
            config_id=config_id,
            reproducibility_meta=reproducibility_meta,
        )

    def cancel_run(self, run_id: str):
        run = self._repo.get_run(run_id)
        if run.status in _RUN_TERMINAL_STATES:
            # terminal runs are immutable: cancel is refused, not a state machine
            # error (T-13 §七: BIZ_RUN_NOT_CANCELLABLE)
            raise RunNotCancellableError(f"run {run_id} is {run.status}: terminal runs cannot be cancelled")
        self._transition(run, "cancelled")
        cancelled = self._repo.cancel_run_if_active(run_id, finished_at=_utcnow())
        if cancelled is None:
            current = self._repo.get_run(run_id)
            raise RunNotCancellableError(
                f"run {run_id} is {current.status}: cancellation lost a terminal race"
            )
        return cancelled


    def is_run_cancelled(self, run_id: str) -> bool:
        """Use persisted state as the cancellation source of truth.

        Read via get_run_status (a fresh column read), NOT get_run: the executor
        session loaded this run and never expires it (expire_on_commit=False), so
        the identity map would keep returning the status from run creation and a
        cancel committed by the request's session would never be seen — the run
        would keep evaluating every remaining record and then persist full
        coverage onto a row that says "cancelled".
        """
        return self._repo.get_run_status(run_id) == "cancelled"

    # ---- T-13 read surface (Router -> Service -> Repository) ----

    def get_run(self, run_id: str):
        return self._repo.get_run(run_id)

    def get_config(self, config_id: str):
        return self._repo.get_config(config_id)

    def get_dataset(self, dataset_id: str):
        return self._repo.get_dataset(dataset_id)

    def list_runs(self, *, page, page_size, project_id=None, status=None):
        return self._repo.list_runs(page=page, page_size=page_size, project_id=project_id, status=status)

    def list_results(self, run_id, *, page, page_size, is_failure=None):
        return self._repo.list_results(run_id, page=page, page_size=page_size, is_failure=is_failure)

    def get_result_detail(self, run_id: str, result_id: str):
        """Single-result detail bundle (Phase 1A G3): result row + its metric
        rows (comparison_basis included) + its diagnoses (evidence included).
        Pure read-through — nothing is recomputed or regenerated here."""
        result = self._repo.get_result(run_id, result_id)
        metric_rows = self._repo.list_result_metric_rows(result_id)
        diagnosis_rows = self._repo.list_diagnoses_for_result(result_id)
        return result, metric_rows, diagnosis_rows

    def list_diagnoses(self, run_id, *, page, page_size, severity=None, failure_type=None):
        return self._repo.list_diagnoses(run_id, page=page, page_size=page_size,
                                         severity=severity, failure_type=failure_type)

    def list_recommendations(self, run_id):
        return self._repo.list_recommendations(run_id)

    def mark_failed(self, run_id: str, error: BaseException) -> None:
        """Background-task safety net: if execution crashed outside per-record
        isolation, move the run to failed with a structured error_summary."""
        run = self._repo.get_run(run_id)
        try:
            self._transition(run, "failed")
        except BizError:
            return  # already terminal — nothing to do
        self._repo.fail_run_if_active(
            run_id,
            finished_at=_utcnow(),
            error_summary={"error_records": run.error_records,
                           "details": [{"record_id": None,
                                        "error": {"code": "SYS_RUN_EXECUTION_ERROR",
                                                   "message": f"{type(error).__name__}: {error}"[:500]}}]},
        )

    async def execute_run(
        self,
        run_id: str,
        *,
        engines: list[EvaluationEngine],
        enabled_metrics: list[str],
        params: EvalParams,
        diagnosis_engine: DiagnosisEngine | None = None,
        runner: EvaluationRunner | None = None,
        on_progress: ProgressCallback | None = None,
        is_cancelled: CancelCheck | None = None,
        rag_adapter: RagInputAdapter | None = None,
    ) -> dict:
        """Run one evaluation through the EvaluationRunner (Pre-T13 C1).

        Input boundary (T-14B): question/reference_* come from DatasetRecord;
        answer/contexts come from the RagInputAdapter (formal path =
        HttpRagAdapter via the launcher; GoldenRunMetadataAdapter is the
        EXPLICIT test/golden-run fallback). A failed fetch enters the
        evaluation-execution-error path — answer/contexts are never fabricated.

        Execution loop lives in the runner; THIS service owns persistence,
        counters, diagnosis orchestration and the state machine. Per-record
        isolation (FR-22) is the runner's contract — a failed record arrives
        as an exception in on_record_done and never breaks the batch.

        Returns {run_id, status, total, evaluated, errors, coverage}."""
        runner = runner or LocalAsyncRunner()
        rag = rag_adapter or GoldenRunMetadataAdapter()
        # Phase D (task §5): diagnosis.enabled from the EFFECTIVE pipeline —
        # service-orchestration switch only; DiagnosisEngine itself is untouched.
        # The effective-config flag is AUTHORITATIVE (even over an injected engine).
        pipeline_extra = params.extra.get("pipeline") if isinstance(params.extra.get("pipeline"), dict) else {}
        diagnosis_enabled = bool(pipeline_extra.get("diagnosis_enabled", True))
        if not diagnosis_enabled:
            diagnosis_engine = None
        elif diagnosis_engine is None:
            diagnosis_engine = DiagnosisEngine(severity_mapping=params.severity_mapping or {})
        run = self._repo.get_run(run_id)
        self._transition(run, "running")
        started = self._repo.start_run_if_pending(run_id, started_at=_utcnow())
        if started is None:
            current = self._repo.get_run(run_id)
            return {
                "run_id": run_id,
                # Fresh status read: the ORM-enabled UPDATE in
                # start_run_if_pending synchronises its SET values onto the
                # in-session object even when rowcount == 0, so `current` may
                # claim "running" for a run a concurrent cancel just persisted
                # as "cancelled".
                "status": self._repo.get_run_status(run_id),
                "total": current.total_records,
                "evaluated": current.evaluated_records,
                "errors": current.error_records,
                "coverage": current.evaluation_coverage or 0.0,
            }

        records = self._repo.list_records(run.dataset_id)
        total = len(records)
        self._repo.update_run(run_id, total_records=total)

        golden_by_id = {r.id: r for r in records}
        row_index_by_id = {r.id: i for i, r in enumerate(records)}
        # placeholder records carry golden identity only — answer/contexts are
        # filled per record by resolve_record (RagInputAdapter), never here
        placeholders = {
            r.id: EvaluationRecord(
                id=r.id, question=r.question, contexts=[], answer="",
                reference_answer=r.reference_answer,
                reference_contexts=list(r.reference_contexts or []),
                metadata=dict(r.metadata_ or {}),
            )
            for r in records
        }
        assembled_by_id: dict[str, EvaluationRecord] = {}

        async def resolve_record(record_id: str):
            row = golden_by_id[record_id]
            rag_output = await rag.fetch(
                GoldenSample(record_id=row.id, question=row.question,
                             metadata=dict(row.metadata_ or {}))
            )
            record = self._assemble_record(row, rag_output)
            assembled_by_id[record_id] = record
            return record

        state = {"evaluated": 0, "error_records": 0}
        error_details: list[dict] = []
        classifier = FailureClassifier()

        def on_record_done(record_id: str, payload) -> None:
            if isinstance(payload, Exception):
                state["error_records"] += 1
                # adapter faults keep their EXT_ codes; evaluation faults stay SYS
                code = getattr(payload, "code", "SYS_RECORD_EVALUATION_ERROR")
                error_details.append(
                    {"record_id": record_id,
                     "error": {"code": code,
                               "message": f"{type(payload).__name__}: {payload}"[:500]}}
                )
                self._repo.update_run_progress(
                    run_id,
                    evaluated_records=state["evaluated"],
                    error_records=state["error_records"],
                    evaluation_coverage=(state["evaluated"] / total) if total else 0.0,
                )
                return
            record = assembled_by_id.get(record_id) or placeholders[record_id]

            normalized = [_normalize_result(r) for r in payload]
            if any(r.error is None for r in normalized):
                state["evaluated"] += 1
            else:
                state["error_records"] += 1

            # is_failure: only CONFIRMED failures — never ambiguity/error (§3)
            is_failure = any(classifier.classify(r).status == "failure" for r in normalized)
            result_row = self._repo.insert_evaluation_result(
                EvaluationResultRow(
                    run_id=run_id,
                    record_id=record.id,
                    row_index=row_index_by_id[record_id],
                    question=record.question,
                    contexts=record.contexts,
                    answer=record.answer,
                    reference_answer=record.reference_answer,
                    reference_contexts=record.reference_contexts,
                    is_failure=is_failure,
                )
            )
            self._repo.insert_metric_results(
                [
                    MetricResultRow(
                        result_id=result_row.id,
                        metric_name=r.metric_name,
                        category=r.category,
                        score=r.score,
                        threshold=r.threshold,
                        passed=r.passed,
                        comparison_basis=r.comparison_basis.model_dump() if r.comparison_basis else None,
                        metric_version=r.metric_version,
                        error=r.error,
                    )
                    for r in normalized
                ]
            )

            self._repo.update_run_progress(
                run_id,
                evaluated_records=state["evaluated"],
                error_records=state["error_records"],
                evaluation_coverage=(state["evaluated"] / total) if total else 0.0,
            )

            for r in normalized:
                if diagnosis_engine is None:
                    continue  # diagnosis.enabled=false — results/failures persist, no diagnosis rows
                outcome = diagnosis_engine.diagnose(record, r)
                if outcome.status not in _PERSISTED_DIAGNOSIS_STATUSES:
                    continue  # not_failed: no diagnosis row
                drow = self._to_diagnosis_row(run_id, result_row.id, r, outcome)
                rec_rows = [
                    RecommendationRow(action=rec.action, priority=rec.priority, source=rec.source)
                    for rec in outcome.recommendations
                ]
                self._repo.insert_diagnosis_with_recommendations(drow, rec_rows)

        summary = await runner.run(
            records=list(placeholders.values()),
            engines=engines,
            enabled_metrics=enabled_metrics,
            params=params,
            on_progress=on_progress,
            on_record_done=on_record_done,
            is_cancelled=is_cancelled,
            resolve_record=resolve_record,
        )

        evaluated = state["evaluated"]
        error_records = state["error_records"]
        coverage = (evaluated / total) if total else 0.0
        status = "cancelled" if summary.cancelled else LocalAsyncRunner._determine_status(
            total, evaluated, error_records
        )
        # T-14C §七: overall_score is computed ONCE at run completion and
        # persisted — GET /report reads the stored value, never recomputes.
        overall_score, _valid, _total = compute_overall_score(
            self._repo.list_metric_rows_for_run(run_id),
            (run.reproducibility_meta or {}).get("metric_weights") or {},
        )
        finished = self._repo.finish_run_if_running(
            run_id,
            status=status,
            overall_score=overall_score,
            evaluated_records=evaluated,
            error_records=error_records,
            evaluation_coverage=coverage,
            finished_at=_utcnow(),
            error_summary=({"error_records": error_records, "details": error_details}
                           if error_details else None),
        )
        # Fresh column read, not get_run: the ORM-enabled UPDATE above syncs its
        # SET values onto the in-session object even when rowcount == 0.
        status = (
            finished.status if finished is not None else self._repo.get_run_status(run_id)
        )
        return {
            "run_id": run_id,
            "status": status,
            "total": total,
            "evaluated": evaluated,
            "errors": error_records,
            "coverage": coverage,
        }

    # ---- internals ----

    @staticmethod
    def _assemble_record(record_row, rag_output) -> EvaluationRecord:
        """T-14B assembly (formal input boundary):
          question / reference_answer / reference_contexts / metadata  <- DatasetRecord
          answer / contexts                                            <- RagOutput (RAG Adapter)
        RagOutput.metadata (if any) is merged under the "rag" key; golden
        metadata keys keep precedence. Never fabricates content — a failed
        fetch never reaches this method (record-level error path instead)."""
        metadata = dict(record_row.metadata_ or {})
        if rag_output.metadata:
            metadata["rag"] = dict(rag_output.metadata)
        return EvaluationRecord(
            id=record_row.id,
            question=record_row.question,
            contexts=list(rag_output.contexts),
            answer=rag_output.answer,
            reference_answer=record_row.reference_answer,
            reference_contexts=list(record_row.reference_contexts or []),
            metadata=metadata,
        )

    @staticmethod
    def _to_diagnosis_row(
        run_id: str, evaluation_result_id: str, result: MetricResult, outcome: DiagnosisResult
    ) -> DiagnosisRow:
        """Domain DiagnosisResult -> ORM row (user decision §4).

        diagnosed    -> failure_type = real taxonomy code, root_cause set,
                        evidence = contract-satisfying items.
        undetermined -> root_cause = None (attribution forbidden),
                        failure_type = candidate real code or NULL (never a
                        sentinel), detail = {reason, missing_evidence}.
        result_id    -> FK to evaluation_results.id (sample level).
        """
        if outcome.status == "diagnosed":
            d = outcome.diagnosis
            assert d is not None  # engine contract
            return DiagnosisRow(
                run_id=run_id,
                result_id=evaluation_result_id,
                status="diagnosed",
                failure_type=d.failure_type,
                related_metric=d.related_metric,
                root_cause=d.root_cause,
                severity=d.severity,
                evidence=[e.model_dump() for e in d.evidence],
                evidence_contract=d.evidence_contract,
                confidence=d.confidence,
                detail=None,
            )
        return DiagnosisRow(
            run_id=run_id,
            result_id=evaluation_result_id,
            status="undetermined",
            failure_type=outcome.candidate_failure_type,  # real code or NULL
            related_metric=result.metric_name,
            root_cause=None,
            severity="INFO",
            evidence=[],
            evidence_contract=(f"{outcome.candidate_failure_type}.v1"
                               if outcome.candidate_failure_type else ""),
            confidence="low",
            detail={"reason": outcome.reason, "missing_evidence": outcome.missing_evidence},
        )

    def _transition(self, run, to: str) -> None:
        allowed = _RUN_TRANSITIONS.get(run.status, set())
        if to not in allowed:
            raise BizError(
                f"illegal run transition {run.status} -> {to}",
                code="BIZ_RUN_STATE_TRANSITION",
            )
