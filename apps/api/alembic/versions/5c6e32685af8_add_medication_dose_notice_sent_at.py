"""medication_dose 에 복약 알림 처리 시각(notice_sent_at)과 발송용 인덱스를 더한다 (#749)

발송 배치가 회차를 맡으면서 찍는 시각이다. 결정과 근거는 docs/medication/medication-tables-v1.md.

- nullable — 아직 맡지 않은 회차는 비어 있다. 이미 있는 회차는 채우지 않는다(지난 회차는 배치가
  10분 창 밖이라 맡지 않는다).
- 보내기 **전에** 찍는다. 성공 여부가 아니라 "배치가 맡았다" 는 뜻이다 — 일정 알림의
  `reminder.sent_at` 과 같다.
- 부분 인덱스 `(dose_date, scheduled_time) WHERE notice_sent_at IS NULL AND checked_at IS NULL` —
  배치가 1분마다 아직 맡지 않은 미체크 회차를 날짜 · 시각으로 찾는다. 맡았거나 체크한 회차는
  빠진다. 맡지 않고 지나간 회차(구독자 없음 · 멈춘 코스 · 창을 놓침)는 남지만 앞 칸이 날짜라
  배치는 하루 이틀 범위만 읽는다.

Revision ID: 5c6e32685af8
Revises: 885b03bd5b61
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5c6e32685af8"
down_revision: Union[str, Sequence[str], None] = "885b03bd5b61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "medication_dose",
        sa.Column("notice_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "medication_dose_notice_due_idx",
        "medication_dose",
        ["dose_date", "scheduled_time"],
        postgresql_where=sa.text("notice_sent_at IS NULL AND checked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("medication_dose_notice_due_idx", table_name="medication_dose")
    op.drop_column("medication_dose", "notice_sent_at")
