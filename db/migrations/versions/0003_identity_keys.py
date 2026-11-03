"""Identity lookup keys on source_records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_records", sa.Column("title_key", sa.Text(), nullable=True))
    op.add_column(
        "source_records", sa.Column("title_tokens", postgresql.ARRAY(sa.Text()), nullable=True)
    )
    op.add_column("source_records", sa.Column("doi", sa.Text(), nullable=True))
    op.add_column(
        "source_records", sa.Column("explicit_refs", postgresql.ARRAY(sa.Text()), nullable=True)
    )
    op.create_index("ix_source_records_title_key", "source_records", ["title_key"])
    op.create_index("ix_source_records_doi", "source_records", ["doi"])
    op.create_index(
        "ix_source_records_title_tokens", "source_records", ["title_tokens"], postgresql_using="gin"
    )
    op.create_index(
        "ix_source_records_explicit_refs",
        "source_records",
        ["explicit_refs"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_source_records_explicit_refs", table_name="source_records")
    op.drop_index("ix_source_records_title_tokens", table_name="source_records")
    op.drop_index("ix_source_records_doi", table_name="source_records")
    op.drop_index("ix_source_records_title_key", table_name="source_records")
    op.drop_column("source_records", "explicit_refs")
    op.drop_column("source_records", "doi")
    op.drop_column("source_records", "title_tokens")
    op.drop_column("source_records", "title_key")
