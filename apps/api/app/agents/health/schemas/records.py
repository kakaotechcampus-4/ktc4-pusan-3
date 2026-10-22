"""진료 준비 tool 의 argument 모델.

조회 tool 은 기간(과 증상 하나)만 받는다. child_id · 날짜 구간은 받지 않는다 — 구간은 코드가
보호자 시간대의 오늘에서 센다. 모델이 날짜를 지어내거나 남의 아이를 가리킬 길이 없다.

출력 tool(`compose_symptom_timeline`)은 조회 결과의 key 만 받는다. 줄의 글은 코드가 기록에서
그린다 — 모델은 문장을 쓰지 않는다 (#551). 알레르기 · 만성질환 줄도 코드가 `health_safety` 원문으로
붙인다.
"""

from typing import Annotated, Literal

from pydantic import Field

from app.agents.health.schemas.common import ToolArgs

# 출력 tool 이 받는 key 수의 상한. 30일 조회의 증상 · 먹인 약 · 복약 체크 · 3일 식사를 다 담는다.
# key 는 "s12" 꼴이라 상한까지 보내도 출력 토큰 상한(`labels/visit.py`)에 걸리지 않는다
MAX_KEYS = 200


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
        Literal["3d", "7d", "14d", "30d"],
        Field(description="오늘까지 거슬러 볼 기간. 보호자가 기간을 말하지 않았으면 14d."),
    ] = "14d"


class ComposeTimelineArgs(ToolArgs):
    keys: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=MAX_KEYS,
            description=(
                "정리에 넣을 줄의 key. 조회 결과의 key 를 그대로 복사한다. 순서는 상관없다 — "
                "날짜순 정렬은 코드가 한다. 조회 결과에 없는 key 는 지워진다."
            ),
        ),
    ]
