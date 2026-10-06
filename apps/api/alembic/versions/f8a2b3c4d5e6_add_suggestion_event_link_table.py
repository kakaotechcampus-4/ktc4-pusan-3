"""add suggestion_event link table

Revision ID: f8a2b3c4d5e6
Revises: 3e7a9c1d5b20
Create Date: 2026-10-05 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "9a1d5e3f7b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "suggestion_event",
        sa.Column("suggestion_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"],
            ["suggestion.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("suggestion_id", "event_id"),
    )
    op.create_index("ix_suggestion_event_event_id", "suggestion_event", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_suggestion_event_event_id", table_name="suggestion_event")
    op.drop_table("suggestion_event")
