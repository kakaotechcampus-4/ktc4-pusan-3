"""Food 카탈로그 두 표 — 아이와 무관한 공용 데이터라 `child_id` 가 없다.
(food_agent_own_table §1 · §1-1)

`menu_catalog` 는 급식 · 기록 · 영양 판정용, `recipe_catalog` 는 식단 추천 후보 풀이다. 두 표를 뗀
이유는 레시피 재료로 급식 메뉴를 판정하면 어린이집이 실제로 넣은 재료를 표준 레시피가 가리기
때문이다(#326 리뷰).

🚨 `allergen_codes` · `ingredients` · `main_ingredients` · `food_groups` · `stage_min` ·
   `resolved` 는 알레르기 판정 입력이다. CHECK 로 값 범위를 막고, 어댑터는 값을 바꾸지 않고
   옮긴다.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Numeric, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

# 마이그레이션과 같은 글자다. 값을 바꾸면 둘 다 고친다
ALLERGEN_CODES_CHECK = (
    "allergen_codes <@ '{1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19}'::smallint[]"
)
STAGE_CHECK = (
    "stage_min IS NULL OR stage_min IN ('infant_milk', 'infant_weaning', 'toddler', 'preschool')"
)
FOOD_GROUPS_CHECK = (
    "food_groups <@ '{grains,meat_fish_egg_legumes,vegetables,fruits,dairy,fats_sugars}'::text[]"
)
SERVING_CHECK = "serving_g IS NULL OR serving_g > 0"


class _CatalogColumns:
    """두 표가 같이 쓰는 칸 — 행 모양이 같고 `source` 로 가른다(`MenuCatalogRow`)."""

    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    food_code: Mapped[str | None] = mapped_column(Text)
    serving_g: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    nutrients: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ingredients: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    allergen_codes: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default="{}"
    )
    food_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    stage_min: Mapped[str | None] = mapped_column(String(20))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    source_version: Mapped[str | None] = mapped_column(String(32))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MenuCatalog(_CatalogColumns, Base):
    """메뉴 카탈로그 — 메뉴명(공개 DB · 급식표) → 식품코드 · 영양성분 (own_table §1)."""

    __tablename__ = "menu_catalog"
    __table_args__ = (
        CheckConstraint(
            "source IN ('mfds_nutri', 'center_standard', 'manual')", name="menu_catalog_source"
        ),
        CheckConstraint(ALLERGEN_CODES_CHECK, name="menu_catalog_allergen_codes"),
        CheckConstraint(STAGE_CHECK, name="menu_catalog_stage_min"),
        CheckConstraint(FOOD_GROUPS_CHECK, name="menu_catalog_food_groups"),
        CheckConstraint(SERVING_CHECK, name="menu_catalog_serving_g"),
    )

    menu_key: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)


class RecipeCatalog(_CatalogColumns, Base):
    """레시피 카탈로그 — 식단 추천 후보 풀 (own_table §1-1). 이름이 같은 레시피도 따로 행이다."""

    __tablename__ = "recipe_catalog"
    __table_args__ = (
        CheckConstraint("source IN ('mafra_recipe', 'mfds_recipe')", name="recipe_catalog_source"),
        CheckConstraint(ALLERGEN_CODES_CHECK, name="recipe_catalog_allergen_codes"),
        CheckConstraint(STAGE_CHECK, name="recipe_catalog_stage_min"),
        CheckConstraint(FOOD_GROUPS_CHECK, name="recipe_catalog_food_groups"),
        CheckConstraint(SERVING_CHECK, name="recipe_catalog_serving_g"),
    )

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    menu_key: Mapped[str] = mapped_column(Text, nullable=False)  # UNIQUE 아님
    main_ingredients: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
