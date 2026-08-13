"""widen_clause_audit_jobs_model

Revision ID: ef1aaaf341bd
Revises: b3e8f2a91c47
Create Date: 2026-08-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ef1aaaf341bd"
down_revision: str | Sequence[str] | None = "b3e8f2a91c47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen clause_audit_jobs.model to fit multi-provider failover refs."""
    op.alter_column(
        "clause_audit_jobs",
        "model",
        existing_type=sa.String(50),
        type_=sa.String(120),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Narrow clause_audit_jobs.model back to 50 characters."""
    op.alter_column(
        "clause_audit_jobs",
        "model",
        existing_type=sa.String(120),
        type_=sa.String(50),
        existing_nullable=False,
    )
