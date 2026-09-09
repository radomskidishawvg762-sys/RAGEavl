"""diagnoses: status / detail / nullable failure_type (T-12)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29

Minimal migration (user decision 2026-08-29 §9 — smallest change that lets the
schema express the T-11/T-12 diagnosis semantics):

1. + status TEXT NOT NULL DEFAULT 'diagnosed'
   'diagnosed' | 'undetermined'. not_failed is NOT persisted (no row).
   Encodes "cannot attribute ≠ no failure" (Spec 5.3.3 / FR-24) without
   overloading failure_type.
2. + detail JSONB NULL
   undetermined rows store {"reason": str, "missing_evidence": [..]} here —
   the record of WHY attribution was forbidden. diagnosed rows: NULL.
3. failure_type TEXT NULL (was NOT NULL)
   NULL = undetermined with no applicable taxonomy code (ambiguous /
   no-matching-rule). failure_type is ONLY ever a real taxonomy code or NULL —
   never a sentinel string like 'undetermined'.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "diagnoses",
        sa.Column("status", sa.Text(), nullable=False, server_default="diagnosed"),
    )
    op.add_column(
        "diagnoses",
        sa.Column("detail", postgresql.JSONB(), nullable=True),
    )
    op.alter_column("diagnoses", "failure_type", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("diagnoses", "failure_type", existing_type=sa.Text(), nullable=False)
    op.drop_column("diagnoses", "detail")
    op.drop_column("diagnoses", "status")
