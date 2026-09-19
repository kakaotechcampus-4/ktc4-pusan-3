"""모델에게 보이는 Food tool 9개의 이름 · 호출 조건 · argument 모델).

알레르기 Safety Tool(filter_food_safety)은 코드가 부름.
영아기 기록 tool 2개(record_feeding · record_ingredient_introduction)는 쓰기 tool 이라
Memory가 관리.
"""

from app.agents.common.tool_schema import ToolDefinition
from app.agents.food.schemas.infant import GuideWeaningStageArgs
from app.agents.food.schemas.menu import DaycareMenuArgs
from app.agents.food.schemas.nutrition import (
    CompareDietBalanceArgs,
    LookupNutritionArgs,
    NutrientReportArgs,
)
from app.agents.food.schemas.recommend import ProposeMealCandidatesArgs
from app.agents.food.schemas.records import (
    AnalyzeMealRecordsArgs,
    CheckRepeatedMenusArgs,
    SearchFoodMemoryArgs,
)

TOOL_DEFINITIONS: list[ToolDefinition] = [
    # 공통 — Food Memory Search
    ToolDefinition(
        name="search_food_memory",
        description=(
            "아이가 먹은 기록과 음식 선호·기피를 찾는다. "
            "추천이나 분석의 근거를 모을 때 먼저 부른다. "
            "결과의 kind·id 만 evidence 로 쓸 수 있다."
        ),
        args=SearchFoodMemoryArgs,
    ),
    # 유아기 — 실제 섭취 기록 분석 · Meal History Summary
    ToolDefinition(
        name="analyze_meal_records",
        description=(
            "기간 동안 실제로 먹은 식사를 기관 급식과 가정 식사를 합쳐 요약한다. "
            "최근 무엇을 얼마나 먹었는지 봐야 할 때 부른다."
        ),
        args=AnalyzeMealRecordsArgs,
    ),
    # 유아기 — 반복 메뉴 확인
    ToolDefinition(
        name="check_repeated_menus",
        description=(
            "최근 자주 반복된 메뉴와 식단 편중을 확인한다. "
            "반복 판정은 코드가 하므로 결과를 그대로 쓴다."
        ),
        args=CheckRepeatedMenusArgs,
    ),
    # 유아기 — 기관 급식 조회 · Daycare / Kindergarten Menu Lookup
    ToolDefinition(
        name="lookup_daycare_menu",
        description=(
            "어린이집·유치원 급식 메뉴를 날짜로 찾는다. 오늘 급식과 겹치지 않게 하거나 "
            "급식까지 포함해 분석할 때 부른다. 아이에게 위험한 식품 표시가 함께 온다."
        ),
        args=DaycareMenuArgs,
    ),
    # 유아기 — 식품 영양성분 조회 · Nutrition Lookup
    ToolDefinition(
        name="lookup_nutrition",
        description=(
            "음식·재료의 영양성분을 식품영양성분 DB 에서 찾는다. 영양소 분석에서만 쓴다. "
            "수치를 지어내지 않고 이 결과만 쓴다."
        ),
        args=LookupNutritionArgs,
    ),
    # 유아기 — 식단 균형 비교 · Nutrition Compare
    ToolDefinition(
        name="compare_diet_balance",
        description=(
            "기간 식단의 영양소 비중을 연령별 기준과 비교한다. 비교 계산은 코드가 한다. "
            "영양소 분석을 보고하기 전에 부른다."
        ),
        args=CompareDietBalanceArgs,
    ),
    # 영아기 — 이유식 단계 보조
    ToolDefinition(
        name="guide_weaning_stage",
        description=(
            "아이의 개월 수에 맞는 이유식 단계와 참고 기준을 가져온다. "
            "이유식 메뉴나 새로 먹여 볼 재료를 추천하기 전에 부른다."
        ),
        args=GuideWeaningStageArgs,
    ),
    # 식단 추천의 출력 — 식사·간식 후보 추천 (suggestion form)
    ToolDefinition(
        name="propose_meal_candidates",
        description=(
            "식사·간식 후보를 제출한다. 식단 추천의 마지막에 한 번 부른다. "
            "재료는 빠짐없이 적는다 — 알레르기 필터는 코드가 이 재료 목록으로 한다."
        ),
        args=ProposeMealCandidatesArgs,
    ),
    # 영양소 분석의 출력 — 과잉/부족 영양소 분석
    ToolDefinition(
        name="report_nutrient_analysis",
        description=(
            "영양소 분석 결과를 보고한다. 영양소 분석의 마지막에 한 번 부른다. "
            "식단 기반 비중·경향만 쓰고, 진단·정확한 섭취량·먹일 음식은 쓰지 않는다."
        ),
        args=NutrientReportArgs,
    ),
]
