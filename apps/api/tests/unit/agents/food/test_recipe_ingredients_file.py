"""커밋된 재료 정리 표(`reference/recipe_ingredients.yaml`) 자체를 검사한다.

`test_recipe_catalog.py` 는 검사 함수를 인라인 데이터로 본다. 사람이 이 파일을 고치다가 19종 밖
코드(20) · 정해지지 않은 식품군 · 이름 없는 `ok` · 겹치는 이름을 적으면, 지금은 배치가
`ingredient_table()` 을 처음 부를 때야 터진다(쓰기 전이라 안전하지만 늦다). PR 에서 먼저 잡는다.
"""

from app.agents.food.recipe_catalog import ingredient_table


def test_정리_표_파일이_표_검사를_통과한다():
    # 모양이 틀리면 parse_ingredient_table 이 ValueError 를 낸다 — 알레르기 판정 입력이다
    assert ingredient_table()
