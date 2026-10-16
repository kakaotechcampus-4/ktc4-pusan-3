"""지난 대화 다시 보기 응답 — #228.

대화 한 턴은 보호자 입력 하나와 그 run 의 답 전체다. 쓴 보호자 본인의 대화만 나온다.
`replies` 는 run 화면(SSE)과 같은 이름 · 같은 필드로 둔다 — 화면이 대화 블록을 다시 쓸 수 있게.
저장 방식(문장 그대로 · id 만)은 `app/api/chat_replies.py` 머리말.

저장할 때 id 만 담은 `saved` 는 여기서 지금 상태로 채운다 — run 화면의 `saved`(#471 · `sse.ts` 의
`SavedEvent`)와 같은 `observations` 에 같은 모양으로(건강 기록 포함), 지운 기록은 내용 없이
`deleted` 에 id 만. 그 자리에서 "저장됨" 으로 본 카드가 지난 대화에서도 같다.
"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.api.chat_replies import (
    AgentResultReply,
    DoneReply,
    EventDraftReply,
    GuidanceReply,
    NoteReply,
    PartialReply,
    StoredRef,
    UnavailableReply,
)
from app.api.v1.schemas.observations import ObservationHealthOut, ObservationOut


class SavedOut(BaseModel):
    type: Literal["saved"] = "saved"
    observations: list[ObservationOut | ObservationHealthOut] = Field(
        description=(
            "그 run 에서 저장한 기록의 지금 모습, 저장한 순서대로. run 화면의 saved 와 같은 칸 · "
            "같은 모양이다 — 건강 기록은 kind 가 observation_health 인 건강 모양"
        )
    )
    deleted: list[StoredRef] = Field(
        description=(
            "그 run 에서 저장했지만 보호자가 지운 기록. 내용은 싣지 않는다 — "
            '화면은 "이 기록은 지웠어요" 로 그린다'
        )
    )


# 🚨 `chat_replies.ChatReply` 에서 `SavedReply` 만 `SavedOut` 으로 바꾼 목록이다. 저장 쪽에 종류를
#    늘리면 여기에도 넣는다 — 빠지면 조회가 그 턴을 그대로 넘기다 응답 검증에서 500 이 난다.
ChatReplyOut = Annotated[
    NoteReply
    | GuidanceReply
    | UnavailableReply
    | SavedOut
    | EventDraftReply
    | AgentResultReply
    | PartialReply
    | DoneReply,
    Field(discriminator="type"),
]


class ChatTurnOut(BaseModel):
    run_id: str
    raw_text: str = Field(description="보호자가 보낸 글 그대로")
    reply_to: str | None = Field(
        description="되묻기에 답한 턴이면 그 질문 턴의 run_id. 질문과 같은 날에 붙는다"
    )
    created_at: dt.datetime = Field(description="입력을 접수한 시각")
    replies: list[ChatReplyOut] = Field(
        description=(
            "그 run 의 답을 도착 순서대로. 마지막은 늘 done 이다. "
            "진행 단계(step)와 연결 유지 신호(ping)는 없다. "
            "일정 초안(event_draft)은 그때 만든 내용 그대로라 넣기 버튼 없이 읽기 전용으로 그린다"
        )
    )


class ChatDayResponse(BaseModel):
    date: dt.date = Field(description="이 응답의 날짜(한국 시간)")
    turns: list[ChatTurnOut] = Field(
        description=(
            "그날의 턴, 오래된 순. 최근 것부터 limit 개를 끊는다 — 대화가 없는 날이면 빈 배열이다"
        )
    )
    next_cursor: str | None = Field(
        description=(
            "그날 안에 더 이른 턴이 남았으면 그 자리. cursor 로 보내면 이어서 준다 — 날짜를 "
            "품고 있어 자정이 지나도 같은 날을 잇는다. 그날을 다 받았으면 null"
        )
    )
    prev_date: dt.date | None = Field(
        description=(
            "이 날짜보다 앞에서 대화가 있었던 가장 늦은 날. 대화가 없는 날은 건너뛴다. "
            "그날을 다 받은 뒤(next_cursor 가 null) date 로 보내 전날을 이어 그린다. 없으면 null"
        )
    )
