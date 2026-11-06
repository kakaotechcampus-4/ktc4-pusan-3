"""suggestion 에 행사 기간(event_starts_on · event_ends_on)을 더한다 (#516)

Activity 가 행사를 붙인 추천의 행사 기간이다. 날짜 없이 행사 이름만 나가면 지난 행사인지 알 수
없어서, 화면이 확인 문구 줄에 날짜로 세운다(FE 합의). 근거가 아니다.
nullable — 행사가 없는 추천과 이 칸이 생기기 전의 행은 비어 있다.

Revision ID: 7c1e5d9a2b40
Revises: 1de43098440d
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e5d9a2b40"
down_revision: Union[str, Sequence[str], None] = "1de43098440d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("suggestion", sa.Column("event_starts_on", sa.Date(), nullable=True))
    op.add_column("suggestion", sa.Column("event_ends_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("suggestion", "event_ends_on")
    op.drop_column("suggestion", "event_starts_on")
