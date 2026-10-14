"""parent.deleted_at 을 뺀다 (#379)

탈퇴가 유예 없이 보호자 행을 지우게 되면서(#167 · #379) 이 칸을 채우는 곳이 없어졌다.
읽던 곳(인증의 404 분기 · 로그인 교환의 검사)도 같은 PR 에서 지웠다. 탈퇴한 계정의 다른
기기는 세션이 함께 지워져 401 이다.

child.deleted_at 은 그대로다 — 아이 쪽은 다른 이야기다.

Revision ID: 5c09288aa40c
Revises: 988fc4ef647d
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5c09288aa40c"
down_revision: Union[str, Sequence[str], None] = "988fc4ef647d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("parent", "deleted_at")


def downgrade() -> None:
    op.add_column("parent", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
