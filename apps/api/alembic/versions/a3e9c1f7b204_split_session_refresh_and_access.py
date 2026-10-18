"""session 을 refresh(로그인 1건)와 access(session_access)로 나눈다 (#466)

지금은 session 한 행이 12시간짜리 토큰 하나다. 웹이 그 토큰을 sessionStorage 에 두어 앱을 끄면
사라지고, 매번 카카오 로그인을 다시 거친다. 그래서 access 는 15분짜리로 웹 메모리에, refresh 는
HttpOnly 쿠키에 둔다 (#465 · #466).

  session         — 기기 로그인 1건. refresh 를 맡는다. token_hash · expires_at 을 빼고
                    refresh_hash · prev_refresh_hash · rotated_at · refresh_expires_at ·
                    absolute_expires_at 을 둔다
  session_access  — 신설. access 1건. 한 세션에 여러 행 — 탭 두 개가 번갈아 refresh 해도 서로의
                    access 를 덮어쓰지 않는다

🚨 기존 session 행을 전부 지운다. 옛 행에는 refresh 가 없어 옮길 값이 없고, 남겨도 access 로 쓸
   수 없다(옛 토큰은 12시간짜리였다). 배포 직후 모두 한 번씩 다시 로그인한다. downgrade 도 같은
   이유로 비우고 되돌린다.

autogenerate 를 쓰지 않고 손으로 썼다. 적용 뒤 `alembic revision --autogenerate` 가 빈 diff 를
내는지로 모델과 맞는지 확인한다.

Revision ID: a3e9c1f7b204
Revises: b5be2945826e
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3e9c1f7b204"
down_revision: Union[str, Sequence[str], None] = "b5be2945826e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM session")

    op.drop_index("ix_session_expires_at", table_name="session")
    op.drop_index("ix_session_token_hash", table_name="session")
    op.drop_column("session", "expires_at")
    op.drop_column("session", "token_hash")

    # bytea. SHA-256 digest 32바이트이고 토큰 원문은 저장하지 않는다 (§5 · A-18).
    op.add_column("session", sa.Column("refresh_hash", sa.LargeBinary(), nullable=False))
    op.add_column("session", sa.Column("prev_refresh_hash", sa.LargeBinary(), nullable=True))
    op.add_column("session", sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "session", sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False)
    )
    op.add_column(
        "session", sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False)
    )
    # refresh 가 이 둘로 행을 찾는다. UNIQUE 는 중복 방지이자 그 조회의 인덱스다.
    op.create_index("ix_session_refresh_hash", "session", ["refresh_hash"], unique=True)
    op.create_index("ix_session_prev_refresh_hash", "session", ["prev_refresh_hash"], unique=True)
    # 정리 배치가 둘 중 하나라도 지난 행을 지운다.
    op.create_index("ix_session_refresh_expires_at", "session", ["refresh_expires_at"])
    op.create_index("ix_session_absolute_expires_at", "session", ["absolute_expires_at"])

    op.create_table(
        "session_access",
        # 매 요청의 Bearer 대조가 이 값으로 찾는다. PK 가 그 인덱스다.
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # 로그아웃 · 탈퇴 · 재사용 탐지는 세션 행을 지운다 — access 가 같이 사라져 바로 401 이다.
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_session_access_session_id", "session_access", ["session_id"])
    op.create_index("ix_session_access_expires_at", "session_access", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_session_access_expires_at", table_name="session_access")
    op.drop_index("ix_session_access_session_id", table_name="session_access")
    op.drop_table("session_access")

    op.execute("DELETE FROM session")

    op.drop_index("ix_session_absolute_expires_at", table_name="session")
    op.drop_index("ix_session_refresh_expires_at", table_name="session")
    op.drop_index("ix_session_prev_refresh_hash", table_name="session")
    op.drop_index("ix_session_refresh_hash", table_name="session")
    op.drop_column("session", "absolute_expires_at")
    op.drop_column("session", "refresh_expires_at")
    op.drop_column("session", "rotated_at")
    op.drop_column("session", "prev_refresh_hash")
    op.drop_column("session", "refresh_hash")

    op.add_column("session", sa.Column("token_hash", sa.LargeBinary(), nullable=False))
    op.add_column("session", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_session_token_hash", "session", ["token_hash"], unique=True)
    op.create_index("ix_session_expires_at", "session", ["expires_at"])
