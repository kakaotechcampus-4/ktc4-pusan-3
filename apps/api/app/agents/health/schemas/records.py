"""진료 준비 tool 의 argument 모델.

모델이 정하는 것은 증상 · 먹인 약을 거슬러 볼 기간과 증상 하나뿐이다 (#551 리뷰). child_id · 날짜
구간은 받지 않는다 — 구간은 코드가 보호자 시간대의 오늘에서 센다. 모델이 날짜를 지어내거나
남의 아이를 가리킬 길이 없다.

출력 tool(`compose_symptom_timeline`)은 인자가 없다. 조회한 증상 · 먹인 약 줄과 코드가 읽는 식사 ·
복약 체크 줄을 전부 묶는다 — 모델이 줄을 고르지 않는다. 알레르기 · 만성질환 줄도 코드가
`health_safety` 원문으로 붙인다.
"""

from typing import Annotated, Literal

from pydantic import Field

from app.agents.health.schemas.common import ToolArgs

Period = Literal["3d", "7d", "14d", "30d"]


class SearchHealthArgs(ToolArgs):
    period: Annotated[
        Period,
        Field(
            description=(
                "오늘까지 거슬러 볼 기간. 보호자가 기간을 말하지 않았으면 14d. "
                "복약 체크도 이 기간으로 정리된다."
            )
        ),
    ] = "14d"
    symptom: Annotated[
        str | None,
        Field(
            default=None,
            max_length=100,
            description=(
                "보호자가 특정 증상만 정리해 달라고 했을 때 요청 원문에 있는 증상 말을 그대로 "
                "복사한다. 증상을 좁히지 않았으면 비운다."
            ),
        ),
    ]


class ComposeTimelineArgs(ToolArgs):
    """인자가 없다 — 정리에 들어갈 줄은 코드가 정한다."""
