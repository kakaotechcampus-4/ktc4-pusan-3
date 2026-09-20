"""기록 기반 tool 3개(식단 기억 검색 / 섭취 기록 분석 / 반복 메뉴 확인)

지금은 구현부가 주석이라 호출시 NotImplementedError.
"""

from app.agents.food.context import FoodContext
from app.agents.food.result import ToolResult
from app.agents.food.schemas.records import (
    AnalyzeMealRecordsArgs,
    CheckRepeatedMenusArgs,
    SearchFoodMemoryArgs,
)


async def search_food_memory(context: FoodContext, args: SearchFoodMemoryArgs) -> ToolResult:
    """아이의 식사 기록과 음식 선호·기피 정보를 조회한다.

    DB 연결 후:
    - args.period를 날짜 범위로 변환한다. 값이 없으면 기간 제한 없이 조회한다.
    - observation_food와 food 도메인의 profile_affinity를 함께 조회한다.
    - 결과에는 kind, id, subject, 날짜, polarity만 포함하고 raw_text는 제외한다.
    - 마지막 관찰이 6개월 이상 지난 선호 정보는 is_stale=True로 표시한다.
      오래된 선호만으로 개인화 추천 근거를 만들지 않는다.
    - 조회 결과가 없으면 NO_RECORDS를 반환한다.
    """
    raise NotImplementedError("DB 연결 후 구현")


async def analyze_meal_records(context: FoodContext, args: AnalyzeMealRecordsArgs) -> ToolResult:
    """기간 내 급식과 가정 식사 기록을 묶어 요약한다.

    DB 연결 후:
    - args.period를 날짜 범위로 변환한다.
    - 가정 식사 기록과 기관 급식 메뉴를 날짜와 끼니 기준으로 합친다.
    - 각 끼니에서 먹은 음식과 남긴 음식만 정리한다.
      기록만으로 실제 섭취량을 임의 계산하지 않는다.
    - 기록이 없는 날짜나 끼니는 별도로 표시하고 값을 추정해 채우지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")


async def check_repeated_menus(context: FoodContext, args: CheckRepeatedMenusArgs) -> ToolResult:
    """기간 내 반복되는 메뉴나 식단 편중을 확인한다.

    DB 연결 후:
    - 급식과 가정 식사 기록을 같은 기준으로 조회한다.
    - subject별 등장 횟수를 집계해 반복 여부를 판단한다.
      판정 기준은 코드의 상수로 관리한다.
    - 반복된 메뉴, 횟수, 마지막 기록 날짜를 반환한다.
    """
    raise NotImplementedError("DB 연결 후 구현")
