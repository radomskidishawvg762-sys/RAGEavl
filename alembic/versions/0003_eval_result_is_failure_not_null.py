"""evaluation_results.is_failure -> NOT NULL DEFAULT false (Pre-T13 C2)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29

Fixes the Architecture Freeze Review deviation: Spec §5.2 declares
`is_failure BOOLEAN NOT NULL DEFAULT false`, but 0001 shipped it nullable.
EvaluationService always writes an explicit boolean, so this is a DDL-contract
fix only — no data rewrite, no table rebuild. Touches nothing else.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "evaluation_results",
        "is_failure",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.alter_column(
        "evaluation_results",
        "is_failure",
        existing_type=sa.Boolean(),
        nullable=True,
        server_default=None,
    )
