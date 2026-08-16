"""rent statistics

Revision ID: 2abc7025592c
Revises: ef1aaaf341bd
Create Date: 2026-08-16 20:51:37.494080

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2abc7025592c"
down_revision: str | Sequence[str] | None = "ef1aaaf341bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the rent source file ledger, bond lodgement detail, and statistics tables."""
    op.create_table(
        "rent_source_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jurisdiction", "source_file"),
    )
    op.create_table(
        "rent_bond_lodgements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_file_id", sa.Uuid(), nullable=False),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("postcode", sa.Text(), nullable=False),
        sa.Column("dwelling_type", sa.Text(), nullable=False),
        sa.Column("bedrooms", sa.Integer(), nullable=False),
        sa.Column("weekly_rent", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["source_file_id"], ["rent_source_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rent_bond_lodgements_postcode_period",
        "rent_bond_lodgements",
        ["postcode", "period"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rent_bond_lodgements_source_file_id"),
        "rent_bond_lodgements",
        ["source_file_id"],
        unique=False,
    )
    op.create_table(
        "rent_statistics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("area_code", sa.Text(), nullable=False),
        sa.Column("dwelling_type", sa.Text(), nullable=False),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("median", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("p25", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("p75", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction",
            "period",
            "area_code",
            "dwelling_type",
            "bedrooms",
            name="uq_rent_statistics_key",
        ),
    )
    op.create_index(
        "ix_rent_statistics_lookup",
        "rent_statistics",
        ["jurisdiction", "area_code", "dwelling_type", "bedrooms", "period"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the rent statistics, bond lodgement detail, and source file ledger tables."""
    op.drop_index("ix_rent_statistics_lookup", table_name="rent_statistics")
    op.drop_table("rent_statistics")
    op.drop_index(op.f("ix_rent_bond_lodgements_source_file_id"), table_name="rent_bond_lodgements")
    op.drop_index("ix_rent_bond_lodgements_postcode_period", table_name="rent_bond_lodgements")
    op.drop_table("rent_bond_lodgements")
    op.drop_table("rent_source_files")
