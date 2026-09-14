"""영양성분 조회 · 식단 균형 비교 · 영양소 분석 보고 인자.
정확한 섭취량을 계산하지 않고 식단 기반 영양소 비중만 본다.
"""
# TODO: 영양소 정보를 내부적으로 저장하고, 출력 및 추천 시에만 비중으로 계산하는 방식에 대한 검토
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
                "식단 기반 비중·경향만. 예: 단백질 비중이 기준보다 낮은 편. "
                "결핍·과잉 진단, 정확한 수치, 먹일 음식 제안은 쓰지 않는다"
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
