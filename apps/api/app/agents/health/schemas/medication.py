"""복약 tool 의 argument 모델. 모델은 요청 원문의 말을 그대로 복사한 span 만 넘긴다.

값을 고르거나 풀어 쓰는 것은 코드의 일이다 — 시점 표현은 사전(`tools/timing.py`)이 anchor 로 바꾸고,
span 이 원문의 부분 문자열인지도 코드가 본다. **대상 코스의 id 는 받지 않는다.** `title_span` 으로
코드가 진행 중 코스에서 찾는다 — 모델이 id 를 지어내면 엉뚱한 약이 바뀐다.
"""

from typing import Annotated, Literal

from pydantic import Field

from app.agents.health.schemas.common import ToolArgs

_COPY = "요청 원문에 있는 말을 그대로 복사한다. 고치거나 풀어 쓰지 않는다. 원문에 없으면 비운다."
_SPAN_MAX = 100


class ShowDoseStatusArgs(ToolArgs):
    """인자가 없다 — 체크 현황은 코드가 등록된 코스의 회차 행에서 읽는다."""


class DeferDoseQuestionArgs(ToolArgs):
    """인자가 없다 — 용량 · 간격은 판단하지 않고 처방한 곳에 확인하도록 안내만 한다."""


class CreateMedicationArgs(ToolArgs):
    title_span: Annotated[
        str | None,
        Field(default=None, max_length=_SPAN_MAX, description=f"약 이름. 예: 항생제. {_COPY}"),
    ]
    dosage_span: Annotated[
        str | None,
        Field(
            default=None, max_length=_SPAN_MAX, description=f"한 번에 먹이는 양. 예: 5ml. {_COPY}"
        ),
    ]
    timing_spans: Annotated[
        list[str],
        Field(
            default_factory=list,
            max_length=6,
            description=f"언제 먹이는지 말한 표현. 예: 하루 세 번 식후. 없으면 빈 목록. {_COPY}",
        ),
    ]
    period_span: Annotated[
        str | None,
        Field(default=None, max_length=_SPAN_MAX, description=f"며칠 먹이는지. 예: 5일. {_COPY}"),
    ]
    indication_span: Annotated[
        str | None,
        Field(
            default=None,
            max_length=_SPAN_MAX,
            description=f"약을 먹이는 이유를 말한 부분. 이유를 짐작하지 않는다. {_COPY}",
        ),
    ]


class UpdateMedicationArgs(ToolArgs):
    title_span: Annotated[
        str | None,
        Field(
            default=None,
            max_length=_SPAN_MAX,
            description=f"바꿀 약이 어느 것인지 가리키는 이름. 예: 항생제. {_COPY}",
        ),
    ]
    dosage_span: Annotated[
        str | None,
        Field(default=None, max_length=_SPAN_MAX, description=f"바뀐 양. {_COPY}"),
    ]
    timing_spans: Annotated[
        list[str] | None,
        Field(
            default=None,
            max_length=6,
            description=f"바뀐 시점 표현. 예: 저녁 시간 7시. 시점이 안 바뀌면 비운다. {_COPY}",
        ),
    ]
    period_span: Annotated[
        str | None,
        Field(default=None, max_length=_SPAN_MAX, description=f"바뀐 기간. {_COPY}"),
    ]


class StopMedicationArgs(ToolArgs):
    title_span: Annotated[
        str | None,
        Field(
            default=None,
            max_length=_SPAN_MAX,
            description=f"멈출 약 이름. 약 이름을 말하지 않았으면 비운다. {_COPY}",
        ),
    ]


class SearchMedicationArgs(ToolArgs):
    status: Annotated[
        Literal["active"],
        Field(
            default="active", description="진행 중인 복약만 본다. 끝났거나 멈춘 것은 보지 않는다."
        ),
    ]
