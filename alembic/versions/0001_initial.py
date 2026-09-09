"""initial: 10 RAGEval business tables

Revision ID: 0001
Revises:
Create Date: 2026-08-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. projects
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_status_created_at", "projects", ["status", "created_at"])

    # 2. datasets
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("validation_status", sa.Text(), nullable=False),
        sa.Column("validation_report", postgresql.JSONB(), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "name", "version", name="uq_datasets_project_name_version"
        ),
    )

    # 3. dataset_records
    op.create_table(
        "dataset_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column("reference_contexts", postgresql.JSONB(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dataset_records_dataset_row", "dataset_records", ["dataset_id", "row_index"]
    )

    # 4. evaluation_configs
    op.create_table(
        "evaluation_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain_config", postgresql.JSONB(), nullable=True),
        sa.Column("profile_config", postgresql.JSONB(), nullable=True),
        sa.Column("pipeline_config", postgresql.JSONB(), nullable=True),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column("judge_params", postgresql.JSONB(), nullable=True),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_eval_configs_project_name"),
    )

    # 5. evaluation_runs
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("config_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("total_records", sa.Integer(), nullable=False),
        sa.Column("evaluated_records", sa.Integer(), nullable=False),
        sa.Column("error_records", sa.Integer(), nullable=False),
        sa.Column("evaluation_coverage", sa.Float(), nullable=True),
        sa.Column("reproducibility_meta", postgresql.JSONB(), nullable=False),
        sa.Column("error_summary", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.ForeignKeyConstraint(["config_id"], ["evaluation_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6. evaluation_results
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("contexts", postgresql.JSONB(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column("reference_contexts", postgresql.JSONB(), nullable=True),
        sa.Column("is_failure", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
        sa.ForeignKeyConstraint(["record_id"], ["dataset_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_eval_results_run_failure", "evaluation_results", ["run_id", "is_failure"]
    )
    op.create_index(
        "ix_eval_results_run_record", "evaluation_results", ["run_id", "record_id"]
    )

    # 7. metric_results
    op.create_table(
        "metric_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("result_id", sa.String(length=36), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("comparison_basis", postgresql.JSONB(), nullable=True),
        sa.Column("metric_version", sa.Text(), nullable=False),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["result_id"], ["evaluation_results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metric_results_result_metric", "metric_results", ["result_id", "metric_name"]
    )

    # 8. diagnoses
    op.create_table(
        "diagnoses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("result_id", sa.String(length=36), nullable=True),
        sa.Column("failure_type", sa.Text(), nullable=False),
        sa.Column("related_metric", sa.Text(), nullable=True),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_contract", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
        sa.ForeignKeyConstraint(["result_id"], ["evaluation_results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_diagnoses_run_severity", "diagnoses", ["run_id", "severity"])
    op.create_index(
        "ix_diagnoses_run_failure_type", "diagnoses", ["run_id", "failure_type"]
    )
    op.create_index("ix_diagnoses_result", "diagnoses", ["result_id"])

    # 9. recommendations
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("diagnosis_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["diagnoses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # 10. metric_definitions (mirror only, no FK)
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_requirements", postgresql.JSONB(), nullable=True),
        sa.Column("default_severity", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_metric_definitions_name"),
    )


def downgrade() -> None:
    # Reverse order; never DROP DATABASE (constraint 18).
    for table in (
        "metric_definitions",
        "recommendations",
        "diagnoses",
        "metric_results",
        "evaluation_results",
        "evaluation_runs",
        "evaluation_configs",
        "dataset_records",
        "datasets",
        "projects",
    ):
        op.drop_table(table)
