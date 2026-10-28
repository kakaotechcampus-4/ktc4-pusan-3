"""일정 알림이 울릴 시각 대신 "얼마나 먼저" 를 저장한다 (#594)

- remind_at(울릴 시각) → lead("1시간 전" 같은 고정 목록 값). 울릴 시각은 보낼 때 일정 시작에서
  계산한다(`app/rules/reminder.py`) — 일정 시간을 옮기면 알림이 따라간다.
- (event_id, parent_id) 유니크 — 보호자 한 명은 한 일정에 알림 1개. 이 유니크가 event_id 로 찾는
  인덱스를 겸해서 ix_reminder_event_id 는 지운다.

lead 는 NOT NULL 로 바로 더한다 — reminder 에는 아직 쓰기 경로가 없어 행이 없다. 행이 있으면
ADD COLUMN 이 실패한다(옮길 값이 없다). downgrade 도 같은 이유로 행이 있으면 실패한다.

Revision ID: db405527cc82
Revises: 091b165cedea
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "db405527cc82"
down_revision: Union[str, Sequence[str], None] = "091b165cedea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEADS = (
    "at_start",
    "5m",
    "10m",
    "15m",
    "30m",
    "1h",
    "2h",
    "1d",
    "2d",
    "1w",
    "day_of_9am",
    "1d_9am",
    "2d_9am",
    "1w_9am",
)


def upgrade() -> None:
    op.add_column(
        "reminder",
        sa.Column(
            "lead",
            sa.Enum(
                *_LEADS, name="reminder_lead", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
    )
    op.drop_column("reminder", "remind_at")
    op.drop_index("ix_reminder_event_id", table_name="reminder")
    op.create_unique_constraint(
        "uq_reminder_event_id_parent_id", "reminder", ["event_id", "parent_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_reminder_event_id_parent_id", "reminder", type_="unique")
    op.create_index("ix_reminder_event_id", "reminder", ["event_id"], unique=False)
    op.add_column(
        "reminder",
        sa.Column("remind_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
    )
    op.drop_column("reminder", "lead")
