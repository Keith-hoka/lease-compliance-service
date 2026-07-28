"""clause_audit_jobs

Revision ID: a1c47e92b5d3
Revises: df9c4c593e57
Create Date: 2026-07-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c47e92b5d3"
down_revision: str | Sequence[str] | None = "df9c4c593e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the clause_audit_jobs table."""
    op.create_table(
        "clause_audit_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.String(50), nullable=False),
        sa.Column("client_ref", sa.String(100), nullable=True),
        sa.Column("jurisdiction", sa.String(3), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("document", sa.LargeBinary(), nullable=True),
        sa.Column("document_kind", sa.String(4), nullable=False),
        sa.Column("lease", sa.JSON(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("discrepancies", sa.JSON(), nullable=False),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_clause_audit_jobs_client_id", "clause_audit_jobs", ["client_id"])
    op.create_index("ix_clause_audit_jobs_client_ref", "clause_audit_jobs", ["client_ref"])


def downgrade() -> None:
    """Drop the clause_audit_jobs table."""
    op.drop_index("ix_clause_audit_jobs_client_ref", "clause_audit_jobs")
    op.drop_index("ix_clause_audit_jobs_client_id", "clause_audit_jobs")
    op.drop_table("clause_audit_jobs")
