"""삭제 제안 확정 · 취소 요청 · 응답 — #535.

대화에서 나온 삭제 카드의 버튼이 부른다. 카드에 그릴 후보 행은 SSE `delete_proposal` 과 지난 대화가
싣고, 여기서는 상태만 돌려준다.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

# 탭 수(6) × 탭당 최대 40건. 이보다 많으면 제안 후보 밖이 섞인 것이다
MAX_CONFIRM_IDS = 240

DeleteProposalState = Literal["pending", "deleted", "cancelled", "expired"]


# 행 id 는 UUID 글자(36자)다. 길이만 막는다 — 형식이 달라도 후보에 없으면 422 다
RowId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ConfirmDeleteProposalRequest(BaseModel):
    ids: list[RowId] = Field(
        min_length=1,
        max_length=MAX_CONFIRM_IDS,
        description=(
            "보호자가 체크한 행의 id. 제안 후보 안의 것만 받는다 — 하나라도 밖이면 422 이고 "
            "아무것도 지우지 않는다. 0건이면 화면이 버튼을 막는다"
        ),
    )


class DeleteProposalOut(BaseModel):
    id: UUID
    status: DeleteProposalState = Field(
        description="결정 전(pending) · 지움(deleted) · 취소(cancelled) · 만료(expired, 24시간)"
    )
    expires_at: datetime
    result: dict[str, int] | None = Field(
        description=(
            "지운 뒤 종류별 실제 지운 건수 (`{observation_food: 30, event: 1}`). 그사이 이미 "
            "지워진 행은 세지 않는다. 지우기 전 · 취소 · 만료면 null"
        )
    )


class DeleteProposalResponse(BaseModel):
    proposal: DeleteProposalOut
