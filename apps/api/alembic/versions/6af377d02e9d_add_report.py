"""앱 안 신고 · 문의를 받는 report 테이블 (#487)

Play 정책(AI 생성 콘텐츠 · 사용자 제작 콘텐츠)이 앱을 나가지 않고 신고하는 길을 요구한다.
보호자가 쓴 글이라 로그가 아니라 이 테이블에 둔다 (루트 CLAUDE.md §2).

autogenerate 를 쓰지 않고 손으로 썼다. 적용 뒤 `alembic revision --autogenerate` 가 빈 diff 를
내는지로 모델과 맞는지 확인한다.

Revision ID: 6af377d02e9d
Revises: a3e9c1f7b204
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6af377d02e9d"
down_revision: Union[str, Sequence[str], None] = "a3e9c1f7b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("reporter_parent_id", sa.UUID(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "ai_content",
                "shared_record",
                "bug",
                "other",
                name="report_category",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["reporter_parent_id"], ["parent.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_reporter_parent_id", "report", ["reporter_parent_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_report_reporter_parent_id", table_name="report")
    op.drop_table("report")
