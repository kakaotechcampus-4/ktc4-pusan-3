"""복약 알림 구독(medication_notice_subscription) 표를 더한다 (#749)

보호자 한 명이 코스 하나의 복약 알림을 받는다는 행이다. 행이 있으면 받고, 끄면 지운다(일정 알림
`reminder` 와 같다). 결정과 근거는 docs/medication/medication-tables-v1.md.

- `subscribed_at` — 켠 시각. 이보다 예정 시각이 앞선 회차는 보내지 않는다.
- 코스 FK · 보호자 FK 모두 CASCADE — 코스(아이) 삭제 · 탈퇴 때 함께 지워진다.
- (schedule_id, parent_id) 유니크, parent_id 인덱스(연결 끊기 · 탈퇴가 보호자로 찾는다).

Revision ID: 885b03bd5b61
Revises: 361f26c96b2e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "885b03bd5b61"
down_revision: Union[str, Sequence[str], None] = "361f26c96b2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "medication_notice_subscription",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("schedule_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("subscribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["medication_schedule.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id",
            "parent_id",
            name="uq_medication_notice_subscription_schedule_id_parent_id",
        ),
    )
    op.create_index(
        "ix_medication_notice_subscription_parent_id",
        "medication_notice_subscription",
        ["parent_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_medication_notice_subscription_parent_id", table_name="medication_notice_subscription"
    )
    op.drop_table("medication_notice_subscription")
