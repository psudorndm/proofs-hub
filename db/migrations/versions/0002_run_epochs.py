"""Run cursor epochs, per-page filter counts, and observation adapter.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_observations",
        sa.Column("adapter", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
    )
    op.add_column(
        "harvest_runs",
        sa.Column("cursor_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "harvest_pages",
        sa.Column("epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "harvest_pages",
        sa.Column("filtered_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "harvest_pages",
        sa.Column("provider_response_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_observations", "adapter")
    op.drop_column("harvest_pages", "provider_response_at")
    op.drop_column("harvest_pages", "filtered_count")
    op.drop_column("harvest_pages", "epoch")
    op.drop_column("harvest_runs", "cursor_epoch")
