"""Make references to curation_commands deferrable.

A curation command row is immutable and is written last in its transaction, after
the overrides, separation constraints, and events that reference it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FKS = [
    (
        "curation_commands",
        "reverts_command_id",
        "fk_curation_commands_reverts_command_id_curation_commands",
    ),
    ("work_events", "curation_command_id", "fk_work_events_curation_command_id_curation_commands"),
    (
        "source_assignment_overrides",
        "command_id",
        "fk_source_assignment_overrides_command_id_curation_commands",
    ),
    (
        "separation_constraints",
        "command_id",
        "fk_separation_constraints_command_id_curation_commands",
    ),
]


def upgrade() -> None:
    for table, column, name in FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name,
            table,
            "curation_commands",
            [column],
            ["id"],
            deferrable=True,
            initially="DEFERRED",
        )


def downgrade() -> None:
    for table, column, name in FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "curation_commands", [column], ["id"])
