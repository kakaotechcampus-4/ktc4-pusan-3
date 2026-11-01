"""레시피 소스 — `recipe_catalog` 행이 어느 공개 데이터에서 왔는가 (#510).

BE 조회 계약(`domains/food`)과 AI 포트(`agents/food/store/ports`)가 같이 쓰므로 양쪽이 허용하는
`core` 에 둔다.

DB 의 `recipe_catalog_source` CHECK 보다 좁다 — 식약처(`mfds_recipe`)는 DB 만 받는다. 이용 조건을
확인하기 전이라 운영에서 받지 않는 소스고, 확인한 뒤에 이 값에 더한다.
"""

from typing import Literal

RecipeSource = Literal["mafra_recipe", "nics_recipe"]
