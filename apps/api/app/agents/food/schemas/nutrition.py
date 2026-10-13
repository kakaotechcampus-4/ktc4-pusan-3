"""영양성분 조회 · 식단 균형 비교 · 영양소 분석 보고 인자.
정확한 섭취량을 계산하지 않고 식단 기반 영양소 비중만 본다.
"""

# 메뉴별 영양 수치는 메뉴 카탈로그에만 둔다.
# 아이별 합계 · 구간은 판정할 때마다 계산하고 저장하지 않는다.
from typing import Annotated

from pydantic import Field

from app.agents.food.schemas.common import EvidenceRef, Period, ToolArgs


class LookupNutritionArgs(ToolArgs):
    foods: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=10,
            description="영양성분을 볼 음식·재료 이름. 기록 조회나 급식 메뉴 결과에 있던 이름만",
        ),
    ]


class CompareDietBalanceArgs(ToolArgs):
    """기간 식단의 영양소 비중을 연령별 기준과 비교한다. 계산은 코드가 한다."""

    period: Period


# 모델에게 보이는 findings 예문. nutrient_wording 의 문장 규칙을 통과해야 한다 — 걸리는 예문을 주면
# 모델이 그대로 따라 쓰고 거절당한다 (test_nutrient_wording.py 가 확인)
FINDING_EXAMPLE = "기록된 식사를 보면 단백질 식품이 적은 편이에요"


class NutrientReportArgs(ToolArgs):
    """영양소 분석의 출력.
    Supervisor가 식단 추천을 영양소 분석으로 잘못 보내도 식품 제안이 나갈 자리가 없다.
    """

    period: Period
    findings: Annotated[
        list[str],
        Field(
            max_length=5,
            description=(
                f"먹은 식사를 주어로 식단의 비중·경향만. 예: {FINDING_EXAMPLE}. "
                "결핍·과잉 진단, 정확한 수치, 아이 몸 상태, 표 밖의 먹일 음식 제안은 쓰지 않는다"
            ),
        ),
    ]
    data_gaps: Annotated[
        list[str],
        Field(default_factory=list, description="기록이 없어 판단하지 못한 끼니·날"),
    ]
    evidence: Annotated[
        list[EvidenceRef],
        Field(default_factory=list, description="이 분석에 쓴 기록. 조회 결과에 있던 것만"),
    ]
