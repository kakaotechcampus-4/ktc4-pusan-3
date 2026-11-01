"""recipe_catalog.source 에 nics_recipe 를 더한다 (#510)

농촌진흥청 「농식품 이달의 식재료」 레시피를 두 번째 운영 소스로 적재한다. CHECK 는 처음부터 받던
두 값(mafra_recipe · mfds_recipe)에 nics_recipe 를 더해 세 값을 받는다.

downgrade 는 nics_recipe 행을 지운 뒤 옛 CHECK 로 되돌린다. 이 표는 공개 API 에서 받아 채우는 공용
캐시라(`scripts/load_food_catalog`) 다시 받으면 돌아오고, 행을 남기면 옛 CHECK 를 거는 순간 실패한다.

Revision ID: e1d147d1b701
Revises: 23b12754714a
Create Date: 2026-11-02
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1d147d1b701"
down_revision: Union[str, Sequence[str], None] = "23b12754714a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 모델(`domains/food/models.py` RECIPE_SOURCE_CHECK)과 같은 글자다. 앱 코드를 import 하지 않는다
RECIPE_SOURCE_BEFORE = "source IN ('mafra_recipe', 'mfds_recipe')"
RECIPE_SOURCE = "source IN ('mafra_recipe', 'mfds_recipe', 'nics_recipe')"


def upgrade() -> None:
    op.drop_constraint("recipe_catalog_source", "recipe_catalog", type_="check")
    op.create_check_constraint("recipe_catalog_source", "recipe_catalog", RECIPE_SOURCE)


def downgrade() -> None:
    op.execute("DELETE FROM recipe_catalog WHERE source = 'nics_recipe'")
    op.drop_constraint("recipe_catalog_source", "recipe_catalog", type_="check")
    op.create_check_constraint("recipe_catalog_source", "recipe_catalog", RECIPE_SOURCE_BEFORE)
