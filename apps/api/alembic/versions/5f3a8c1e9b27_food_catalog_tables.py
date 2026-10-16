"""Food 카탈로그 두 표 — menu_catalog(영양DB · 사람이 넣은 메뉴) · recipe_catalog(식단 추천 후보)

설계는 docs/agents/food/food_agent_own_table.md §1 · §1-1. 공용 데이터라 child_id 가 없다. 두 표를
뗀 이유는 레시피 재료로 급식 메뉴를 판정하면 어린이집이 넣은 재료를 표준 레시피가 가리기
때문이다(#326 리뷰).

CHECK 로 막는 것
  - menu_catalog 에 레시피 소스가 들어오지 않는다(코드는 RECIPE_SOURCES 로 한 번 더 막는다)
  - allergen_codes 는 19종 코드(1~19)만 — 알레르기 판정 입력이다
  - stage_min 은 단계 네 값 · food_groups 는 식품군 여섯 값 · serving_g 는 양수

Revision ID: 5f3a8c1e9b27
Revises: d0be81909739
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5f3a8c1e9b27"
down_revision: Union[str, Sequence[str], None] = "d0be81909739"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALLERGEN_CODES = "allergen_codes <@ '{1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19}'::smallint[]"
STAGE = (
    "stage_min IS NULL OR stage_min IN ('infant_milk', 'infant_weaning', 'toddler', 'preschool')"
)
FOOD_GROUPS = (
    "food_groups <@ '{grains,meat_fish_egg_legumes,vegetables,fruits,dairy,fats_sugars}'::text[]"
)
SERVING = "serving_g IS NULL OR serving_g > 0"


def _shared() -> list[sa.Column]:
    return [
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("food_code", sa.Text(), nullable=True),
        sa.Column("serving_g", sa.Numeric(7, 1), nullable=True),
        sa.Column(
            "nutrients",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ingredients", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "allergen_codes",
            postgresql.ARRAY(sa.SmallInteger()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("food_groups", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("stage_min", sa.String(20), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_version", sa.String(32), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(ALLERGEN_CODES, name=f"{table}_allergen_codes"),
        sa.CheckConstraint(STAGE, name=f"{table}_stage_min"),
        sa.CheckConstraint(FOOD_GROUPS, name=f"{table}_food_groups"),
        sa.CheckConstraint(SERVING, name=f"{table}_serving_g"),
    ]


def upgrade() -> None:
    op.create_table(
        "menu_catalog",
        sa.Column("menu_key", sa.Text(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        *_shared(),
        sa.CheckConstraint(
            "source IN ('mfds_nutri', 'center_standard', 'manual')", name="menu_catalog_source"
        ),
        *_checks("menu_catalog"),
    )
    op.create_table(
        "recipe_catalog",
        sa.Column("source", sa.String(16), primary_key=True),
        sa.Column("source_id", sa.Text(), primary_key=True),
        sa.Column("menu_key", sa.Text(), nullable=False),
        sa.Column(
            "main_ingredients", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        *_shared(),
        sa.CheckConstraint(
            "source IN ('mafra_recipe', 'mfds_recipe')", name="recipe_catalog_source"
        ),
        *_checks("recipe_catalog"),
    )


def downgrade() -> None:
    op.drop_table("recipe_catalog")
    op.drop_table("menu_catalog")
