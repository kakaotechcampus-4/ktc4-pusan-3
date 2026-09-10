"""alter Pattern-A enum columns to VARCHAR(32)

Pattern A 파일들(identity, child, consent, schedule)은 length 를 지정하지 않아
SQLAlchemy 가 현재 최장 값 길이로 VARCHAR(N) 을 만들었다.
enum_col_py 로 통합하면서 length=32 로 맞춘다.

대상 컬럼 8개:
  auth_identity.provider            VARCHAR(6)  → VARCHAR(32)
  parent_child.relation             VARCHAR(11) → VARCHAR(32)
  consent.scope                     VARCHAR(15) → VARCHAR(32)
  consent.action                    VARCHAR(9)  → VARCHAR(32)
  event.event_type                  VARCHAR(8)  → VARCHAR(32)
  event.category                    VARCHAR(11) → VARCHAR(32)
  event.status                      VARCHAR(9)  → VARCHAR(32)
  event.created_by                  VARCHAR(9)  → VARCHAR(32)

Revision ID: c1d2e3f4a5b6
Revises: 70e8ab2bf4b9
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "auth_identity", "provider",
        type_=sa.String(32),
        existing_type=sa.String(6),
        existing_nullable=False,
    )
    op.alter_column(
        "parent_child", "relation",
        type_=sa.String(32),
        existing_type=sa.String(11),
        existing_nullable=False,
    )
    op.alter_column(
        "consent", "scope",
        type_=sa.String(32),
        existing_type=sa.String(15),
        existing_nullable=False,
    )
    op.alter_column(
        "consent", "action",
        type_=sa.String(32),
        existing_type=sa.String(9),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "event_type",
        type_=sa.String(32),
        existing_type=sa.String(8),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "category",
        type_=sa.String(32),
        existing_type=sa.String(11),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "status",
        type_=sa.String(32),
        existing_type=sa.String(9),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "created_by",
        type_=sa.String(32),
        existing_type=sa.String(9),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "event", "created_by",
        type_=sa.String(9),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "status",
        type_=sa.String(9),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "category",
        type_=sa.String(11),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "event", "event_type",
        type_=sa.String(8),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "consent", "action",
        type_=sa.String(9),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "consent", "scope",
        type_=sa.String(15),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "parent_child", "relation",
        type_=sa.String(11),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
    op.alter_column(
        "auth_identity", "provider",
        type_=sa.String(6),
        existing_type=sa.String(32),
        existing_nullable=False,
    )
