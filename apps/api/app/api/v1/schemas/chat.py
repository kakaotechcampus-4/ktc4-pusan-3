"""지난 대화 다시 보기 응답 — #228.

대화 한 턴은 보호자 입력 하나와 그 run 의 답 전체다. 쓴 보호자 본인의 대화만 나온다.
`replies` 는 run 화면(SSE)과 같은 이름 · 같은 필드로 둔다 — 화면이 대화 블록을 다시 쓸 수 있게.
저장 방식(문장 그대로 · id 만)은 `app/api/chat_replies.py` 머리말.
"""

import datetime as dt

from pydantic import BaseModel, Field

from app.api.chat_replies import ChatReply


class ChatTurnOut(BaseModel):
    run_id: str
    raw_text: str = Field(description="보호자가 보낸 글 그대로")
    reply_to: str | None = Field(
        description="되묻기에 답한 턴이면 그 질문 턴의 run_id. 질문과 같은 날에 붙는다"
    )
    created_at: dt.datetime = Field(description="입력을 접수한 시각")
    replies: list[ChatReply] = Field(
        description=(
            "그 run 의 답을 도착 순서대로. 마지막은 늘 done 이다. "
            "진행 중에만 의미가 있는 것(step · ping · offer)은 없다"
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
