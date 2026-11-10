"""rate_event — 하루 입력 · 초대 시도 카운터를 워커 밖으로 (#877 3단계)

- 🚨 UNLOGGED — DB 가 비정상 종료되면 비워진다(메모리 카운터가 재시작에 비워지던 것과 같다).
- 삭제 트리거는 걸지 않는다 — `deletion_log.NOT_LOGGED` 에 이유가 있다.
- 새 표만 만든다 (expand-only).

Revision ID: a4c9e2f17d58
Revises: 7e1d4c2a9b30
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4c9e2f17d58"
down_revision: str | Sequence[str] | None = "7e1d4c2a9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        prefixes=["UNLOGGED"],
    )
    op.create_index("ix_rate_event_key_at", "rate_event", ["key", "at"])
    op.create_index("ix_rate_event_at", "rate_event", ["at"])


def downgrade() -> None:
    op.drop_table("rate_event")
