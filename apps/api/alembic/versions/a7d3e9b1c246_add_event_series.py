"""add event_series and event.series_id (#738)

요일 반복 묶음. 회차는 날짜마다 따로인 event 행이고, 묶음 행은 규칙(요일 · 첫 회차 · 끝 날짜)만 든다.

Revision ID: a7d3e9b1c246
Revises: 7c1e5d9a2b40
Create Date: 2026-11-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e9b1c246"
down_revision: Union[str, Sequence[str], None] = "7c1e5d9a2b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_series",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("weekdays", sa.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("until", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # autogenerate 가 못 잡는 CHECK — 직접 쓴다 (apps/api/CLAUDE.md §Alembic)
        sa.CheckConstraint("until >= starts_on", name="ck_event_series_until_after_start"),
        sa.CheckConstraint(
            "cardinality(weekdays) > 0 AND weekdays <@ ARRAY[1,2,3,4,5,6,7]::smallint[]",
            name="ck_event_series_weekdays",
        ),
    )
    op.create_index("ix_event_series_child_id", "event_series", ["child_id"])
    op.add_column("event", sa.Column("series_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "event_series_id_fkey", "event", "event_series", ["series_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_event_series_id", "event", ["series_id"])


def downgrade() -> None:
    op.drop_index("ix_event_series_id", table_name="event")
    op.drop_constraint("event_series_id_fkey", "event", type_="foreignkey")
    op.drop_column("event", "series_id")
    op.drop_index("ix_event_series_child_id", table_name="event_series")
    op.drop_table("event_series")
