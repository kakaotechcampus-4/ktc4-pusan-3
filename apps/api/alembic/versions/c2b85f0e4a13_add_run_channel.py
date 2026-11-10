"""run_channel · run_event — run 채널의 표 미러 (#877 4단계)

- 🚨 UNLOGGED — DB 가 비정상 종료되면 비워진다(메모리 채널이 재시작에 사라지던 것과 같다).
- 삭제 트리거는 걸지 않는다 — `deletion_log.NOT_LOGGED` 에 이유가 있다.
- 새 표만 만든다 (expand-only).

Revision ID: c2b85f0e4a13
Revises: a4c9e2f17d58
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c2b85f0e4a13"
down_revision: str | Sequence[str] | None = "a4c9e2f17d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_channel",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("ended_with", sa.Text(), nullable=True),
        sa.Column("closed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        prefixes=["UNLOGGED"],
    )
    op.create_index("ix_run_channel_parent_id", "run_channel", ["parent_id"])
    op.create_index("ix_run_channel_expires_at", "run_channel", ["expires_at"])
    op.create_table(
        "run_event",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["run_channel.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "seq"),
        prefixes=["UNLOGGED"],
    )


def downgrade() -> None:
    op.drop_table("run_event")
    op.drop_table("run_channel")
