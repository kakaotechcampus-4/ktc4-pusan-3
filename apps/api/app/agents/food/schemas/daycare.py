"""급식 갱신·삭제 인자.

`update_daycare_meal`·`delete_daycare_meal`은 모델에게 보이는 몇 안 되는 쓰기 tool이다.
날짜·메뉴·양은 전부 발화 원문의 부분 문자열이어야 하고, 그 검증은 코드가 한다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.food.schemas.common import ToolArgs
from app.agents.food.store.ports import DaycareSlot


class UpdateDaycareMealArgs(ToolArgs):
    """급식 갱신. 뺄 메뉴 없이 넣을 메뉴만 있으면 코드가 되묻는다(전체 교체 금지)."""

    date_span: Annotated[
        str, Field(description="날짜를 가리키는 원문 구간. 예: 내일모레, 지난 화요일")
    ]
    slot: Annotated[
        DaycareSlot | None,
        Field(default=None, description="lunch / snack_am / snack_pm 중 하나. 모르면 비운다"),
    ]
    removed_spans: Annotated[
        list[str],
        Field(default_factory=list, description="뺄 메뉴를 가리키는 원문 구간들"),
    ]
    added_spans: Annotated[
        list[str],
        Field(default_factory=list, description="넣을 메뉴를 가리키는 원문 구간들"),
    ]
    amount_span: Annotated[
        str | None,
        Field(default=None, description="양을 가리키는 원문 구간. 예: 엄청 많이, 반만"),
    ]


class DeleteDaycareMealArgs(ToolArgs):
    """급식 삭제(결석 처리). slot을 비우면 그날 행 전부를 지운다."""

    date_span: Annotated[str, Field(description="날짜를 가리키는 원문 구간")]
    slot: Annotated[
        DaycareSlot | None,
        Field(
            default=None,
            description="lunch / snack_am / snack_pm 중 하나. 비우면 그날 전부 삭제",
        ),
    ]
