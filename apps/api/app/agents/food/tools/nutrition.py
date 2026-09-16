"""영양 tool 3개(영양성분 조회 / 식단 균형 비교 . 영양소 분석 보고)

정확한 섭취량을 계산하지 않고 식단에 담긴 영양소의 비중만 고려.
"""

from app.agents.food.context import FoodContext
from app.agents.food.result import ToolResult
from app.agents.food.schemas.nutrition import (
    CompareDietBalanceArgs,
    LookupNutritionArgs,
    NutrientReportArgs,
)


async def lookup_nutrition(context: FoodContext, args: LookupNutritionArgs) -> ToolResult:
    """음식이나 식재료의 영양성분을 조회한다.

    외부 API 연결 후:
    - context.nutrition.facts(food_names)로 식약처 식품영양성분 DB를 조회한다.
    - 조회되지 않은 음식은 별도로 반환하고, 비슷한 음식의 값으로 대체하지 않는다.
    - 영양성분 수치는 조회된 원본 값을 그대로 사용한다.
      모델이 값을 추정하거나 보정하지 않는다.
    """
    raise NotImplementedError("외부 API 연결 후 구현")


async def compare_diet_balance(context: FoodContext, args: CompareDietBalanceArgs) -> ToolResult:
    """기간 내 식단의 영양소 비중을 연령별 기준과 비교한다.

    DB와 외부 API 연결 후:
    - 급식과 가정 식사 기록을 함께 조회한다.
      식단 조회 방식은 analyze_meal_records와 동일하게 맞춘다.
    - 메뉴별 영양성분을 기준으로 전체 영양소 비중을 계산한다.
      먹은 양을 임의로 적용해 실제 섭취량으로 계산하지 않는다.
    - birth_date로 개월 수를 계산하고 연령에 맞는 기준 비율을 가져온다.
    - 기준과 비교해 각 영양소를 높음, 비슷, 낮음으로 구분한다.
      판정 기준은 상수로 관리하고 키와 몸무게는 사용하지 않는다.
    - 누락된 식사 기록이 많으면 비교 결과 대신 data_gaps를 반환한다.
    """
    raise NotImplementedError("DB·외부 API 연결 후 구현")


async def report_nutrient_analysis(context: FoodContext, args: NutrientReportArgs) -> ToolResult:
    """영양소 분석 결과를 최종 검증하고 반환한다.

    DB 연결 후:
    - findings에 음식 추천, 근거 없는 정확한 수치, 진단 표현이 포함됐는지 확인한다.
      허용하지 않는 내용이 있으면 VALIDATION_ERROR를 반환한다.
    - evidence가 이번 실행에서 실제로 조회한 ref인지 확인하고,
      확인되지 않은 ref는 결과에서 제외한다.
    - 저장할 테이블이 정해지기 전까지는 분석 결과만 반환한다.
    - 결핍 진단, 치료식, 영양제 관련 요청은 이 단계에서 처리하지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")
