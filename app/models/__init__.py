from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class _TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Project(Base, _TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, default="general")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active|archived

    __table_args__ = (Index("ix_projects_status_created_at", "status", "created_at"),)


class Dataset(Base, _TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)  # v1/v2/v3
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="valid"
    )  # valid|invalid
    validation_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "project_id", "name", "version", name="uq_datasets_project_name_version"
        ),
    )


class DatasetRecord(Base, _TimestampMixin):
    __tablename__ = "dataset_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_contexts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    __table_args__ = (
        Index("ix_dataset_records_dataset_row", "dataset_id", "row_index"),
    )


class EvaluationConfig(Base, _TimestampMixin):
    __tablename__ = "evaluation_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    profile_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pipeline_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_eval_configs_project_name"),
    )


class EvaluationRun(Base, _TimestampMixin):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id"), nullable=False
    )
    config_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_configs.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluated_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluation_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    reproducibility_meta: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EvaluationResult(Base, _TimestampMixin):
    __tablename__ = "evaluation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset_records.id"), nullable=False
    )
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    contexts: Mapped[list] = mapped_column(JSONB, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_contexts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_failure: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )  # Spec §5.2: NOT NULL DEFAULT false (Pre-T13 C2, migration 0003)

    __table_args__ = (
        Index("ix_eval_results_run_failure", "run_id", "is_failure"),
        Index("ix_eval_results_run_record", "run_id", "record_id"),
    )


class MetricResult(Base, _TimestampMixin):
    __tablename__ = "metric_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    result_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_results.id"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)  # retrieval|generation|integrity
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comparison_basis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metric_version: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_metric_results_result_metric", "result_id", "metric_name"),
    )


class Diagnosis(Base, _TimestampMixin):
    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("evaluation_runs.id"), nullable=False
    )
    result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("evaluation_results.id"), nullable=True
    )  # non-null = Sample Diagnosis; null = Run Diagnosis
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="diagnosed", server_default="diagnosed"
    )  # diagnosed|undetermined (T-12 migration 0002; not_failed is never a row)
    failure_type: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # real taxonomy code ONLY; NULL = undetermined without applicable code
    related_metric: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)  # null when undetermined
    severity: Mapped[str] = mapped_column(Text, nullable=False)  # INFO|WARNING|ERROR|CRITICAL
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    evidence_contract: Mapped[str] = mapped_column(Text, nullable=False)  # "<code>.v1"; "" = no rule matched
    confidence: Mapped[str] = mapped_column(Text, nullable=False)  # high|medium|low
    detail: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # undetermined: {"reason", "missing_evidence"}; diagnosed: NULL

    __table_args__ = (
        Index("ix_diagnoses_run_severity", "run_id", "severity"),
        Index("ix_diagnoses_run_failure_type", "run_id", "failure_type"),
        Index("ix_diagnoses_result", "result_id"),
    )


class Recommendation(Base, _TimestampMixin):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    diagnosis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("diagnoses.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # 1 = highest
    source: Mapped[str] = mapped_column(Text, nullable=False, default="rule")  # rule|llm


class MetricDefinition(Base, _TimestampMixin):
    __tablename__ = "metric_definitions"

    # Mirror of code-registered metrics for UI display + Run snapshot ONLY.
    # NOT a registration entry point (PRD Q8); registration is via MetricRegistry.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_requirements: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    default_severity: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "Project",
    "Dataset",
    "DatasetRecord",
    "EvaluationConfig",
    "EvaluationRun",
    "EvaluationResult",
    "MetricResult",
    "Diagnosis",
    "Recommendation",
    "MetricDefinition",
]
