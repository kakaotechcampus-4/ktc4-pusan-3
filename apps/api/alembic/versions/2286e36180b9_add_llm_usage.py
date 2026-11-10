"""add llm_usage (#710)

LLM 호출 1번에 1행 — 보호자 · run · 창구 · 모델별 토큰 사용량 (BM 산정). 새 표뿐이라 expand-only 다.

🚨 parent_id 는 ON DELETE SET NULL 이다. 탈퇴하면 보호자 칸만 비고 숫자는 90일 남는다
   (제공처 콘솔 합계와 맞춰 보려면 탈퇴한 보호자의 토큰도 있어야 한다).
🚨 source 의 CHECK 값은 글자를 그대로 적었다. 나중에 core 의 `Source` 가 바뀌어도 이 revision 은
   움직이면 안 된다 — 값을 늘리면 새 revision 으로 CHECK 를 넓힌다.

Revision ID: 2286e36180b9
Revises: 815d91c90155
Create Date: 2026-11-09 09:42:49.331900

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2286e36180b9"
down_revision: Union[str, Sequence[str], None] = "815d91c90155"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "source IN ('input', 'suggest', 'photo', 'delete')", name="ck_llm_usage_source"
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["parent.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_occurred_at", "llm_usage", ["occurred_at"], unique=False)
    op.create_index(
        "ix_llm_usage_parent_id_occurred_at",
        "llm_usage",
        ["parent_id", "occurred_at"],
        unique=False,
    )
    op.create_index("ix_llm_usage_run_id", "llm_usage", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_usage_run_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_parent_id_occurred_at", table_name="llm_usage")
    op.drop_index("ix_llm_usage_occurred_at", table_name="llm_usage")
    op.drop_table("llm_usage")
