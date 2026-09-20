"""remove event status and expires_at, add event_item timestamps

9/17 결정(#118) — event 는 보호자가 제출한 행만 담으므로 draft/confirmed 를
가르는 status 와 그 draft 의 만료 시각(expires_at)이 더 이상 필요 없다.
취소는 hard delete 로 통일한다.

status 는 VARCHAR + CHECK 로 구현돼 있었다(app/infra/db/types.py 의 enum_col_py).
downgrade 에서 원래 타입 정의(sa.Enum(..., native_enum=False, create_constraint=True))를
그대로 재사용해 CHECK 까지 복원한다 — 단순 VARCHAR(32) 로 되돌리면 값 검증이 사라진다.

event 테이블은 아직 쓰기 경로가 없어 비어 있다(라우터·리포지토리 없음). downgrade 의
`expires_at ADD COLUMN ... NOT NULL` 에 기본값을 둬서, 나중에 행이 쌓인 뒤 이 파일을
그대로 재사용하는 실수를 하더라도 최소한 실패하지 않게 해뒀다 — 그래도 값 자체는
의미가 없으니 실제로 데이터가 있는 상태에서 이 downgrade 를 쓰지는 말 것.

Revision ID: 0f4480ddc873
Revises: e4f31ff97e3a
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0f4480ddc873"
down_revision: Union[str, Sequence[str], None] = "e4f31ff97e3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("event", "status")
    op.drop_column("event", "expires_at")
    op.add_column(
        "event_item",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.add_column(
        "event_item",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("event_item", "updated_at")
    op.drop_column("event_item", "created_at")
    op.add_column(
        "event",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "event",
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "confirmed",
                "cancelled",
                name="event_status",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default="draft",
            nullable=False,
        ),
    )
