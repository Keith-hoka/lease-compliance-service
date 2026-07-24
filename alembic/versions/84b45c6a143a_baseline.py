"""baseline

Revision ID: 84b45c6a143a
Revises:
Create Date: 2026-07-25 01:20:20.899700

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "84b45c6a143a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create acts, sections, ingested_versions and audits."""
    op.create_table(
        "acts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("jurisdiction", sa.String(3), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
    )
    op.create_index("ix_acts_jurisdiction", "acts", ["jurisdiction"])

    op.create_table(
        "sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("act_id", sa.Uuid(), sa.ForeignKey("acts.id"), nullable=False),
        sa.Column("section_no", sa.String(20), nullable=False),
        sa.Column("heading", sa.String(300), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("part", sa.String(300), nullable=True),
        sa.Column("division", sa.String(300), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source_version_date", sa.Date(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_sections_act_id", "sections", ["act_id"])
    op.create_index("ix_sections_act_no_from", "sections", ["act_id", "section_no", "valid_from"])

    op.create_table(
        "ingested_versions",
        sa.Column("act_id", sa.Uuid(), sa.ForeignKey("acts.id"), primary_key=True),
        sa.Column("version_date", sa.Date(), primary_key=True),
    )

    op.create_table(
        "audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("jurisdiction", sa.String(3), nullable=False),
        sa.Column("as_at", sa.Date(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """Drop the four tables in reverse order."""
    op.drop_table("audits")
    op.drop_table("ingested_versions")
    op.drop_table("sections")
    op.drop_table("acts")
