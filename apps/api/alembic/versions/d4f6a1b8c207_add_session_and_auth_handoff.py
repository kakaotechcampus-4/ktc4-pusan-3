"""add session and auth_handoff

카카오 OAuth 로그인(#34)이 쓰는 두 테이블 — 명세 docs/api/auth-kakao-v1.md §5-3 · §5-4.

autogenerate 가 아니라 손으로 썼다. 이 저장소에 uv 환경이 없는 곳에서 작업해
--autogenerate 를 돌릴 수 없었고, apps/api/CLAUDE.md 가 "확인 없이 커밋하지 않는다" 로
정한 대상(CHECK 제약·enum)이 둘 다 여기 들어 있다. 적용 뒤 `alembic revision --autogenerate`
가 빈 diff 를 내는지로 모델과 일치하는지 확인할 것.

enum 컬럼은 처음부터 length=32 로 만든다. c1d2e3f4a5b6 이 VARCHAR(N) 을 32 로 올리는
마이그레이션이었는데, 여기서 길이를 빼면 같은 일을 또 하게 된다.

Revision ID: d4f6a1b8c207
Revises: c1d2e3f4a5b6
Create Date: 2026-09-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a1b8c207"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # session — 명세 §5-3
    op.create_table(
        "session",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        # bytea. SHA-256 digest 32바이트이고 토큰 원문은 저장하지 않는다 (§5 · A-18).
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 토큰 대조가 매 요청 경로다. UNIQUE 는 중복 방지이자 그 조회의 인덱스다.
    op.create_index("ix_session_token_hash", "session", ["token_hash"], unique=True)
    # 탈퇴·아이 파기에서 parent_id 로 전부 지운다.
    op.create_index("ix_session_parent_id", "session", ["parent_id"])
    # 만료 배치가 expires_at < now() 로 훑는다.
    op.create_index("ix_session_expires_at", "session", ["expires_at"])

    # auth_handoff — 명세 §5-4
    op.create_table(
        "auth_handoff",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        # 클라이언트 비밀의 해시. 원문을 담지 않아 행이 새도 bind 를 위조할 수 없다 (§7-2).
        sa.Column("bind_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "kakao",
                "apple",
                "google",
                "naver",
                name="auth_provider",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            nullable=False,
        ),
        # 기존 회원일 때만 찬다.
        sa.Column("parent_id", sa.UUID(), nullable=True),
        # 처음 보는 회원번호일 때만 찬다. signup 이 auth_identity 를 만들 때 쓴다.
        sa.Column("provider_user_id", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # 둘 중 정확히 하나만 NOT NULL (§5-4). 코드가 아니라 DB 가 막는다.
        sa.CheckConstraint(
            "(parent_id IS NULL) <> (provider_user_id IS NULL)",
            name="ck_auth_handoff_parent_xor_provider_user",
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # DELETE … RETURNING 이 code_hash 로 한 행을 집는다. 원자적 소비의 조회 경로다.
    op.create_index("ix_auth_handoff_code_hash", "auth_handoff", ["code_hash"], unique=True)
    op.create_index("ix_auth_handoff_expires_at", "auth_handoff", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_handoff_expires_at", table_name="auth_handoff")
    op.drop_index("ix_auth_handoff_code_hash", table_name="auth_handoff")
    op.drop_table("auth_handoff")

    op.drop_index("ix_session_expires_at", table_name="session")
    op.drop_index("ix_session_parent_id", table_name="session")
    op.drop_index("ix_session_token_hash", table_name="session")
    op.drop_table("session")
