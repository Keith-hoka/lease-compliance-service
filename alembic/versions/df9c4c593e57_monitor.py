"""monitor

Revision ID: df9c4c593e57
Revises: 84b45c6a143a
Create Date: 2026-07-26 23:08:44.457527

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "df9c4c593e57"
down_revision: str | Sequence[str] | None = "84b45c6a143a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenant keys to audits and create audit_changes."""
    op.add_column(
        "audits",
        sa.Column("client_id", sa.String(50), nullable=False, server_default="legacy"),
    )
    op.add_column("audits", sa.Column("client_ref", sa.String(100), nullable=True))
    op.create_index("ix_audits_client_id", "audits", ["client_id"])
    op.create_index("ix_audits_client_ref", "audits", ["client_ref"])

    op.create_table(
        "audit_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.String(50), nullable=False),
        sa.Column("client_ref", sa.String(100), nullable=False),
        sa.Column("old_audit_id", sa.Uuid(), sa.ForeignKey("audits.id"), nullable=False),
        sa.Column("new_audit_id", sa.Uuid(), sa.ForeignKey("audits.id"), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_changes_client_id", "audit_changes", ["client_id"])
    op.create_index("ix_audit_changes_client_ref", "audit_changes", ["client_ref"])


def downgrade() -> None:
    """Drop audit_changes and the tenant columns."""
    op.drop_table("audit_changes")
    op.drop_index("ix_audits_client_ref", "audits")
    op.drop_index("ix_audits_client_id", "audits")
    op.drop_column("audits", "client_ref")
    op.drop_column("audits", "client_id")
