"""삭제 제안 표를 만든다 (#535)

- delete_proposal — 대화 run 하나의 삭제 카드. AI 가 고른 후보를 보호자가 버튼으로 확정한다.
- tabs 는 (종류, id) 와 기본 체크만 담는 JSONB 다. 원문을 담지 않는다.
- status 는 pending · deleted · cancelled. 만료는 저장하지 않고 expires_at 으로 읽을 때 판정한다.
- 탈퇴 · 아이 삭제와 함께 지운다 (parent_id · child_id CASCADE).

Revision ID: d5e8a1c4b937
Revises: 7c1e5d9a2b40
Create Date: 2026-11-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d5e8a1c4b937"
down_revision: Union[str, Sequence[str], None] = "7c1e5d9a2b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "delete_proposal",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("tabs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "deleted",
                "cancelled",
                name="delete_proposal_status",
                native_enum=False,
                length=32,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_delete_proposal_parent_child", "delete_proposal", ["parent_id", "child_id"])


def downgrade() -> None:
    op.drop_index("ix_delete_proposal_parent_child", table_name="delete_proposal")
    op.drop_table("delete_proposal")
