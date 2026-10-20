"""대화 한 턴을 담는 chat_turn 표를 더한다 (#228)

보호자 입력 하나와 그 run 의 답 전체가 한 행이다. 앱을 다시 켜도 그날과 지난 날의 대화를
다시 볼 수 있게 한다. 칸과 저장 방식은 #228 의 10-15 댓글에서 정했다.

- 쓴 보호자 본인만 보는 개인 기록이라 보호자 · 아이 둘 다 CASCADE 다 (일기와 같다).
  연결 끊기는 서비스 코드가 지운다.
- run_id 는 registry 의 uuid4().hex 문자열이라 suggestion.run_id 와 같은 text 다. 같은 키 재생이
  두 행을 만들지 않게 UNIQUE 다.
- reply_to 는 FK 가 아니다 — 질문 행보다 답이 먼저 올 수 있다.

autogenerate 를 쓰지 않고 손으로 썼다. 적용 뒤 `alembic revision --autogenerate` 가 빈 diff 를
내는지로 모델과 맞는지 확인한다.

Revision ID: 091b165cedea
Revises: 6af377d02e9d
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "091b165cedea"
down_revision: Union[str, Sequence[str], None] = "6af377d02e9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_turn",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=False),
        sa.Column("child_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("conversation_date", sa.Date(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("reply_to", sa.Text(), nullable=True),
        sa.Column("replies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_id"], ["child.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_chat_turn_parent_child_date",
        "chat_turn",
        ["parent_id", "child_id", "conversation_date", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_turn_parent_child_date", table_name="chat_turn")
    op.drop_table("chat_turn")
