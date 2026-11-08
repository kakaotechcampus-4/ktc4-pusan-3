"""add event.origin_date (#790)

반복 회차가 처음 만들어진 날(KST). 한 회차만 옮겨도 바뀌지 않아, 묶음 고치기가 지금 날짜가 아니라
이 날짜로 "옛 규칙의 어느 날 회차인가" 를 가린다. expand-only — NULL 허용 컬럼 하나를 더하고 기존
회차를 지금 날짜로 채운다. 옛 이미지는 이 컬럼을 몰라 NULL 로 만들 수 있어 NOT NULL 로 조이지 않는다.

Revision ID: b3f6c8d2e471
Revises: a7d3e9b1c246
Create Date: 2026-11-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3f6c8d2e471"
down_revision: Union[str, Sequence[str], None] = "a7d3e9b1c246"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("event", sa.Column("origin_date", sa.Date(), nullable=True))
    # 이미 옮겨 둔 회차의 원래 날짜는 알 길이 없다 — 지금 날짜(KST)로 채운다
    op.execute(
        "UPDATE event SET origin_date = (starts_at AT TIME ZONE 'Asia/Seoul')::date "
        "WHERE series_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("event", "origin_date")
