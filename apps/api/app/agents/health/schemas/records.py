"""진료 준비 tool 의 argument 모델.

조회 tool 은 기간(과 증상 하나)만 받는다. child_id · 날짜 구간은 받지 않는다 — 구간은 코드가
보호자 시간대의 오늘에서 센다. 모델이 날짜를 지어내거나 남의 아이를 가리킬 길이 없다.

출력 tool(`compose_symptom_timeline`)은 줄마다 근거 id(`ref`)를 받는다. 알레르기 · 만성질환 줄은
받지 않는다 — 구획 자체가 없고, 코드가 `health_safety` 원문으로 붙인다.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from app.agents.health.schemas.common import ToolArgs


class SearchHealthArgs(ToolArgs):
    period: Annotated[
        Literal["3d", "7d", "14d", "30d"],
        Field(description="오늘까지 거슬러 볼 기간. 보호자가 기간을 말하지 않았으면 14d."),
    ] = "14d"
    symptom: Annotated[
        str | None,
        Field(
            default=None,
            max_length=100,
            description=(
                "특정 증상만 보고 싶을 때 요청 원문에 있는 증상 말을 그대로 복사한다. "
                "증상을 말하지 않았으면 비운다."
            ),
        ),
    ]


class SearchMealsArgs(ToolArgs):
    period: Annotated[Literal["3d"], Field(description="최근 식사는 3일만 본다.")] = "3d"


class SearchMedicationChecksArgs(ToolArgs):
    period: Annotated[
        Literal["3d", "7d", "14d"],
        Field(description="오늘까지 거슬러 볼 기간. 보호자가 기간을 말하지 않았으면 14d."),
    ] = "14d"


class TimelineLine(ToolArgs):
    date: Annotated[date, Field(description="그 줄이 가리키는 날짜. 조회 결과에 있는 날짜만.")]
    section: Annotated[
        Literal["symptom", "meal", "medication", "given"],
        Field(
            description=(
                "symptom=증상 · meal=식사 · medication=등록된 약의 체크 · "
                "given=보호자가 먹였다고 기록한 약"
            )
        ),
    ]
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description=(
                "조회 결과에 있는 말을 옮긴 한 줄. 약 이름 · 용량은 조회 결과 그대로 쓴다. "
                "원인 · 병명을 짐작하지 않고, 체크 안 된 회차는 '체크 안 됨' 이지 "
                "'안 먹였다' 가 아니다."
            ),
        ),
    ]
    ref: Annotated[str, Field(description="그 줄의 근거가 된 조회 결과의 ref 를 그대로 복사한다.")]


class ComposeTimelineArgs(ToolArgs):
    lines: Annotated[
        list[TimelineLine],
        Field(max_length=60, description="날짜순 타임라인 줄. 근거(ref) 없는 줄은 지워진다."),
    ]
