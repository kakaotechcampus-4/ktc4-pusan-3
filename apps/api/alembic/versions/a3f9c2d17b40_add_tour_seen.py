"""화면 가이드를 본 기록 표를 만든다 (#696)

- tour_seen — 보호자 × 가이드 1행. 행이 없으면 안 본 것이다.
- tour_key 는 text 다. 허용 값은 API 스키마가 막는다 — 화면이 늘 때 마이그레이션이 없게.
- 탈퇴하면 함께 지운다 (parent_id CASCADE).

Revision ID: a3f9c2d17b40
Revises: c7d18f3ae5b2
Create Date: 2026-11-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f9c2d17b40"
down_revision: Union[str, Sequence[str], None] = "c7d18f3ae5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tour_seen",
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("tour_key", sa.Text(), nullable=False),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("parent_id", "tour_key"),
    )


def downgrade() -> None:
    op.drop_table("tour_seen")
