"""Keep shared child records when their writer is deleted.

Revision ID: e7b2a9c4f013
Revises: d4f6a1b8c207

Downgrade requires every writer reference to be non-NULL. Deleted writers cannot
be recovered; PostgreSQL rejects SET NOT NULL rather than discarding records or
inventing replacement authors. Transactional DDL rolls back a failed downgrade.
"""

from alembic import op
import sqlalchemy as sa

revision = "e7b2a9c4f013"
down_revision = "d4f6a1b8c207"
branch_labels = None
depends_on = None

WRITER_COLUMNS = (
    ("observation_food", "source_writer"),
    ("observation_education", "source_writer"),
    ("observation_activity", "source_writer"),
    ("observation_health", "source_writer"),
    ("health_safety", "created_by"),
)


def upgrade() -> None:
    for table, column in WRITER_COLUMNS:
        constraint = f"{table}_{column}_fkey"
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.alter_column(table, column, existing_type=sa.UUID(), nullable=True)
        op.create_foreign_key(constraint, table, "parent", [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    for table, column in reversed(WRITER_COLUMNS):
        constraint = f"{table}_{column}_fkey"
        op.alter_column(table, column, existing_type=sa.UUID(), nullable=False)
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(constraint, table, "parent", [column], ["id"], ondelete="RESTRICT")
