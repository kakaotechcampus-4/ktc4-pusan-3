"""위치 이용 확인자료 표 location_use_log (#693)

위치정보법 제16조 ② 의 자동 기록이다. 좌표 칸을 두지 않는다 — 위치 값은 저장하지 않는다.
🚨 downgrade 하면 표와 함께 확인자료가 사라진다. 운영에서는 내리지 않는다.

Revision ID: e4b19c07d2a6
Revises: d5e8a1c4b937
Create Date: 2026-11-03 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b19c07d2a6"
down_revision: Union[str, Sequence[str], None] = "d5e8a1c4b937"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "location_use_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "suggestion",
                name="location_purpose",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_location_use_log_parent_id", "location_use_log", ["parent_id"])
    op.create_index("ix_location_use_log_used_at", "location_use_log", ["used_at"])


def downgrade() -> None:
    op.drop_index("ix_location_use_log_used_at", table_name="location_use_log")
    op.drop_index("ix_location_use_log_parent_id", table_name="location_use_log")
    op.drop_table("location_use_log")
