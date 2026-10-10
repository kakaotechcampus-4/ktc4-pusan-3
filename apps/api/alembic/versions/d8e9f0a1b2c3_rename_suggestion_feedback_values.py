"""suggestion_feedback enum 값 rename: liked → child_liked, disliked → child_disliked

프론트(types.ts)는 child_liked/child_disliked 를 쓰고
서버(models.py)는 liked/disliked 를 썼다. 프론트 기준으로 통일한다.

native_enum=False 라서 VARCHAR + CHECK 제약이다. 네이티브 ENUM 이 아니므로
ALTER TYPE 이 아니라 기존 값 UPDATE + CHECK 교체로 처리한다.

Revision ID: d8e9f0a1b2c3
Revises: f8a2b3c4d5e6
Create Date: 2026-10-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "f8a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "suggestion"
COLUMN = "feedback"
CONSTRAINT = "suggestion_feedback"
OLD_VALUES = ("liked", "disliked", "not_acted")
NEW_VALUES = ("child_liked", "child_disliked", "not_acted")


def _replace_check(values: tuple[str, ...]) -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    joined = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(CONSTRAINT, TABLE, f"{COLUMN} IN ({joined})")


def upgrade() -> None:
    op.execute(f"UPDATE {TABLE} SET {COLUMN} = 'child_liked' WHERE {COLUMN} = 'liked'")
    op.execute(f"UPDATE {TABLE} SET {COLUMN} = 'child_disliked' WHERE {COLUMN} = 'disliked'")
    _replace_check(NEW_VALUES)


def downgrade() -> None:
    op.execute(f"UPDATE {TABLE} SET {COLUMN} = 'liked' WHERE {COLUMN} = 'child_liked'")
    op.execute(f"UPDATE {TABLE} SET {COLUMN} = 'disliked' WHERE {COLUMN} = 'child_disliked'")
    _replace_check(OLD_VALUES)
