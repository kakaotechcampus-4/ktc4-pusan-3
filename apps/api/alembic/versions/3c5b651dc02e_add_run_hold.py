"""run_hold — 워커 밖 보관함을 만든다 (#877)

프로세스 메모리에 있던 되묻기 맥락(`api/runs/pending_reply.py`)을 옮길 표. 2단계에서 다시 시도 ·
사진 run 이 `kind` 를 더한다 (docs/ops/stateless-v1.md).

- 🚨 UNLOGGED — autogenerate 가 모델의 `prefixes` 를 옮겨 오지만, 확인 없이 지우지 말 것.
  DB 가 비정상 종료되면 비워지고(메모리 보관함과 같은 동작) WAL 에 원문이 남지 않는다.
- 삭제 트리거는 걸지 않는다 — `deletion_log.NOT_LOGGED` 에 이유가 있다.
- 새 표만 만든다 (expand-only).

Revision ID: 3c5b651dc02e
Revises: 90ac5aae7ad1
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3c5b651dc02e"
down_revision: str | Sequence[str] | None = "90ac5aae7ad1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_hold",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("in_use", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("kind IN ('pending_reply')", name="ck_run_hold_kind"),
        # 🚨 보호자 FK 를 먼저 만든다. FK 검사 트리거는 이름(만든 순서) 순으로 돌아 넣을 때 보호자 →
        #    아이 순서로 잠근다 — 탈퇴(withdraw_parent)와 같은 순서라 겹쳐도 교착이 없다 (#899 리뷰)
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("kind", "key"),
        prefixes=["UNLOGGED"],
    )
    op.create_index("ix_run_hold_parent_id", "run_hold", ["parent_id"])
    op.create_index("ix_run_hold_child_id", "run_hold", ["child_id"])
    op.create_index("ix_run_hold_expires_at", "run_hold", ["expires_at"])


def downgrade() -> None:
    op.drop_table("run_hold")
