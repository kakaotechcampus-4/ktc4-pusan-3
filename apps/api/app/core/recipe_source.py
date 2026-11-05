"""레시피 소스 — `recipe_catalog` 행이 어느 공개 데이터에서 왔는가 (#510).

BE 조회 계약(`domains/food`) · 05(`api/v1/routers/suggestions`)와 AI 포트(`agents/food`)가 같이
쓰므로 양쪽이 허용하는 `core` 에 둔다.

DB 의 `recipe_catalog_source` CHECK 보다 좁다 — 식약처(`mfds_recipe`)는 DB 만 받는다. 이용 조건을
확인하기 전이라 운영에서 받지 않는 소스고, 확인한 뒤에 이 값과 `RECIPE_SOURCE_INFO` 에 더한다.
"""

from dataclasses import dataclass
from typing import Literal

RecipeSource = Literal["mafra_recipe", "nics_recipe"]


@dataclass(frozen=True)
class RecipeSourceInfo:
    """소스를 사람에게 보일 때 쓰는 값. 05 근거 카드(기관 · 제목 · 주소)와 Agent 가 메뉴 근거에
    붙이는 한 줄(`note`)이 이 표 하나를 읽는다 — 두 곳에 따로 두면 소스를 더할 때 한쪽을
    빠뜨린다."""

    org: str
    title: str
    url: str
    note: str  # 코드 문구다(모델이 쓰지 않는다). 05 화면에 그대로 나가서 사실만 쓴다


RECIPE_SOURCE_INFO: dict[RecipeSource, RecipeSourceInfo] = {
    "mafra_recipe": RecipeSourceInfo(
        org="농식품정보원",
        title="농림축산식품 공공데이터포털 레시피 기본 · 재료정보",
        url="https://data.mafra.go.kr",
        note="농식품정보원 공개 레시피 중 재료가 검수된 메뉴예요.",
    ),
    "nics_recipe": RecipeSourceInfo(
        org="농촌진흥청 국립식량과학원",
        title="농식품 이달의 식재료 목록정보",
        url="https://www.data.go.kr/data/15168024/openapi.do",
        note="농촌진흥청 공개 레시피 중 재료가 검수된 메뉴예요.",
    ),
}
