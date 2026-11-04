"""조회 · 삭제 제안 카드의 탭 모양 — #535.

run 화면(SSE `found_tabs` · `delete_proposal`)과 지난 대화가 같은 모양을 쓴다. 탭 하나 = 조회
tool 하나다. 관찰은 `records`, 일정은 `events` 에 싣고, 다른 쪽은 빈 배열이다 — 화면이 탭마다
칸 유무를 따로 보지 않게 두 칸을 늘 둔다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.v1.schemas.observations import ObservationHealthOut, ObservationOut
from app.api.v1.schemas.suggestions import CalendarEventOut

DeleteProposalState = Literal["pending", "deleted", "cancelled", "expired"]


class CardTabOut(BaseModel):
    kind: str = Field(description="`observation_food` · … · `event`. 탭 순서는 서버가 정한 그대로")
    truncated: bool = Field(
        description="최근 40건만 실었는지. 참이면 화면이 더 있다고 알린다 (건수는 싣지 않는다)"
    )
    records: list[ObservationOut | ObservationHealthOut] = Field(
        description="관찰 탭의 기록, 조회 순서(오래된 순). 지워졌거나 못 읽은 기록은 빠진다"
    )
    events: list[CalendarEventOut] = Field(description="일정 탭의 일정. 관찰 탭이면 빈 배열")


class ProposalTabOut(CardTabOut):
    picked_ids: list[str] = Field(
        description="AI 가 요청에 맞아 고른 id — 기본 체크. 탭 안 행을 두 묶음으로 나누는 기준이다"
    )


class FoundTabsOut(BaseModel):
    """조회 답변 · 수정 후보 카드. 보기 전용이다."""

    type: Literal["found_tabs"] = "found_tabs"
    tabs: list[CardTabOut]


class DeleteProposalCardOut(BaseModel):
    """삭제 제안 카드. 버튼은 `POST …/delete-proposals/{proposal_id}/confirm · cancel`."""

    type: Literal["delete_proposal"] = "delete_proposal"
    proposal_id: str
    status: DeleteProposalState = Field(
        description=(
            "지금 상태. pending 이면 카드와 버튼을 그대로, 나머지는 한 줄 요약으로 그린다. "
            "24시간이 지난 pending 은 expired 로 온다"
        )
    )
    expires_at: datetime = Field(
        description=(
            "이 시각이 지나면 확정 · 취소할 수 없다(만든 뒤 24시간). run 화면의 카드는 늘 "
            "pending 으로 오므로, 띄워 둔 채 이 시각이 지나면 화면이 직접 닫힌 카드로 그린다 — "
            "그래도 누르면 409 proposal_expired 다"
        )
    )
    result: dict[str, int] | None = Field(
        description="지운 뒤 종류별 실제 지운 건수. 한 줄 요약이 읽는다. deleted 가 아니면 null"
    )
    tabs: list[ProposalTabOut] = Field(
        description=(
            "후보 행. pending 일 때만 싣고 끝난 제안은 빈 배열이다 — 끝난 카드는 한 줄 요약만 "
            "그린다. 그사이 지워진 후보는 빠지고, 행이 하나도 없는 탭도 빠진다"
        )
    )
