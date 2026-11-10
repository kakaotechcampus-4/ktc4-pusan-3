"""suggestion 에 meal_slot 을 더한다 (#898)

식사 추천으로 일정 초안을 만들면 제목이 끼니와 상관없이 "저녁 식사" 였다. Food 모델은 후보마다
끼니를 내는데 추천 행에 칸이 없어 초안을 만들 때 서버가 끼니를 몰랐다. 끼니를 행에 남겨 초안
제목("아침 식사")을 정한다. 근거가 아니다.

nullable · 기본값 없음 — Food 가 아닌 추천과 이 칸이 생기기 전의 행은 끼니를 모른다. 직전 코드는
이 칸을 모르고 쓰지 않아 그대로 돈다 (expand-only).

Revision ID: b7e4c2a9d815
Revises: c2b85f0e4a13
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4c2a9d815"
down_revision: Union[str, Sequence[str], None] = "c2b85f0e4a13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MEAL_SLOT_VALUES = ("breakfast", "lunch", "dinner", "snack")


def upgrade() -> None:
    # 이름 · 식은 모델의 enum_col_py(SuggestionMealSlot, name="suggestion_meal_slot") CHECK 와 같다
    op.add_column("suggestion", sa.Column("meal_slot", sa.String(32), nullable=True))
    values = ", ".join(f"'{value}'" for value in MEAL_SLOT_VALUES)
    op.create_check_constraint("suggestion_meal_slot", "suggestion", f"meal_slot IN ({values})")


def downgrade() -> None:
    op.drop_constraint("suggestion_meal_slot", "suggestion", type_="check")
    op.drop_column("suggestion", "meal_slot")
